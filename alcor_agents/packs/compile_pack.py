#!/usr/bin/env python3
"""Compile a task pack and its photo criteria from the procedure sheet + handbook.

AM.I.E.S1 and AM.II.K.S3 were compiled by hand, and the inspector's Atoms and
Photo-assessment tabs are built on what that compilation produced: `checks` become
correctness atoms, `error_modes` become defect atoms, and both supply the text a
photo is graded against. The other nine pilot tasks have a procedure sheet and
nothing else, so both tabs are empty for them.

This drafts the missing structure. The skeleton — step ids, ordering, sections,
safety, equipment, references — is derived deterministically from `steps.json`
and `tasks.csv`. Only judgement is asked of a model: what the finished work must
satisfy, how it goes wrong, and which of those a photograph can actually settle.

    python3 packs/compile_pack.py AM.III.F.S11          # one task
    python3 packs/compile_pack.py --all                 # every task without a pack
    python3 packs/compile_pack.py AM.III.F.S11 --dry-run   # skeleton only, no calls

Two outputs per task:

    tasks/<ACS>/pack.yaml        the pack, status: draft, every drafted element
                                 marked `origin: drafted`
    build/criteria/<ACS>.json    the photo criterion for each step and for the
                                 task, with per-condition source attribution

They are separate because they answer to different readers. The pack is what an
SME reviews and what the atoms are built from, so it stays terse. The criteria
store carries the attribution trail — which source each condition rests on, the
quoted handbook phrase behind any number — which is what makes a criterion
defensible to a student but would bury the pack.

**These packs are machine-drafted and have not been through subject-matter
review.** That is what `provenance:` records and what `status: draft` gates:
`pack_lint.py --require-reviewed` refuses them, and the inspector shows a banner.
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "inspector"))

import vlm  # noqa: E402

TASK_DIR = ROOT / "tasks"
TASKS_CSV = ROOT / "data" / "processed" / "tasks.csv"
VIDEO_DIR = ROOT / "data" / "videos"
CRITERIA_DIR = ROOT / "build" / "criteria"
VIDEO_SUFFIXES = {".mp4", ".mov", ".m4v"}

GENERATOR = "packs/compile_pack.py"
SCHEMA_VERSION = 1

# Hand-authored steps for operations the AIM sheet omits. Kept out of steps.json
# because packs/ingest.py rewrites that file from the .docx on every run.
SUPPLEMENT = "steps_supplement.json"
CAMPUS = "AIM Fremont"  # every pilot task in tasks.csv is taught here

# Sections that carry prerequisites and boilerplate rather than gradeable work.
# They hold no steps in practice, but naming them keeps a future sheet that does
# put a step under one from silently becoming an assessable subtask.
NON_PROCEDURE_SECTIONS = {"Before You Begin", "Safety and Equipment"}

# Dropped when building a section's two-letter mnemonic, so "Tie a Hitch Along
# the Bundle" keys on "tie hitch" rather than "tie a".
ID_STOPWORDS = {"a", "an", "the", "of", "on", "in", "into", "for", "and", "to",
                "with", "using", "from", "at", "by", "out", "up"}


# ------------------------------------------------------------------ skeleton


def task_rows() -> list[dict]:
    with TASKS_CSV.open() as handle:
        return list(csv.DictReader(handle))


def read_json(path: Path):
    return json.loads(path.read_text()) if path.exists() else None


def read_text(path: Path) -> str:
    return path.read_text() if path.exists() else ""


def section_key(name: str, taken: set[str]) -> str:
    """A short, stable mnemonic for a section, matching AM.II.K.S3's convention.

    Ids appear in every verdict, dataset label and eval row, so they need to be
    readable and to stay put. Initials of the first two significant words give
    `pw` for "Prepare the Wire" and `tc` for "Test the Connector", the same
    shape the hand-compiled pack uses.
    """
    words = [w for w in re.findall(r"[A-Za-z]+", name) if w.lower() not in ID_STOPWORDS]
    key = "".join(w[0].lower() for w in words[:2]) or "s"
    if key in taken:
        for suffix in range(2, 100):
            if f"{key}{suffix}" not in taken:
                key = f"{key}{suffix}"
                break
    taken.add(key)
    return key


def section_steps(section: dict, taken: set[str], origin: str) -> list[dict]:
    """Ordered steps for one section of steps.json or of the supplement.

    `note_refs` are 1-based indices into the section's Senior Mechanic Notes,
    which is where nearly all the acceptance detail lives — the step line itself
    is usually just an imperative ("Tie a hitch.").
    """
    steps = section.get("steps") or []
    if not steps or section.get("section") in NON_PROCEDURE_SECTIONS:
        return []
    name = section.get("section") or "Procedure"
    key = section_key(name, taken)
    notes = [(n.get("text") or "").strip() for n in section.get("notes") or []]
    out = []
    for index, step in enumerate(steps, start=1):
        refs = [r for r in (step.get("note_refs") or []) if isinstance(r, int)]
        out.append({
            "id": f"{key}.s{index}",
            "section": name,
            "text": (step.get("text_clean") or step.get("text") or "").strip(),
            "note_refs": refs,
            "notes": [notes[r - 1] for r in refs if 1 <= r <= len(notes)],
            "position": f"{index} of {len(steps)}",
            "origin": origin,
        })
    return out


def procedure_steps(acs: str) -> list[dict]:
    """Sheet steps from steps.json, then any drafted steps from the supplement.

    Sheet steps are emitted first and claim their mnemonics first, so adding a
    supplement never renumbers an existing id — ids travel in every verdict,
    dataset label and eval row, and must stay put.
    """
    data = read_json(TASK_DIR / acs / "steps.json") or {}
    out: list[dict] = []
    taken: set[str] = set()
    for variant in data.get("variants") or []:
        for section in variant.get("sections") or []:
            out.extend(section_steps(section, taken, "sheet"))
    for section in (read_json(TASK_DIR / acs / SUPPLEMENT) or {}).get("sections") or []:
        out.extend(section_steps(section, taken, "drafted"))
    return out


def collect(acs: str, field: str) -> list[str]:
    """Union of a section field (safety, equipment) in source order."""
    data = read_json(TASK_DIR / acs / "steps.json") or {}
    supplement = read_json(TASK_DIR / acs / SUPPLEMENT) or {}
    sections = [s for v in data.get("variants") or [] for s in v.get("sections") or []]
    sections += supplement.get("sections") or []
    out: list[str] = []
    for section in sections:
        for item in section.get(field) or []:
            text = (item.get("text") or "").strip()
            if text and text not in out:
                out.append(text)
    return out


def handbook_references(acs: str) -> list[dict]:
    """Pack `references.handbook` entries, read back from the extractor sidecars.

    Read from the sidecar rather than restated, because pack_lint.py compares the
    two and a hand-typed page list that drifts from what was actually extracted
    is precisely the error it exists to catch.
    """
    directory = TASK_DIR / acs / "references" / "handbook"
    entries = []
    for sidecar in sorted(directory.glob("*.json")) if directory.is_dir() else []:
        meta = read_json(sidecar) or {}
        labels = meta.get("labels") or []
        chapter = None
        if labels and re.match(r"^\d+-", labels[0]):
            chapter = int(labels[0].split("-")[0])
        entry = {"handbook": meta.get("handbook")}
        if chapter is not None:
            entry["chapter"] = chapter
        entry.update({
            "pages": labels,
            "pdf_page_indices": meta.get("pdf_page_indices") or [],
            "located_by": meta.get("located_by"),
            "cited_by_source": bool(meta.get("cited_by_source")),
            "file": meta.get("file"),
        })
        if not entry["cited_by_source"]:
            # A reference the campus never cited cannot pass for a campus
            # standard, so it carries an id and an assumption entry.
            entry["id"] = f"ref.handbook.{(meta.get('handbook_key') or '?').lower()}"
            entry["assumed"] = True
        entries.append(entry)
    return entries


def videos_for(acs: str) -> list[dict]:
    directory = VIDEO_DIR / acs
    if not directory.is_dir():
        return []
    return [
        {"path": str(path.relative_to(ROOT))}
        for path in sorted(directory.iterdir())
        if path.suffix.lower() in VIDEO_SUFFIXES
    ]


# ------------------------------------------------------------- source bundle


def source_bundle(acs: str, row: dict) -> str:
    """The text every drafting call for this task is grounded in.

    The whole normalized procedure sheet goes in — Step Instructions *and*
    Senior Mechanic Notes — because the bare step line carries almost none of
    the acceptance detail. The handbook goes in because numeric standards live
    there and nowhere else. Provenance travels with each, since a criterion
    resting on a reference the campus never cited must be reviewable as such.
    """
    sections = [f"TASK\n{row.get('acs_code')} — {row.get('task', '')}"]

    sheet = read_text(TASK_DIR / acs / "procedure.md").strip()
    if sheet:
        sections.append(f"FULL PROCEDURE SHEET\n{sheet}")

    # Supplemental steps travel with their provenance for the same reason an
    # uncited handbook range does: a check resting on an operation the campus
    # never wrote down must be reviewable as such.
    supplement = read_json(TASK_DIR / acs / SUPPLEMENT) or {}
    if supplement.get("sections"):
        lines = []
        for section in supplement["sections"]:
            lines.append(f"## {section.get('section')}")
            for index, step in enumerate(section.get("steps") or [], start=1):
                lines.append(f"{index}. {step.get('text_clean') or step.get('text')}")
            for index, note in enumerate(section.get("notes") or [], start=1):
                lines.append(f"   note {index}: {note.get('text')}")
        sections.append(
            "SUPPLEMENTAL STEPS — NOT from the AIM procedure sheet. Drafted to cover "
            "operations the sheet omits; treat any standard taken from them as "
            "provisional and say so.\n"
            f"Rationale: {supplement.get('rationale', '')}\n" + "\n".join(lines)
        )

    for reference in handbook_references(acs):
        text = read_text(TASK_DIR / acs / (reference.get("file") or "")).strip()
        if not text:
            continue
        if reference["cited_by_source"]:
            provenance = "cited by the AIM procedure sheet"
        else:
            provenance = ("NOT cited by the AIM procedure sheet — located during pack "
                          "compilation and flagged assumed; treat any standard taken "
                          "from it as provisional and say so")
        pages = ", ".join(reference.get("pages") or [])
        sections.append(
            f"REFERENCE HANDBOOK — {reference.get('handbook')} pages {pages} "
            f"({provenance})\n{text}"
        )
    return "\n\n".join(sections)


def step_prompt(step: dict) -> str:
    lines = [f"Section: {step['section']}",
             f"Step {step['position']}: {step['text']}"]
    for note in step["notes"]:
        lines.append(f"Senior Mechanic Note: {note}")
    return "\n".join(lines)


def steps_summary(steps: list[dict], drafted: dict[str, dict]) -> str:
    """What the task-level call is told about the steps already compiled."""
    lines = []
    for step in steps:
        lines.append(f"- {step['id']} [{step['section']}] {step['text']}")
        for check in (drafted.get(step["id"]) or {}).get("checks") or []:
            lines.append(f"    check ({check.get('observable')}): {check.get('statement')}")
    return "\n".join(lines)


# ---------------------------------------------------------------- compiling


def clean(value, allowed: set[str] | None = None, default: str | None = None):
    text = (str(value).strip().lower() if value is not None else "")
    if allowed is not None and text not in allowed:
        return default
    return text or default


def with_retry(call, attempts: int = 3) -> dict:
    """Retry a drafting call, including on an unparseable reply.

    A truncated or fenced reply is a transport-level miss, not a verdict about
    the work, and dropping it silently costs a step its checks or a task its
    whole evidence list. Every failure mode here is worth another attempt.
    """
    result: dict = {}
    for attempt in range(attempts):
        result = call()
        if not result.get("error"):
            return result
        if attempt < attempts - 1:
            time.sleep(2 * (attempt + 1))
    return result


def compile_task(acs: str, row: dict, model: str, workers: int,
                 dry_run: bool = False) -> tuple[dict, dict, float]:
    steps = procedure_steps(acs)
    if not steps:
        raise SystemExit(f"{acs}: steps.json defines no procedure steps")

    bundle = source_bundle(acs, row)
    drafted: dict[str, dict] = {}
    spend = 0.0

    if not dry_run:
        def draft(step: dict) -> tuple[str, dict]:
            return step["id"], with_retry(lambda: vlm.draft_pack_step(
                model=model, sources=bundle, step_text=step_prompt(step)))

        with ThreadPoolExecutor(max_workers=max(1, min(workers, len(steps)))) as pool:
            for step_id, result in pool.map(draft, steps):
                spend += result.get("cost_usd") or 0.0
                if result.get("error"):
                    print(f"    ! {step_id}: {result['error']} — {result.get('message', '')[:90]}")
                    continue
                drafted[step_id] = result

    # --- steps ------------------------------------------------------------
    assumptions: list[dict] = []
    pack_steps = []
    for step in steps:
        result = drafted.get(step["id"]) or {}
        checks, errors = [], []
        for index, check in enumerate(result.get("checks") or [], start=1):
            statement = (check.get("statement") or "").strip()
            if not statement:
                continue
            check_id = f"{step['id']}.c{index}"
            entry = {
                "id": check_id,
                "statement": statement,
                "observable": clean(check.get("observable"),
                                    {"photo", "video", "measurement", "document"}, "photo"),
                "source": check.get("source") or "procedure sheet",
                "origin": "drafted",
            }
            if check.get("note"):
                entry["note"] = str(check["note"]).strip()
            if check.get("assumed"):
                entry["assumed"] = True
                assumptions.append({
                    "id": check_id,
                    "statement": statement,
                    "reason": str(check.get("note") or "").strip() or
                              "Drawn from a handbook section the procedure sheet does not cite.",
                    "resolve_by": "Confirm with AIM before marking this pack reviewed.",
                })
            checks.append(entry)

        for index, error in enumerate(result.get("error_modes") or [], start=1):
            statement = (error.get("statement") or "").strip()
            if not statement:
                continue
            errors.append({
                "id": f"{step['id']}.e{index}",
                "statement": statement,
                "severity": clean(error.get("severity"),
                                  {"critical", "major", "minor"}, "major"),
                "origin": "drafted",
            })

        entry = {"id": step["id"], "section": step["section"], "text": step["text"]}
        if step.get("origin") == "drafted":
            # Not a step the campus wrote. A reviewer reading this pack has to be
            # able to tell an operation drafted from the handbook apart from one
            # the AIM sheet actually specifies.
            entry["origin"] = "drafted"
            entry["assumed"] = True
        if step["note_refs"]:
            entry["note_refs"] = step["note_refs"]
        # pack_lint requires at least one check per step. A step the model could
        # not compile gets an explicit placeholder rather than silently
        # disappearing from the atoms, which would read as "nothing to check
        # here" instead of "this was never compiled".
        entry["checks"] = checks or [{
            "id": f"{step['id']}.c1",
            "statement": f"The work described by this step is complete: {step['text']}",
            "observable": "photo",
            "source": "procedure sheet",
            "note": "Placeholder — drafting produced no checks for this step.",
            "origin": "placeholder",
        }]
        if errors:
            entry["error_modes"] = errors
        pack_steps.append(entry)

    # Each drafted step carries assumed: true, and pack_lint pairs assumed items
    # with assumptions entries one-for-one by id, so each needs its own entry.
    # The reason names the section, because that is the level at which the scope
    # question actually gets settled.
    drafted_sections: dict[str, str] = {}
    for step in steps:
        if step.get("origin") != "drafted":
            continue
        drafted_sections.setdefault(step["section"], step["id"].split(".")[0])
        assumptions.append({
            "id": step["id"],
            "statement": step["text"],
            "reason": f"The AIM procedure sheet documents no '{step['section']}' steps; "
                      "this one was drafted from the handbook and the reference video to "
                      "cover an operation the sheet omits.",
            "resolve_by": f"Confirm with AIM that '{step['section']}' forms part of the "
                          "graded submission, ideally against a corrected procedure sheet, "
                          "before marking this pack reviewed.",
        })

    # --- task level -------------------------------------------------------
    fit = row.get("photo_fit_level") or "Unrated"
    task_result: dict = {}
    if not dry_run:
        # The task call has more to say than a step call — an evidence list, a
        # criterion, sources and open questions — so it gets a larger budget.
        # Running out of tokens mid-JSON loses the whole reply.
        task_result = with_retry(lambda: vlm.draft_pack_task(
            model=model, sources=bundle, max_tokens=8000,
            summary=(f"Campus photo-assessment fit rating: {fit} "
                     f"({row.get('photo_assessment_fit', '')})\n\n"
                     + steps_summary(steps, drafted)),
        ))
        spend += task_result.get("cost_usd") or 0.0
        if task_result.get("error"):
            print(f"    ! task level: {task_result['error']} — "
                  f"{task_result.get('message', '')[:90]}")
            task_result = {}

    evidence = []
    for index, item in enumerate(task_result.get("evidence_required") or [], start=1):
        description = (item.get("description") or "").strip()
        if not description:
            continue
        entry = {
            "id": f"ev{index}",
            "description": description,
            "medium": clean(item.get("medium"),
                            {"photo", "video", "measurement", "document"}, "photo"),
            "origin": "drafted",
        }
        if item.get("framing"):
            entry["framing"] = str(item["framing"]).strip()
        # A non-photo evidence item is an acceptance criterion a still cannot
        # settle, which is exactly the kind of claim that must be flagged rather
        # than quietly folded into a photo grade.
        if item.get("assumed") or entry["medium"] != "photo":
            entry["assumed"] = True
            assumptions.append({
                "id": entry["id"],
                "statement": description,
                "reason": f"Required as {entry['medium']}, which a single photograph "
                          "cannot evidence.",
                "resolve_by": "Decide the capture method with Alcor during pilot scoping.",
            })
        evidence.append(entry)

    if not evidence:
        evidence = [{
            "id": "ev1",
            "description": "Overall view of the completed work for "
                           f"{(row.get('task') or acs).rstrip('.')}.",
            "medium": "photo",
            "framing": "Whole workpiece in frame, perpendicular to the work, in focus.",
            "origin": "placeholder",
        }]

    references = {}
    handbook = handbook_references(acs)
    if handbook:
        references["handbook"] = handbook
        for reference in handbook:
            if reference.get("assumed"):
                assumptions.append({
                    "id": reference["id"],
                    "statement": f"{reference['handbook']} pages "
                                 f"{', '.join(reference.get('pages') or [])} are the "
                                 "governing handbook section for this task.",
                    "reason": "The procedure sheet cites no available handbook section for "
                              "this task; this range was located by content search during "
                              "pack compilation.",
                    "resolve_by": "Confirm the page range with AIM before marking this "
                                  "pack reviewed.",
                })
    videos = videos_for(acs)
    if videos:
        references["videos"] = videos
    if row.get("trainwithaim_video_url"):
        references["trainwithaim_video"] = row["trainwithaim_video_url"]

    open_questions = [str(q).strip() for q in (task_result.get("open_questions") or [])
                      if str(q).strip()]
    open_questions.insert(
        0,
        "Every check, error mode and evidence item in this pack was drafted by "
        f"{GENERATOR} from the procedure sheet and handbook, and has not been reviewed "
        "by a subject-matter expert.",
    )
    if drafted_sections:
        names = ", ".join(f"'{n}'" for n in drafted_sections)
        open_questions.insert(1, (
            f"The AIM procedure sheet documents no steps for {names}, yet the task title "
            "and the sheet's own equipment list call for that work. Those steps were "
            "drafted from the handbook and the reference video and carry "
            "`origin: drafted`. Confirm with AIM whether they form part of the graded "
            "submission, and ask for a corrected procedure sheet if they do."
        ))
    if not videos:
        open_questions.append(
            "No reference video exists for this task, so no frame of correct work is "
            "available as an exemplar or for photo grading."
        )

    pack = {
        "schema_version": SCHEMA_VERSION,
        "status": "draft",
        "acs_code": acs,
        "task_no": int(row["task_no"]) if str(row.get("task_no", "")).isdigit() else None,
        "title": (row.get("task") or "").strip().rstrip("."),
        "subject": row.get("subject") or "",
        "block": int(row["block"]) if str(row.get("block", "")).isdigit() else row.get("block"),
        "campus": CAMPUS,
        "week": int(row["week"]) if str(row.get("week", "")).isdigit() else row.get("week"),
        "day": int(row["day"]) if str(row.get("day", "")).isdigit() else row.get("day"),
        "provenance": {
            "generator": GENERATOR,
            "model": model if not dry_run else None,
            "drafted_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "reviewed_by": None,
            "sources": ["procedure.md", "steps.json", "tasks.csv"]
            + ([SUPPLEMENT] if (TASK_DIR / acs / SUPPLEMENT).exists() else [])
            + [r["file"] for r in handbook if r.get("file")],
            "note": "Checks, error modes and evidence were drafted from the procedure "
                    "sheet and handbook. Nothing here has been through subject-matter "
                    "review; `status` stays `draft` until it has.",
        },
        "photo_assessment": {
            "fit": fit,
            "rationale": (task_result.get("rationale") or
                          row.get("photo_assessment_fit") or "").strip(),
        },
    }
    if collect(acs, "safety"):
        pack["safety"] = collect(acs, "safety")
    if collect(acs, "equipment"):
        pack["equipment"] = collect(acs, "equipment")
    pack["steps"] = pack_steps
    pack["evidence"] = {"required": evidence}
    if references:
        pack["references"] = references
    if assumptions:
        # Several checks can rest on the same reference; keep the first note.
        seen, deduped = set(), []
        for assumption in assumptions:
            if assumption["id"] in seen:
                continue
            seen.add(assumption["id"])
            deduped.append(assumption)
        pack["assumptions"] = deduped
    pack["open_questions"] = open_questions

    # --- criteria store ---------------------------------------------------
    entries = {}
    for step in steps:
        result = drafted.get(step["id"])
        if not result:
            continue
        entries[step["id"]] = {
            "kind": "step",
            "step_id": step["id"],
            "section": step["section"],
            "step_text": step["text"],
            "criterion": (result.get("criterion") or "").strip(),
            "sources": [
                {"condition": c.get("statement"), "source": c.get("source"),
                 "note": c.get("note"), "assumed": bool(c.get("assumed"))}
                for c in result.get("checks") or [] if c.get("statement")
            ],
            "not_photo_gradeable": [str(x) for x in result.get("not_photo_gradeable") or []],
            "required_framing": result.get("required_framing"),
            "conflicts": [str(x) for x in result.get("conflicts") or []],
        }
    if task_result:
        entries["task"] = {
            "kind": "task",
            "step_id": None,
            "criterion": (task_result.get("criterion") or "").strip(),
            "sources": [s for s in task_result.get("sources") or [] if isinstance(s, dict)],
            "not_photo_gradeable": [str(x) for x in task_result.get("not_photo_gradeable") or []],
            "required_framing": None,
            "conflicts": [],
        }

    criteria = {
        "schema_version": SCHEMA_VERSION,
        "task_code": acs,
        "generator": GENERATOR,
        "model": model if not dry_run else None,
        "drafted_at": pack["provenance"]["drafted_at"],
        "reviewed_by": None,
        "sources": pack["provenance"]["sources"],
        "entries": entries,
    }
    return pack, criteria, spend


# ------------------------------------------------------------------- output


class _Folded(str):
    """A string to emit as a YAML folded scalar."""


def _represent_folded(dumper, data):
    style = "|" if "\n" in data else ">"
    return dumper.represent_scalar("tag:yaml.org,2002:str", data, style=style)


yaml.add_representer(_Folded, _represent_folded)


def fold_long(node, threshold: int = 72):
    """Wrap long prose in folded scalars so the pack reads like the hand-written ones."""
    if isinstance(node, dict):
        return {key: fold_long(value, threshold) for key, value in node.items()}
    if isinstance(node, list):
        return [fold_long(value, threshold) for value in node]
    if isinstance(node, str) and (len(node) > threshold or "\n" in node):
        return _Folded(node)
    return node


HEADER = """\
# Compiled by {generator} from the AIM procedure sheet and the FAA handbook.
# DRAFTED, NOT REVIEWED — every check, error mode and evidence item below was
# proposed by {model} and has not been seen by a subject-matter expert.
# Regenerate with: python3 {generator} {acs}
"""


def emit(pack: dict) -> str:
    body = yaml.dump(fold_long(pack), sort_keys=False, allow_unicode=True,
                     default_flow_style=False, width=88, indent=2)
    return HEADER.format(generator=GENERATOR, acs=pack["acs_code"],
                         model=pack["provenance"].get("model") or "a model") + "\n" + body


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("acs_code", nargs="*")
    parser.add_argument("--all", action="store_true",
                        help="compile every task that has no pack.yaml")
    parser.add_argument("--model", default="anthropic/claude-opus-5")
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--dry-run", action="store_true",
                        help="build the skeleton and print it; make no model calls")
    parser.add_argument("--force", action="store_true",
                        help="overwrite an existing pack.yaml")
    args = parser.parse_args()

    rows = task_rows()
    if args.all:
        selected = [r for r in rows
                    if not (TASK_DIR / r["acs_code"] / "pack.yaml").exists() or args.force]
    elif args.acs_code:
        wanted = set(args.acs_code)
        selected = [r for r in rows if r["acs_code"] in wanted]
        missing = wanted - {r["acs_code"] for r in selected}
        if missing:
            parser.error(f"unknown task(s): {', '.join(sorted(missing))}")
    else:
        parser.error("name at least one task, or pass --all")

    if not args.dry_run and not vlm.load_api_key():
        parser.error("OPENROUTER_API_KEY is not set (environment or alcor_agents/.env)")

    total = 0.0
    for row in selected:
        acs = row["acs_code"]
        pack_path = TASK_DIR / acs / "pack.yaml"
        if pack_path.exists() and not args.force:
            print(f"{acs}: pack.yaml exists, skipping (use --force to overwrite)")
            continue

        steps = procedure_steps(acs)
        print(f"\n{acs} — {row.get('task', '')}\n  {len(steps)} steps"
              + (" (dry run)" if args.dry_run else f" · drafting on {args.model}"))
        pack, criteria, spend = compile_task(acs, row, args.model, args.workers, args.dry_run)
        total += spend

        if args.dry_run:
            print(emit(pack))
            continue

        pack_path.write_text(emit(pack), encoding="utf-8")
        CRITERIA_DIR.mkdir(parents=True, exist_ok=True)
        (CRITERIA_DIR / f"{acs}.json").write_text(
            json.dumps(criteria, indent=2) + "\n", encoding="utf-8")

        checks = sum(len(s.get("checks") or []) for s in pack["steps"])
        errors = sum(len(s.get("error_modes") or []) for s in pack["steps"])
        placeholders = sum(1 for s in pack["steps"]
                           for c in s.get("checks") or [] if c.get("origin") == "placeholder")
        print(f"  -> {checks} checks, {errors} error modes, "
              f"{len(pack['evidence']['required'])} evidence items, "
              f"{len(criteria['entries'])} criteria · ${spend:.2f}")
        if placeholders:
            print(f"  ! {placeholders} step(s) produced no checks and got a placeholder")

    if not args.dry_run:
        print(f"\ntotal spend: ${total:.2f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
