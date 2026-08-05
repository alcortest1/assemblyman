#!/usr/bin/env python3
"""The deterministic half of the criteria pipeline: what a subtask is, and what backs it.

Nothing here calls a model or touches the network, so the same subtask list, the
same manual citations and the same rendered template come out of every run.
`generate_criteria.py` asks a model for rubric wording and nothing else;
`criteria_lint.py` re-derives these same structures to check what came back
against the sources it was actually shown.

Splitting it this way is what makes the validator worth running. A linter that
trusted the generator's own record of which pages it cited could not catch a
criterion resting on a page that was never in the prompt — the failure that
matters most here, because a fabricated citation reads exactly like a real one.
"""

from __future__ import annotations

import csv
import json
import math
import re
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TASK_DIR = ROOT / "tasks"
TASKS_CSV = ROOT / "data" / "processed" / "tasks.csv"
VIDEO_DIR = ROOT / "data" / "videos"
OUT_DIR = ROOT / "criteria" / "generated_criteria"
VIDEO_SUFFIXES = {".mp4", ".mov", ".m4v"}

# Hand-authored steps for operations the AIM sheet omits. Kept out of steps.json
# because packs/ingest.py rewrites that file from the .docx on every run.
SUPPLEMENT = "steps_supplement.json"

# Prerequisites and boilerplate, not gradeable work. `Safety & Equipment` is
# AM.II.K.S3's spelling of the same heading.
NON_PROCEDURE_SECTIONS = {"before you begin", "safety and equipment", "safety & equipment"}

# A sheet whose only heading is `Procedure` names no operation, so the subtask
# takes its name from the document variant instead — which is what distinguishes
# AM.I.E.S1's three safety-wire methods from each other.
GENERIC_SECTIONS = {"procedure", "procedures", "steps"}

ID_STOPWORDS = {"a", "an", "the", "of", "on", "in", "into", "for", "and", "to",
                "with", "using", "from", "at", "by", "out", "up"}

# Dropped from the head of a subtask id, so AM.I.E.S1's "Perform the Wire Safety
# on Bolts (By Hand)" keys on the operation rather than on `perform`.
LEAD_VERBS = {"perform", "complete", "conduct"}

# Misspellings in the source sheets. The id is normalized and the title is left
# verbatim, so the output still matches the document a student is holding.
TYPO_FIXES = {"knnot": "knot"}

OVERALL_RULE = ("PASS requires every required criterion to pass and no critical "
                "defect.")
UNVERIFIED_RULE = "FAIL — not demonstrated in image."


def read_json(path: Path):
    return json.loads(path.read_text()) if path.exists() else None


def read_text(path: Path) -> str:
    return path.read_text() if path.exists() else ""


def task_rows() -> list[dict]:
    with TASKS_CSV.open() as handle:
        return list(csv.DictReader(handle))


# ----------------------------------------------------------------- subtasks


def strip_code_prefix(title: str) -> str:
    """Drop the leading `AM.I.D.S1–` from a document or variant title."""
    return re.sub(r"^\s*AM\.[IVX]+\.[A-Z]\.S\d+\s*[–—-]?\s*", "", title or "").strip()


def snake(text: str) -> str:
    words = [TYPO_FIXES.get(w, w) for w in re.findall(r"[A-Za-z0-9]+", (text or "").lower())]
    if words and words[0] in LEAD_VERBS:
        words.pop(0)
        if words and words[0] in {"the", "a", "an"}:
            words.pop(0)
    return "_".join(words) or "procedure"


def looks_like_heading(name: str) -> bool:
    """Whether a step-less heading opens a subtask or continues the one above it.

    AM.II.A.S6 has both: `Create the Patch Doubler` carries no steps of its own,
    and the block after it — `Flush patches need a doubler to attach to under the
    skin` — is a sentence the converter promoted to a heading, holding the three
    notes that describe what the doubler must be. Treating the sentence as its
    own subtask would name a subtask after a sentence; treating the real heading
    as skippable would lose the doubler entirely.
    """
    words = re.findall(r"[A-Za-z][A-Za-z'-]*", name or "")
    if not words or len(words) > 7:
        return False
    significant = [w for w in words if w.lower() not in ID_STOPWORDS]
    if not significant:
        return False
    capitalized = sum(1 for w in significant if w[:1].isupper())
    return capitalized >= max(1, (len(significant) + 1) // 2)


def _section_notes(section: dict) -> list[str]:
    return [(n.get("text") or "").strip() for n in section.get("notes") or []
            if (n.get("text") or "").strip()]


def _section_steps(section: dict, notes: list[str]) -> list[dict]:
    steps = []
    for step in section.get("steps") or []:
        refs = [r for r in (step.get("note_refs") or []) if isinstance(r, int)]
        steps.append({
            "text": (step.get("text_clean") or step.get("text") or "").strip(),
            "notes": [notes[r - 1] for r in refs if 1 <= r <= len(notes)],
        })
    return [s for s in steps if s["text"]]


def subtasks_for(acs: str) -> list[dict]:
    """Ordered subtasks for a task code, in the order the procedure teaches them.

    A subtask is a procedural heading that describes a distinct operation. Where
    a sheet's only heading is `Procedure` the variant title supplies the name,
    and where several variants exist each becomes its own subtask — three ways of
    safetying are three different articles to photograph, not one.

    Sections drafted in `steps_supplement.json` for operations the sheet omits
    come last and are marked `origin: drafted`. They need subtasks of their own or
    they would have no rubric, and a step with no rubric is silently ungraded.
    """
    data = read_json(TASK_DIR / acs / "steps.json") or {}
    variants = data.get("variants") or []
    supplement = read_json(TASK_DIR / acs / SUPPLEMENT) or {}
    out: list[dict] = []

    groups = [(strip_code_prefix(v.get("variant") or v.get("title") or ""),
               v.get("sections") or [], "sheet") for v in variants]
    if supplement.get("sections"):
        groups.append(("", supplement["sections"], "drafted"))

    for variant_title, sections, origin in groups:
        current: dict | None = None

        for section in sections:
            name = (section.get("section") or "").strip()
            if name.lower() in NON_PROCEDURE_SECTIONS:
                continue
            notes = _section_notes(section)
            steps = _section_steps(section, notes)
            if not steps and not notes and not looks_like_heading(name):
                continue

            if not steps and not looks_like_heading(name):
                # A note-only sentence: it belongs to the heading above it.
                if current is not None:
                    current["notes"].extend(notes)
                    current["continuations"].append(name)
                    continue

            title = name
            if name.lower() in GENERIC_SECTIONS or not name:
                title = variant_title if len(variants) > 1 else (name or "Procedure")

            current = {
                "index": len(out) + 1,
                "id": snake(title),
                "title": title,
                "section": name or "Procedure",
                "variant": variant_title,
                "origin": origin,
                "steps": steps,
                "notes": list(notes),
                "continuations": [],
            }
            out.append(current)

    for position, subtask in enumerate(out, start=1):
        subtask["index"] = position
        subtask["position"] = f"{position} of {len(out)}"
        subtask["previous"] = out[position - 2]["title"] if position > 1 else None
        subtask["next"] = out[position]["title"] if position < len(out) else None
    return out


# ------------------------------------------------- preserving existing ids


_NUMBERED_CLIP = re.compile(r"^[a-z]+(?:_[a-z]+)*_\d+(?:_[a-z]+)*$")
_CLIP_SKIP = {"the", "and", "for", "with", "from", "into", "onto", "line"}


def _clip_words(text: str) -> list[str]:
    return [w for w in re.findall(r"[a-z]{3,}", (text or "").lower()) if w not in _CLIP_SKIP]


def _related(a: str, b: str) -> bool:
    return len(a) >= 3 and len(b) >= 3 and (a.startswith(b[:4]) or b.startswith(a[:4]))


def preserved_ids(acs: str, subtasks: list[dict]) -> dict[int, str]:
    """Subtask ids already established by this task's reference clip names.

    AM.I.D.S1's seven clips are named for the operations they show —
    `route_the_line`, `bend_the_line`, `flare_the_line` — and those names are
    already the subtask vocabulary everywhere else in the pilot, so a derived id
    would fork it. Every other task numbers its clips (`flush_patch_1..8`), which
    carries no name to preserve.

    All-or-nothing on purpose. A half-adopted set would leave a task with some
    ids from the clips and some derived from headings, and no way to tell from an
    id which convention produced it.
    """
    directory = VIDEO_DIR / acs
    if not directory.is_dir() or not subtasks:
        return {}
    clips = sorted({path.stem for path in directory.iterdir()
                    if path.suffix.lower() in VIDEO_SUFFIXES
                    and not _NUMBERED_CLIP.match(path.stem)})
    if len(clips) < len(subtasks):
        return {}

    scored = []
    for subtask in subtasks:
        words = _clip_words(subtask["title"])
        for clip in clips:
            targets = _clip_words(clip)
            score = sum(1 for w in words if any(_related(w, t) for t in targets))
            if score:
                scored.append((score, subtask["index"], clip))
    # Sorted before the greedy pass so a tie resolves identically on every run.
    scored.sort(key=lambda row: (-row[0], row[1], row[2]))

    assignment: dict[int, str] = {}
    used: set[str] = set()
    for _, index, clip in scored:
        if index in assignment or clip in used:
            continue
        assignment[index] = clip
        used.add(clip)
    return assignment if len(assignment) == len(subtasks) else {}


# ------------------------------------------------------------- manual match


_FENCE = re.compile(r"^```.*$", re.M)
_BLOCK = re.compile(r"^##\s+(.+?)\s*$\n(.*?)(?=^##\s|\Z)", re.M | re.S)
_PAGE_HEAD = re.compile(r"^(\S+)\s*\(PDF page index (\d+)\)$")
_ANCHOR_HEAD = re.compile(r"^(.*?)\s*\{#([\w.-]+)\}$")


def parse_blocks(text: str) -> list[dict]:
    """Split a reference extract into citable blocks — one per printed page or anchor."""
    blocks = []
    for match in _BLOCK.finditer(text):
        head, body = match.group(1).strip(), match.group(2)
        page, anchor = _PAGE_HEAD.match(head), _ANCHOR_HEAD.match(head)
        if page:
            label, kind = page.group(1), "page"
        elif anchor:
            label, kind = anchor.group(2), "section"
        else:
            label, kind = head, "section"
        body = _FENCE.sub("", body)
        body = re.sub(r"^>\s?", "", body, flags=re.M)
        body = re.sub(r"\n{3,}", "\n\n", body).strip()
        if body:
            blocks.append({"label": label, "kind": kind, "text": body})
    if not blocks:
        stripped = _FENCE.sub("", text).strip()
        if stripped:
            blocks.append({"label": None, "kind": "extract", "text": stripped})
    return blocks


def reference_files(acs: str) -> list[dict]:
    """This task's manual extracts, with the provenance their sidecars record.

    `link_handbook.py` already decided which handbook governs each task and
    whether the campus itself cited it. Re-searching here would replace a
    recorded judgement with a fresh search hit, so the linkage is read, not
    redone. `cited_by_source` is the field that matters: a range the sheet names
    in its "You Need to" section carries the campus's authority, and one located
    by content search does not.
    """
    directory = TASK_DIR / acs / "references" / "handbook"
    out = []
    for markdown in sorted(directory.glob("*.md")) if directory.is_dir() else []:
        meta = read_json(markdown.with_suffix(".json")) or {}
        text = markdown.read_text()
        handbook = meta.get("handbook")
        if not handbook:
            title = re.search(r"^#\s+(.+)$", text, re.M)
            handbook = title.group(1).split("—")[0].strip() if title else markdown.stem
        labels = meta.get("labels") or []
        chapter = None
        if labels and re.match(r"^\d+-", str(labels[0])):
            chapter = str(labels[0]).split("-")[0]
        out.append({
            "file": str(markdown.relative_to(ROOT)),
            "handbook": handbook,
            "chapter": chapter,
            "labels": labels,
            "cited_by_source": bool(meta.get("cited_by_source")),
            "located_by": meta.get("located_by") or "hand-trimmed extract, no sidecar",
            "sidecar": bool(meta),
            "blocks": parse_blocks(text),
        })
    return out


_CONTENT_STOP = {
    "that", "this", "with", "from", "have", "been", "will", "must", "should",
    "when", "then", "than", "into", "onto", "over", "under", "each", "they",
    "them", "these", "those", "there", "which", "while", "would", "could",
    "also", "only", "such", "used", "using", "make", "made", "does", "your",
    "figure", "shown", "note", "step", "steps", "page",
}


def content_tokens(text: str) -> list[str]:
    return [w for w in re.findall(r"[a-z]{4,}", (text or "").lower()) if w not in _CONTENT_STOP]


def subtask_text(subtask: dict) -> str:
    """Everything the procedure document says about this subtask."""
    parts = [subtask["title"], *subtask.get("continuations", [])]
    for step in subtask["steps"]:
        parts.append(step["text"])
        parts.extend(step["notes"])
    parts.extend(subtask["notes"])
    return "\n".join(p for p in parts if p)


# ------------------------------------------------------------ steps & atoms
#
# A subtask rubric grades the finished subtask; a step rubric grades one step of
# it against the frame where that step ends. The atoms are already compiled —
# `pack.yaml` checks are correctness atoms and its error modes are defect atoms —
# so nothing here invents them. It reads them, decides which a photograph could
# settle, and works out which frame to put in front of a grader.

FRAME_DIR = ROOT / "build" / "frames"
PACK_DIR = TASK_DIR


def load_pack(acs: str) -> dict:
    path = TASK_DIR / acs / "pack.yaml"
    if not path.exists():
        return {}
    try:
        import yaml
        return yaml.safe_load(path.read_text()) or {}
    except Exception:
        return {}


def group_key(step: dict) -> str:
    """What a pack step belongs to: its section, or its variant where it has none.

    AM.I.E.S1 was hand-compiled before sections existed, so all thirteen of its
    steps carry `section: null` and are told apart only by `variant`
    (`bolts_hand`, `bolts_pliers`, `turnbuckle_hand`). Keying on section alone
    would collapse three different articles into one group and lay all thirteen
    steps along a single clip.
    """
    return (step.get("section") or step.get("variant") or "Procedure").strip()


def pack_steps(acs: str, subtasks: list[dict]) -> list[dict]:
    """Ordered pack steps, each joined to the subtask rubric that covers it.

    The join is by section name where the pack has one, and by token overlap
    against the subtask title where it does not — which is what places
    `bolts_pliers` on `wire_safety_on_bolts_with_safety_wire_pliers` rather than
    on the by-hand sheet it shares the word "bolts" with.
    """
    pack = load_pack(acs)
    steps = pack.get("steps") or []
    if not steps:
        return []

    groups = list(dict.fromkeys(group_key(step) for step in steps))
    by_section = {s["section"]: s for s in subtasks}
    resolved: dict[str, dict] = {}
    unresolved = []
    for name in groups:
        if name in by_section:
            resolved[name] = by_section[name]
        else:
            unresolved.append(name)

    # Greedy unique assignment on shared words, highest score first, so a group
    # cannot claim a subtask another group matches better.
    if unresolved:
        free = [s for s in subtasks if s not in resolved.values()]
        scored = []
        for name in unresolved:
            words = set(re.findall(r"[a-z]{3,}", name.lower().replace("_", " ")))
            for subtask in free:
                target = set(re.findall(r"[a-z]{3,}", subtask["title"].lower()))
                overlap = len(words & target)
                if overlap:
                    scored.append((overlap, name, subtask["id"], subtask))
        scored.sort(key=lambda row: (-row[0], row[1], row[2]))
        taken: set[str] = set()
        for _, name, subtask_id, subtask in scored:
            if name in resolved or subtask_id in taken:
                continue
            resolved[name] = subtask
            taken.add(subtask_id)

    out = []
    counts = {name: sum(1 for s in steps if group_key(s) == name) for name in groups}
    seen: dict[str, int] = {}
    for step in steps:
        name = group_key(step)
        seen[name] = seen.get(name, 0) + 1
        subtask = resolved.get(name)
        checks = [c for c in step.get("checks") or [] if (c.get("statement") or "").strip()]
        errors = [e for e in step.get("error_modes") or [] if (e.get("statement") or "").strip()]
        out.append({
            "step_id": step.get("id") or "",
            "text": (step.get("text") or "").strip(),
            "group": name,
            "group_index": seen[name],
            "group_count": counts[name],
            "subtask_id": (subtask or {}).get("id"),
            "subtask_title": (subtask or {}).get("title"),
            "checks": checks,
            "error_modes": errors,
            "photo_checks": [c for c in checks if (c.get("observable") or "photo") == "photo"],
            "other_checks": [c for c in checks
                             if (c.get("observable") or "photo") != "photo"],
        })
    return out


def photo_fit(step: dict) -> tuple[str, str]:
    """Whether a photograph can settle this step, and why.

    The pack already classified every check `photo`, `video`, `measurement` or
    `document`, and that judgement is the whole point of the field — it decides
    whether a grader is asked an answerable question. A step with no photo-
    observable check is not gradeable from a still, and saying so is more useful
    than issuing a rubric that cannot be honoured.
    """
    total, visible = len(step["checks"]), len(step["photo_checks"])
    if not total:
        return "none", "the pack compiled no checks for this step"
    if visible == total:
        return "full", f"all {total} compiled checks are photo-observable"
    if visible:
        return "partial", (f"{visible} of {total} compiled checks are photo-observable; "
                           "the rest need a measurement, a document or the action watched")
    kinds = sorted({c.get("observable") or "photo" for c in step["other_checks"]})
    return "none", (f"no compiled check is photo-observable — this step is settled by "
                    f"{', '.join(kinds)}")


def frame_list(acs: str, clip: str) -> list[str]:
    directory = FRAME_DIR / acs / clip
    if not directory.is_dir():
        return []
    # Filenames encode timestamps (`t000012_25.jpg`), so lexical order is
    # chronological order.
    return sorted(path.name for path in directory.glob("t*.jpg"))


def assign_frames(acs: str, steps: list[dict]) -> None:
    """Attach a suggested frame to each step, in place.

    This mirrors `suggest_clip` / `suggest_frame` in `inspector/server.py`, and
    the two have to stay in step: a rubric graded against one frame in the
    inspector and a different one here would produce two verdicts for the same
    work. The rule is that only work ending where a frame was taken may be
    graded against it — groups sharing a clip take successive slices of it, and
    a group's steps subdivide their own slice, so step *i* of *n* lands at its
    own boundary rather than every step standing at the clip's final frame.
    """
    clips = sorted({path.name for path in (FRAME_DIR / acs).iterdir()}
                   if (FRAME_DIR / acs).is_dir() else [])
    groups = list(dict.fromkeys(step["group"] for step in steps))
    if not clips or not groups:
        for step in steps:
            step["frame"] = None
            step["frame_basis"] = "no extracted frames for this task"
        return

    skip = {"the", "and", "for", "with", "from", "into", "onto", "line"}

    def words(text: str) -> list[str]:
        return [w for w in re.findall(r"[a-z]{3,}", (text or "").lower().replace("_", " "))
                if w not in skip]

    def related(a: str, b: str) -> bool:
        return len(a) >= 3 and len(b) >= 3 and (a.startswith(b[:4]) or b.startswith(a[:4]))

    title_of = {step["group"]: (step.get("subtask_title") or step["group"]) for step in steps}
    group_clip: dict[str, str | None] = {}
    for name in groups:
        source = words(title_of.get(name) or name)
        best, best_score = None, 0
        for clip in clips:
            target = words(clip)
            score = sum(1 for w in source if any(related(w, t) for t in target))
            if score > best_score:
                best, best_score = clip, score
        if best is None and len(clips) == 1:
            best = clips[0]
        group_clip[name] = best

    # Where the clips are one word plus an index and there is exactly one per
    # group, the correspondence is positional. The equal-count condition keeps
    # it from being a guess: 8 groups against 8 numbered clips is a filming
    # convention, 4 against 3 is not.
    numbered = sorted((c for c in clips if re.search(r"_(\d+)$", c)),
                      key=lambda c: int(re.search(r"_(\d+)$", c).group(1)))
    same_prefix = len({re.sub(r"_\d+$", "", c) for c in numbered}) == 1
    positional = (numbered and same_prefix and len(numbered) == len(clips)
                  and len(numbered) == len(groups))
    if positional:
        group_clip = dict(zip(groups, numbered))

    peers: dict[str, list[str]] = {}
    for name in groups:
        if group_clip.get(name):
            peers.setdefault(group_clip[name], []).append(name)

    for step in steps:
        clip = group_clip.get(step["group"])
        names = frame_list(acs, clip) if clip else []
        if not names:
            step["frame"] = None
            step["frame_basis"] = ("no clip matched this subtask" if not clip
                                   else f"no extracted frames for clip {clip}")
            continue
        shared = peers.get(clip) or [step["group"]]
        share = shared.index(step["group"]) if step["group"] in shared else 0
        fraction = (share + step["group_index"] / step["group_count"]) / len(shared)
        cut = round(len(names) * min(1.0, max(0.0, fraction))) - 1
        step["frame"] = f"build/frames/{acs}/{clip}/{names[min(len(names) - 1, max(0, cut))]}"
        basis = [f"clip {clip} matched by {'clip order' if positional else 'name'}"]
        if len(shared) > 1:
            basis.append(f"subtask {share + 1} of {len(shared)} on this clip")
        basis.append(f"step {step['group_index']} of {step['group_count']} within it")
        basis.append(f"{round(fraction * 100)}% of the way through")
        step["frame_basis"] = ", ".join(basis)


def windows(text: str, size: int = 110, stride: int = 55) -> list[list[str]]:
    """Overlapping token windows, so a page is scored by its best passage.

    A printed handbook page carries several unrelated topics. Page 9-3 opens with
    tube cutting, gives the deburring tool, and ends inside hand bending; scored
    whole, it reads as weakly about all three and loses the deburring subtask to
    a page that merely says "tube" more often. Scoring the best window instead
    lets one relevant passage carry the page, which is how the citation is meant
    to be read anyway.
    """
    tokens = content_tokens(text)
    if len(tokens) <= size:
        return [tokens] if tokens else []
    return [tokens[start:start + size] for start in range(0, len(tokens) - size + stride, stride)]


def cite(reference: dict, block: dict) -> str:
    parts = [reference["handbook"]]
    if block["kind"] == "page":
        if reference.get("chapter"):
            parts.append(f"ch. {reference['chapter']}")
        parts.append(f"p. {block['label']}")
    elif block["kind"] == "section":
        parts.append(f"§ {block['label']}")
    elif reference.get("labels"):
        parts.append(f"pp. {reference['labels'][0]}–{reference['labels'][-1]}")
    return " ".join(parts)


def term_weights(subtasks: list[dict]) -> dict[str, float]:
    """How much each word distinguishes one subtask of a task from its siblings.

    Every subtask of AM.I.D.S1 says "tubing", "tool" and "check"; only one says
    "burrs" and only one says "flare". Weighting the query by this is what stops
    a subtask matching the manual page that happens to use the most shop verbs.
    Without it, `Deburr and Resize Tubing Ends` ranked the flaring page above the
    deburring page on the strength of "firmly", "check" and "hydraulic".
    """
    frequency: Counter = Counter()
    for subtask in subtasks:
        frequency.update(set(content_tokens(subtask_text(subtask))))
    total = max(1, len(subtasks))
    return {term: math.log(1 + total / count) for term, count in frequency.items()}


def match_manual(subtask: dict, references: list[dict], limit: int = 3,
                 weights: dict[str, float] | None = None) -> list[dict]:
    """The manual blocks that actually bear on this subtask, best first.

    Scoring is per printed page rather than per extract, so `Bending the Tubing`
    cites p. 9-3 instead of the whole 9-1..9-7 range. Only the blocks returned
    here are put in front of the model, which is what stops a criterion citing a
    page it never saw.

    Terms are weighted by inverse document frequency across the pages in scope,
    and that is what makes the match usable. Every page of the handbook's fluid
    lines chapter says "tubing"; only one says "deburring". Ranking on raw
    overlap put the cutting subtask on the page about flareless fittings, because
    that page is long and says "tube" often — a citation that looks right in the
    output and supports nothing.
    """
    query = Counter(content_tokens(subtask_text(subtask)))
    if not query:
        return []

    blocks = [(reference, block) for reference in references for block in reference["blocks"]]
    document_frequency: Counter = Counter()
    for _, block in blocks:
        document_frequency.update(set(content_tokens(block["text"])))
    total_blocks = max(1, len(blocks))

    def idf(term: str) -> float:
        return math.log(1 + total_blocks / document_frequency[term])

    scored = []
    for reference, block in blocks:
        best = 0.0
        for window in windows(block["text"]):
            counts = Counter(window)
            total = sum(counts.values())
            if not total:
                continue
            overlap = sum(math.log1p(occurrences) * math.log1p(counts[term]) * idf(term)
                          * (weights or {}).get(term, 1.0)
                          for term, occurrences in query.items() if term in counts)
            best = max(best, overlap / math.sqrt(total))
        if not reference["sidecar"]:
            # An extract with no sidecar carries no page provenance, so it
            # loses ties to one that does rather than winning on prose alone.
            best *= 0.95
        scored.append((best, reference, block))

    scored.sort(key=lambda row: (-row[0], row[1]["file"], str(row[2]["label"])))
    if not scored or scored[0][0] <= 0:
        return []
    floor = 0.4 * scored[0][0]

    out = []
    for position, (score, reference, block) in enumerate(scored[:limit]):
        # The two best pages are always supplied; the floor only decides whether
        # a third is worth the context. Supplying a page is not citing it — the
        # model is told to cite only what it relies on, and `criteria_lint.py`
        # drops any citation that was not in the prompt.
        if position >= 2 and score < floor:
            break
        if reference["cited_by_source"]:
            confidence = "high" if block["kind"] == "page" else "medium"
        else:
            confidence = "low"
        out.append({
            "file": reference["file"],
            "handbook": reference["handbook"],
            "citation": cite(reference, block),
            "label": block["label"],
            "kind": block["kind"],
            "text": block["text"],
            "cited_by_source": reference["cited_by_source"],
            "located_by": reference["located_by"],
            "confidence": confidence,
            "score": round(score, 4),
        })
    return out


# ------------------------------------------------------------ source bundle


def pack_non_photo(acs: str, section: str) -> list[str]:
    """Claims an earlier compilation already judged unsettleable from a still.

    `pack.yaml` marks every check `photo`, `video`, `measurement` or `document`.
    The non-photo ones are exactly the requirements this rubric must not ask a
    grader to confirm, so they go into the prompt as a prohibition rather than
    being rediscovered — and rediscovered inconsistently — on every run.
    """
    pack_path = TASK_DIR / acs / "pack.yaml"
    if not pack_path.exists():
        return []
    try:
        import yaml
        pack = yaml.safe_load(pack_path.read_text()) or {}
    except Exception:
        return []
    out = []
    for step in pack.get("steps") or []:
        if (step.get("section") or "") != section:
            continue
        for check in step.get("checks") or []:
            if check.get("observable") in {"video", "measurement", "document"}:
                statement = (check.get("statement") or "").strip()
                if statement and statement not in out:
                    out.append(statement)
    return out[:8]


def preamble(acs: str) -> str:
    """The sheet's own prerequisites, hazards and equipment, shared by every subtask."""
    data = read_json(TASK_DIR / acs / "steps.json") or {}
    buckets = {"you_need_to": [], "safety": [], "equipment": []}
    for variant in data.get("variants") or []:
        for section in variant.get("sections") or []:
            for field in buckets:
                for item in section.get(field) or []:
                    text = (item.get("text") or "").strip()
                    if text and text not in buckets[field]:
                        buckets[field].append(text)
    lines = []
    for field, heading in (("you_need_to", "You Need to"), ("safety", "Safety"),
                           ("equipment", "Equipment")):
        if buckets[field]:
            lines.append(f"{heading}: " + "; ".join(buckets[field]))
    return "\n".join(lines)


def source_bundle(acs: str, task_title: str, subtask: dict, manuals: list[dict],
                  prerequisites: str) -> str:
    """Exactly the text the rubric for this subtask may rest on."""
    sections = [f"TASK\n{acs} — {task_title}"]

    lines = [f"Subtask {subtask['position']}: {subtask['title']}"]
    if subtask.get("variant") and subtask["variant"] != subtask["title"]:
        lines.append(f"From procedure variant: {subtask['variant']}")
    for number, step in enumerate(subtask["steps"], start=1):
        lines.append(f"Step {number}: {step['text']}")
        for note in step["notes"]:
            lines.append(f"  Senior Mechanic Note: {note}")
    covered = {note for step in subtask["steps"] for note in step["notes"]}
    for note in subtask["notes"]:
        if note not in covered:
            lines.append(f"Senior Mechanic Note: {note}")
    for continuation in subtask.get("continuations", []):
        lines.append(f"Also under this heading: {continuation}")
    sections.append("PROCEDURE DOCUMENT — tasks/%s/procedure.md\n%s" % (acs, "\n".join(lines)))

    # Where this subtask's scope ends. Without it a rubric reaches forward into
    # work that has not happened yet: AM.III.M.S5's "Prepare the Damaged Area"
    # is clean, measure and mark, and its first draft made "no material has been
    # removed from the blade" a critical defect — condemning the exactly correct
    # finished state, because removing material is the next subtask.
    scope = []
    if subtask.get("previous"):
        scope.append(f"The previous subtask was “{subtask['previous']}” — already done, "
                     "and its result may be visible but is not graded here.")
    if subtask.get("next"):
        scope.append(f"The next subtask is “{subtask['next']}” — NOT yet performed. Work "
                     "belonging to it must not appear as a criterion or a defect, and its "
                     "absence is the correct state.")
    if scope:
        sections.append("SCOPE OF THIS SUBTASK\n" + "\n".join(scope))

    if prerequisites:
        sections.append("PREREQUISITES, HAZARDS AND EQUIPMENT FROM THE SAME "
                        f"PROCEDURE DOCUMENT\n{prerequisites}")

    for manual in manuals:
        if manual["cited_by_source"]:
            provenance = "cited by the procedure sheet"
        else:
            provenance = ("NOT cited by the procedure sheet — located by content search; "
                          "treat any standard taken from it as provisional and say so in "
                          "source_notes")
        sections.append(f"REFERENCE MANUAL — {manual['citation']} ({provenance})\n"
                        f"{manual['text']}")

    blocked = pack_non_photo(acs, subtask["section"])
    if blocked:
        sections.append("NOT SETTLEABLE FROM A PHOTOGRAPH — an earlier compilation of this "
                        "section judged each of these to need a measurement, a document or "
                        "the action watched. Do not write a criterion that depends on one.\n"
                        + "\n".join(f"- {item}" for item in blocked))
    return "\n\n".join(sections)


# ---------------------------------------------------------------- rendering


TEMPLATE = """### {subtask_id} — VLM GRADING CRITERIA

Assess the completed {description} visible in the image. Evaluate each criterion \
independently as PASS or FAIL.

**Criteria**
{criteria}

**Critical defects**
{defects}

**Overall decision**
Overall PASS requires every required criterion to pass and no critical defect. Mark \
an unobservable required criterion “FAIL — not demonstrated in image.” Evaluate only \
visible evidence and do not infer hidden measurements, internal damage, torque, \
pressure, or test results.

**Source basis**
- Procedure: {procedure}
- Manual: {manual}
- Notes: {notes}
"""


def render_rubric(entry: dict) -> str:
    criteria = "\n".join(f"{n}. {text}" for n, text in enumerate(entry["criteria"], start=1))
    defects = "\n".join(f"- {text}" for text in entry["critical_defects"])
    return TEMPLATE.format(
        subtask_id=entry["subtask_id"],
        description=entry["subtask_description"],
        criteria=criteria or "1. (none drafted)",
        defects=defects or "- (none drafted)",
        procedure="; ".join(entry["procedure_sources"]) or "none",
        manual="; ".join(entry["manual_sources"]) or "none cited",
        notes="; ".join(entry["source_notes"]) or "none",
    )


def word_count(markdown: str) -> int:
    """Words in a rendered rubric, ignoring Markdown syntax.

    Stated here rather than left implicit because the 100–300 band is checked
    against it and a rubric that fails validation has to be reproducible.
    """
    text = re.sub(r"^#{1,6}\s*", "", markdown, flags=re.M)
    text = text.replace("**", "")
    text = re.sub(r"^\s*[-*]\s+", "", text, flags=re.M)
    text = re.sub(r"^\s*\d+\.\s+", "", text, flags=re.M)
    return sum(1 for word in re.split(r"\s+", text) if re.search(r"[A-Za-z0-9]", word))


# ---------------------------------------------------------------- discovery


def discover(only: list[str] | None = None) -> list[dict]:
    """Every task code with a procedure document, with its subtasks resolved."""
    titles = {row["acs_code"]: (row.get("task") or "").strip().rstrip(".")
              for row in task_rows()}
    out = []
    for directory in sorted(TASK_DIR.iterdir()) if TASK_DIR.is_dir() else []:
        acs = directory.name
        if only and acs not in only:
            continue
        if not (directory / "steps.json").exists():
            continue
        data = read_json(directory / "steps.json") or {}
        variants = data.get("variants") or []
        title = titles.get(acs) or ""
        if len(variants) == 1:
            title = strip_code_prefix(variants[0].get("variant") or "") or title

        subtasks = subtasks_for(acs)
        preserved = preserved_ids(acs, subtasks)
        for subtask in subtasks:
            if subtask["index"] in preserved:
                subtask["id"] = preserved[subtask["index"]]
                subtask["id_origin"] = "reference clip name"
            else:
                subtask["id_origin"] = "derived from heading"

        references = reference_files(acs)
        weights = term_weights(subtasks)
        for subtask in subtasks:
            subtask["manuals"] = match_manual(subtask, references, weights=weights)

        out.append({
            "task_code": acs,
            "task_title": title,
            "procedure_file": f"tasks/{acs}/procedure.md",
            "subtasks": subtasks,
            "references": references,
            "prerequisites": preamble(acs),
        })
    return out
