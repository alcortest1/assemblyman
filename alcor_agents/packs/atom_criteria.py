#!/usr/bin/env python3
"""Generate a short photo-assessment rubric for every atom/step in the dataset.

    python3 packs/atom_criteria.py --dry-run       # steps, frames and photo fit, no calls
    python3 packs/atom_criteria.py --all           # draft every step
    python3 packs/atom_criteria.py AM.I.D.S1       # one task
    python3 packs/atom_criteria.py --all --reuse   # re-render from the last drafts
    python3 packs/atom_criteria.py --validate-only

Outputs, all under `criteria/atoms/`:

    <TASK_CODE>/<TASK_CODE>__<step_id>.txt   one rubric per step, task and subtask named
    <TASK_CODE>.md                           every step rubric for a task, in order
    atoms.json                               the machine-readable store
    all_atoms.md                             every step rubric in the dataset
    frame_mapping.csv                        step -> subtask -> photo fit -> frame
    validation_report.md

This is the step-level companion to `generate_criteria.py`. That grades a
finished subtask; this grades one step against the single frame where that step
ends. The atoms are not invented here — `pack.yaml`'s checks are the correctness
atoms and its error modes the defect atoms, and the `observable` field on each
check already records whether a photograph could ever settle it. A step whose
checks are all measurements gets no grading points and says so, because issuing
a rubric that cannot be honoured is how a grader comes to pass work it never saw.

**Machine-drafted, not reviewed**, and the frames are suggestions rather than
reviewed intervals — `frame_basis` records what each guess rested on.
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from difflib import SequenceMatcher
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "inspector"))

import criteria_sources as src  # noqa: E402
import vlm  # noqa: E402

GENERATOR = "packs/atom_criteria.py"
OUT_DIR = ROOT / "criteria" / "atoms"
RAW_DIR = OUT_DIR / ".raw"
WORD_CEILING = 100
RULE = "=" * 78

# Mirrors `generate_criteria.py`. A step rubric is short enough that two
# near-identical points is most of it.
DUPLICATE_RATIO = 0.90


def normalize(text: str) -> str:
    text = re.sub(r"^\s*(?:[-*•]|\d+[.)])\s*", "", str(text or "").strip())
    return re.sub(r"\s+", " ", text).strip().rstrip(";")


def dedupe(items: list[str]) -> list[str]:
    kept: list[str] = []
    for item in items:
        key = re.sub(r"[^a-z0-9 ]", "", item.lower())
        if any(SequenceMatcher(None, key, re.sub(r"[^a-z0-9 ]", "", k.lower())).ratio()
               > DUPLICATE_RATIO for k in kept):
            continue
        kept.append(item)
    return kept


# ------------------------------------------------------------------ drafting


def source_bundle(task: dict, step: dict) -> str:
    """The compiled atoms for this step, and nothing the step does not own."""
    sections = [f"TASK\n{task['task_code']} — {task['task_title']}",
                f"SUBTASK\n{step['subtask_title'] or step['group']} "
                f"(`{step['subtask_id'] or '—'}`)"]

    lines = [f"Step {step['step_id']} ({step['group_index']} of {step['group_count']} "
             f"in this subtask): {step['text']}"]
    sections.append("STEP\n" + "\n".join(lines))

    if step["checks"]:
        rows = []
        for check in step["checks"]:
            observable = check.get("observable") or "photo"
            row = f"- [{observable}] {check.get('statement')}"
            if check.get("note"):
                row += f"\n    basis: {check['note']}"
            rows.append(row)
        sections.append("COMPILED ACCEPTANCE CHECKS (correctness atoms)\n" + "\n".join(rows))
    if step["error_modes"]:
        sections.append("COMPILED ERROR MODES (defect atoms)\n" + "\n".join(
            f"- [{e.get('severity') or 'major'}] {e.get('statement')}"
            for e in step["error_modes"]))

    fit, why = src.photo_fit(step)
    sections.append(f"PHOTO ASSESSMENT FIT\n{fit} — {why}")
    if step.get("frame"):
        sections.append("THE FRAME THIS WILL BE GRADED AGAINST\n"
                        f"{step['frame']}\nChosen by: {step['frame_basis']}. This is a "
                        "suggestion from an even-pace guess, not a reviewed interval, so it "
                        "is likely to catch the work mid-action.")
    return "\n\n".join(sections)


def build_entry(task: dict, step: dict, drafted: dict) -> dict:
    fit, why = src.photo_fit(step)
    points = dedupe([p for p in (normalize(p) for p in drafted.get("grading_points") or []) if p])
    mistakes = dedupe([m for m in (normalize(m) for m in drafted.get("critical_mistakes") or [])
                       if m])

    # A step no compiled check calls photo-observable cannot be graded from a
    # frame at all. Saying that plainly beats issuing points a grader would have
    # to guess at — and beats silently dropping the step, which reads as
    # "nothing to check here".
    if fit == "none":
        points, mistakes = [], []

    limits = normalize(drafted.get("frame_limits") or "")
    if limits.lower() in {"none", "null", "n/a"}:
        limits = ""

    return {
        "task_code": task["task_code"],
        "task_title": task["task_title"],
        "subtask_id": step["subtask_id"],
        "subtask_title": step["subtask_title"],
        "step_id": step["step_id"],
        "step_text": step["text"],
        "photo_fit": fit,
        "photo_fit_reason": why,
        "grading_points": points[:3],
        "critical_mistakes": mistakes[:3],
        "unverified_rule": src.UNVERIFIED_RULE,
        "frame": step.get("frame"),
        "frame_basis": step.get("frame_basis"),
        "frame_limits": limits,
        "correctness_atoms": [c.get("id") for c in step["photo_checks"] if c.get("id")],
        "excluded_atoms": [{"id": c.get("id"), "observable": c.get("observable"),
                            "statement": c.get("statement")} for c in step["other_checks"]],
        "defect_atoms": [e.get("id") for e in step["error_modes"] if e.get("id")],
    }


def body_words(entry: dict) -> int:
    """Words in the graded part — the points and the mistakes, not the header."""
    text = " ".join(entry["grading_points"] + entry["critical_mistakes"])
    return sum(1 for word in re.split(r"\s+", text) if re.search(r"[A-Za-z0-9]", word))


def draft_step(task: dict, step: dict, model: str, attempts: int = 3) -> dict:
    fit, _ = src.photo_fit(step)
    if fit == "none":
        # Nothing to ask a model. The step is recorded with its reason and no
        # grading points, which is the honest output.
        return {**build_entry(task, step, {}), "_cost": 0.0,
                "_raw": {"grading_points": [], "critical_mistakes": [], "frame_limits": None}}

    bundle = source_bundle(task, step)
    label = f"{step['step_id']} — {step['text']}"
    adjust, spend, last = None, 0.0, {}

    for attempt in range(attempts):
        result = vlm.draft_step_rubric(model=model, sources=bundle, step=label, adjust=adjust)
        spend += result.get("cost_usd") or 0.0
        if result.get("error"):
            if attempt < attempts - 1:
                time.sleep(2 * (attempt + 1))
                continue
            return {"error": result["error"], "message": result.get("message", ""),
                    "_cost": spend}
        last = result
        entry = build_entry(task, step, result)
        words = body_words(entry)
        if words <= WORD_CEILING and entry["grading_points"]:
            entry["_cost"], entry["_raw"] = spend, {
                "grading_points": entry["grading_points"],
                "critical_mistakes": entry["critical_mistakes"],
                "frame_limits": entry["frame_limits"]}
            return entry
        if not entry["grading_points"]:
            adjust = ("You returned no grading points. At least one photo-observable check was "
                      "supplied, so give one to three points drawn from those checks.")
        else:
            adjust = (f"The points and mistakes came to {words} words against a hard ceiling "
                      f"of {WORD_CEILING}. Cut to the {min(2, len(entry['grading_points']))} "
                      "most load-bearing points and two mistakes, each under 14 words.")

    entry = build_entry(task, step, last)
    # Deterministic last resort, so the ceiling always holds.
    while body_words(entry) > WORD_CEILING and len(entry["critical_mistakes"]) > 1:
        entry["critical_mistakes"].pop()
    while body_words(entry) > WORD_CEILING and len(entry["grading_points"]) > 1:
        entry["grading_points"].pop()
    entry["_cost"] = spend
    entry["_raw"] = {"grading_points": entry["grading_points"],
                     "critical_mistakes": entry["critical_mistakes"],
                     "frame_limits": entry["frame_limits"]}
    return entry


# -------------------------------------------------------------------- render


def render(entry: dict) -> str:
    lines = [f"{entry['step_id']} — STEP PHOTO GRADING CRITERIA", ""]
    if entry["grading_points"]:
        lines.append(f"Assess the frame taken at the end of this step. Grade each point "
                     f"independently as PASS or FAIL.")
        lines.append("")
        lines.append("Grading points")
        lines.extend(f"{n}. {text}" for n, text in enumerate(entry["grading_points"], start=1))
        if entry["critical_mistakes"]:
            lines.append("")
            lines.append("Critical mistakes")
            lines.extend(f"- {text}" for text in entry["critical_mistakes"])
        lines.append("")
        lines.append("Overall decision")
        lines.append("PASS requires every grading point to pass and no critical mistake. Mark "
                     f"a point the frame does not show “{entry['unverified_rule']}” "
                     "Judge only visible evidence.")
    else:
        lines.append("NOT PHOTO-ASSESSABLE — no grading points are issued for this step.")
        lines.append(f"Reason: {entry['photo_fit_reason']}.")
        lines.append("")
        lines.append("Do not grade this step from an image. The compiled checks below need "
                     "evidence a frame cannot carry.")
    if entry["excluded_atoms"]:
        lines.append("")
        lines.append("Not settleable from this frame")
        lines.extend(f"- [{a['observable']}] {a['statement']}" for a in entry["excluded_atoms"])
    if entry["frame_limits"]:
        lines.append("")
        lines.append(f"Frame limits: {entry['frame_limits']}")
    return "\n".join(lines) + "\n"


def plain_text(entry: dict) -> str:
    """Standalone .txt, in the header/rule/body shape `inspector/server.py` parses."""
    header = "\n".join([
        f"TASK CODE:    {entry['task_code']}",
        f"TASK TITLE:   {entry['task_title']}",
        f"SUBTASK CODE: {entry['subtask_id'] or '—'}",
        f"SUBTASK:      {entry['subtask_title'] or '—'}",
        f"STEP ID:      {entry['step_id']}",
        f"STEP:         {entry['step_text']}",
        f"PHOTO FIT:    {entry['photo_fit']} — {entry['photo_fit_reason']}",
        f"FRAME:        {entry['frame'] or 'none suggested'}",
        f"FRAME BASIS:  {entry['frame_basis'] or '—'}",
        f"ATOMS:        {len(entry['correctness_atoms'])} photo correctness, "
        f"{len(entry['excluded_atoms'])} non-photo, {len(entry['defect_atoms'])} defect",
        "",
        "Machine-drafted from the compiled pack atoms. Not reviewed by a subject-matter",
        "expert. The frame is a suggestion from an even-pace guess, not a reviewed interval.",
        "",
        RULE,
        "",
    ])
    return header + render(entry)


TASK_HEADER = """\
# {code} — {title}

*Step-level photo grading criteria, generated by `{generator}`. {n} steps, in procedure order.*

> **Machine-drafted, not reviewed.** Grading points come from the compiled pack atoms
> for each step; only checks the pack marked `photo` are used. Frames are suggested by
> an even-pace guess along the reference clip, not by a reviewed segmentation.

"""


def write_outputs(entries: list[dict], tasks: list[dict], model: str) -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    for old in list(OUT_DIR.glob("AM.*.md")) + [p for d in OUT_DIR.iterdir() if d.is_dir()
                                                and d.name != ".raw"
                                                for p in d.glob("*.txt")]:
        old.unlink()

    by_task: dict[str, list[dict]] = {}
    for entry in entries:
        by_task.setdefault(entry["task_code"], []).append(entry)

    for task in tasks:
        code = task["task_code"]
        rows = by_task.get(code) or []
        if not rows:
            continue
        task_dir = OUT_DIR / code
        task_dir.mkdir(parents=True, exist_ok=True)
        body = TASK_HEADER.format(code=code, title=task["task_title"],
                                  generator=GENERATOR, n=len(rows))
        chunks = []
        for entry in rows:
            (task_dir / f"{code}__{entry['step_id']}.txt").write_text(
                plain_text(entry), encoding="utf-8")
            chunks.append(f"## {entry['step_id']} — {entry['subtask_title'] or '—'}\n\n"
                          f"*{entry['step_text']}*\n\n"
                          f"`photo fit: {entry['photo_fit']}` · "
                          f"`frame: {entry['frame'] or 'none'}`\n\n"
                          "```\n" + render(entry) + "```")
        (OUT_DIR / f"{code}.md").write_text(body + "\n\n".join(chunks) + "\n", encoding="utf-8")

    combined = [f"# Step-level photo grading criteria — all tasks\n",
                f"*Generated by `{GENERATOR}` on `{model}`. "
                f"{len(by_task)} task codes · {len(entries)} steps · "
                f"{sum(len(e['grading_points']) for e in entries)} grading points · "
                f"{sum(len(e['critical_mistakes']) for e in entries)} critical mistakes.*\n",
                "> **Machine-drafted, not reviewed.** Grading points are drawn only from pack "
                "checks marked `photo`.\n> Frames are suggested by an even-pace guess along "
                "the reference clip, not by a reviewed segmentation.\n",
                "| Task | Steps | Photo-assessable | Points | Mistakes | With a frame |",
                "|---|--:|--:|--:|--:|--:|"]
    for task in tasks:
        rows = by_task.get(task["task_code"]) or []
        if not rows:
            continue
        combined.append(
            f"| [{task['task_code']}](#{task['task_code']}) | {len(rows)} | "
            f"{sum(1 for e in rows if e['photo_fit'] != 'none')} | "
            f"{sum(len(e['grading_points']) for e in rows)} | "
            f"{sum(len(e['critical_mistakes']) for e in rows)} | "
            f"{sum(1 for e in rows if e['frame'])} |")
    for task in tasks:
        rows = by_task.get(task["task_code"]) or []
        if not rows:
            continue
        combined.append(f'\n<a id="{task["task_code"]}"></a>\n')
        combined.append(f"## {task['task_code']} — {task['task_title']}\n")
        for entry in rows:
            combined.append(f"### {entry['step_id']} — {entry['subtask_title'] or '—'}\n")
            combined.append(f"*{entry['step_text']}* · `photo fit: {entry['photo_fit']}` · "
                            f"`frame: {entry['frame'] or 'none'}`\n")
            combined.append("```\n" + render(entry) + "```\n")
    (OUT_DIR / "all_atoms.md").write_text("\n".join(combined) + "\n", encoding="utf-8")

    store = [{k: v for k, v in entry.items() if not k.startswith("_")} for entry in entries]
    (OUT_DIR / "atoms.json").write_text(json.dumps(store, indent=2, ensure_ascii=False) + "\n",
                                        encoding="utf-8")

    with (OUT_DIR / "frame_mapping.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["task_code", "subtask_id", "step_id", "step_text", "photo_fit",
                         "grading_points", "critical_mistakes", "frame", "frame_basis"])
        for entry in entries:
            writer.writerow([entry["task_code"], entry["subtask_id"] or "", entry["step_id"],
                             entry["step_text"], entry["photo_fit"],
                             len(entry["grading_points"]), len(entry["critical_mistakes"]),
                             entry["frame"] or "", entry["frame_basis"] or ""])


# ---------------------------------------------------------------- validation


CHECKS = [
    "Every task with a pack has step rubrics",
    "Every pack step has a rubric",
    "Every rubric is under 100 words",
    "Every rubric has 1-3 grading points, or states why it has none",
    "Every rubric has at most 3 critical mistakes",
    "Grading points rest only on photo-observable atoms",
    "No rubric uses INSUFFICIENT IMAGE or percentage weights",
    "Every rubric names its task and subtask",
    "Every step has a standalone .txt rubric",
    "Markdown, JSON and .txt agree",
]

ABSENCE = re.compile(r"\bcannot be (?:confirmed|verified|seen|determined)\b|"
                     r"\bnot (?:visible|shown|in frame)\b|\bobscured\b|\boccluded\b", re.I)


def validate(scope: list[str] | None = None) -> tuple[dict, dict, list[dict]]:
    store_path = OUT_DIR / "atoms.json"
    failures: dict[str, list[str]] = {}
    notes: dict[str, list[str]] = {}

    def fail(check, detail):
        failures.setdefault(check, []).append(detail)

    def note(check, detail):
        notes.setdefault(check, []).append(detail)

    if not store_path.exists():
        fail(CHECKS[0], "atoms.json does not exist.")
        return failures, notes, []

    entries = json.loads(store_path.read_text())
    tasks = src.discover()
    covered = {e["task_code"] for e in entries}
    in_scope = set(scope) if scope else {t["task_code"] for t in tasks}
    by_key = {(e["task_code"], e["step_id"]): e for e in entries}

    for task in tasks:
        code = task["task_code"]
        steps = src.pack_steps(code, task["subtasks"])
        if code not in in_scope:
            note(CHECKS[0], f"{code} is outside this run's scope.")
            continue
        if steps and code not in covered:
            fail(CHECKS[0], f"{code} has {len(steps)} pack steps and no rubrics.")
            continue
        for step in steps:
            if (code, step["step_id"]) not in by_key:
                fail(CHECKS[1], f"{code}/{step['step_id']} has no rubric.")

    markdown: dict[str, str] = {}
    for entry in entries:
        code, step_id = entry["task_code"], entry["step_id"]
        label = f"{code}/{step_id}"
        words = body_words(entry)
        rendered = render(entry)

        if words > WORD_CEILING:
            fail(CHECKS[2], f"{label} is {words} words (ceiling {WORD_CEILING}).")
        if entry["grading_points"]:
            if not 1 <= len(entry["grading_points"]) <= 3:
                fail(CHECKS[3], f"{label} has {len(entry['grading_points'])} grading points.")
        elif entry["photo_fit"] != "none":
            fail(CHECKS[3], f"{label} has no grading points but photo fit is "
                            f"'{entry['photo_fit']}'.")
        elif not entry["photo_fit_reason"]:
            fail(CHECKS[3], f"{label} states no reason for having no grading points.")
        if len(entry["critical_mistakes"]) > 3:
            fail(CHECKS[4], f"{label} has {len(entry['critical_mistakes'])} critical mistakes.")

        for mistake in entry["critical_mistakes"]:
            if ABSENCE.search(mistake):
                fail(CHECKS[5], f"{label}: mistake fires on absence — “{mistake[:60]}”")
        if entry["grading_points"] and not entry["correctness_atoms"]:
            note(CHECKS[5], f"{label}: grading points issued but the pack recorded no "
                            "photo-observable check id for this step.")

        blob = " ".join(entry["grading_points"] + entry["critical_mistakes"])
        if "insufficient image" in blob.lower():
            fail(CHECKS[6], f"{label} uses INSUFFICIENT IMAGE.")
        if re.search(r"\b\d+\s*points?\b|\(\s*\d+\s*%\s*\)|\bworth\s+\d+", blob, re.I):
            fail(CHECKS[6], f"{label} carries a weight or point value.")

        if not entry["task_code"] or not entry["step_id"]:
            fail(CHECKS[7], f"{label} is missing a task code or step id.")
        if not entry["subtask_id"]:
            note(CHECKS[7], f"{label} has no subtask id — the pack step matched no subtask.")

        text_path = OUT_DIR / code / f"{code}__{step_id}.txt"
        if not text_path.exists():
            fail(CHECKS[8], f"{text_path.name} is missing.")
        else:
            body = src.read_text(text_path)
            if rendered.strip() not in body:
                fail(CHECKS[9], f"{label}: .txt does not match atoms.json.")
            if code not in markdown:
                markdown[code] = src.read_text(OUT_DIR / f"{code}.md")
            if rendered.strip() not in markdown[code]:
                fail(CHECKS[9], f"{label}: {code}.md does not match atoms.json.")

    return failures, notes, entries


def write_report(failures: dict, notes: dict, entries: list[dict]) -> Path:
    total = sum(len(v) for v in failures.values())
    assessable = sum(1 for e in entries if e["photo_fit"] != "none")
    with_frame = sum(1 for e in entries if e["frame"])
    lines = [
        "# Validation report — step/atom photo grading criteria\n",
        f"*Written by `{GENERATOR}` over `criteria/atoms/`.*\n",
        "Grading points are drawn only from pack checks marked `photo`; a step whose checks "
        "are all measurements, documents or watched actions gets no points and records why. "
        "Word counts cover the grading points and critical mistakes, not the header.\n",
        f"**{'PASS' if total == 0 else 'FAIL'}** — {total} failure(s) across {len(CHECKS)} "
        f"checks, {len(entries)} steps, {assessable} photo-assessable, {with_frame} with a "
        "suggested frame.\n",
        "## Checks\n", "| Check | Result | Detail |", "|---|---|---|"]
    for check in CHECKS:
        problems = failures.get(check) or []
        extra = notes.get(check) or []
        result = f"**FAIL ({len(problems)})**" if problems else "pass"
        detail = (problems[0][:100] + " …" if len(problems) > 1 else
                  problems[0][:100]) if problems else (f"{len(extra)} note(s)" if extra else "—")
        lines.append(f"| {check} | {result} | {detail.replace('|', '/')} |")

    for title, bucket in (("Failures", failures), ("Notes", notes)):
        if not bucket:
            continue
        lines.append(f"\n## {title}\n")
        for check, items in bucket.items():
            lines.append(f"### {check}\n")
            lines.extend(f"- {item}" for item in items[:40])
            if len(items) > 40:
                lines.append(f"- … {len(items) - 40} more")
            lines.append("")

    fits: dict[str, int] = {}
    for entry in entries:
        fits[entry["photo_fit"]] = fits.get(entry["photo_fit"], 0) + 1
    lines.append("\n## Photo assessment fit\n")
    lines.append("| Fit | Steps | Meaning |")
    lines.append("|---|--:|---|")
    for fit, meaning in (("full", "every compiled check is photo-observable"),
                         ("partial", "some checks need a measurement, document or video"),
                         ("none", "no compiled check can be settled from a frame")):
        lines.append(f"| {fit} | {fits.get(fit, 0)} | {meaning} |")

    lines.append("\n## Per task\n")
    lines.append("| Task | Steps | full | partial | none | With frame | Points | Mistakes |")
    lines.append("|---|--:|--:|--:|--:|--:|--:|--:|")
    for code in dict.fromkeys(e["task_code"] for e in entries):
        rows = [e for e in entries if e["task_code"] == code]
        lines.append(
            f"| {code} | {len(rows)} | "
            f"{sum(1 for e in rows if e['photo_fit'] == 'full')} | "
            f"{sum(1 for e in rows if e['photo_fit'] == 'partial')} | "
            f"{sum(1 for e in rows if e['photo_fit'] == 'none')} | "
            f"{sum(1 for e in rows if e['frame'])} | "
            f"{sum(len(e['grading_points']) for e in rows)} | "
            f"{sum(len(e['critical_mistakes']) for e in rows)} |")

    lines.append("\n## What this report does not establish\n")
    lines.append("No subject-matter expert has reviewed these rubrics, and no frame here comes "
                 "from a reviewed segmentation — each is an even-pace guess at where a step "
                 "ends, recorded in `frame_basis`. A step rubric graded against the wrong "
                 "frame produces a confident verdict about work the image does not show, "
                 "which is the failure this pipeline can flag but cannot rule out.")

    path = OUT_DIR / "validation_report.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def run_validation(scope: list[str] | None = None) -> int:
    failures, notes, entries = validate(scope)
    path = write_report(failures, notes, entries)
    total = sum(len(v) for v in failures.values())
    print(f"\nvalidation: {'PASS' if total == 0 else 'FAIL'} — {total} failure(s) · "
          f"{path.relative_to(ROOT)}")
    for check, problems in failures.items():
        print(f"  ! {check}: {len(problems)}")
        for problem in problems[:4]:
            print(f"      {problem}")
    return 1 if total else 0


# ---------------------------------------------------------------------- CLI


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("acs_code", nargs="*")
    parser.add_argument("--all", action="store_true")
    parser.add_argument("--model", default="anthropic/claude-opus-5")
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--reuse", action="store_true")
    parser.add_argument("--validate-only", action="store_true")
    args = parser.parse_args()

    if args.validate_only:
        return run_validation()

    only = args.acs_code or None
    if not only and not args.all and not args.dry_run:
        parser.error("name at least one task code, or pass --all")
    tasks = src.discover(only)

    jobs = []
    for task in tasks:
        steps = src.pack_steps(task["task_code"], task["subtasks"])
        src.assign_frames(task["task_code"], steps)
        task["steps"] = steps
        jobs.extend((task, step) for step in steps)

    if args.dry_run:
        for task in tasks:
            print(f"\n{task['task_code']} — {task['task_title']}")
            for step in task["steps"]:
                fit, why = src.photo_fit(step)
                print(f"  {step['step_id']:<8} [{fit:<7}] {step['subtask_id'] or '—':<28} "
                      f"{step['text'][:52]}")
                print(f"           frame: {step.get('frame') or 'none'}")
                print(f"           basis: {step.get('frame_basis')}")
        print(f"\n{len(tasks)} task codes, {len(jobs)} steps")
        return 0

    if not args.reuse and not vlm.load_api_key():
        parser.error("OPENROUTER_API_KEY is not set (environment or alcor_agents/.env)")

    RAW_DIR.mkdir(parents=True, exist_ok=True)
    print(f"{len(tasks)} task codes, {len(jobs)} steps"
          + (" · re-rendering from saved drafts" if args.reuse
             else f" · drafting on {args.model}"))

    def run(job):
        task, step = job
        raw_path = RAW_DIR / f"{task['task_code']}__{step['step_id']}.json"
        if args.reuse:
            drafted = src.read_json(raw_path)
            if drafted is None:
                drafted = {}
            entry = build_entry(task, step, drafted)
            entry["_cost"] = 0.0
            return entry
        entry = draft_step(task, step, args.model)
        if not entry.get("error"):
            raw_path.write_text(json.dumps({"model": args.model, **entry["_raw"]},
                                           indent=2, ensure_ascii=False) + "\n",
                                encoding="utf-8")
        else:
            entry["_job"] = (task["task_code"], step["step_id"])
        return entry

    entries, failures, spend = [], [], 0.0
    with ThreadPoolExecutor(max_workers=max(1, min(args.workers, len(jobs)))) as pool:
        for entry in pool.map(run, jobs):
            spend += entry.get("_cost") or 0.0
            if entry.get("error"):
                code, step_id = entry.get("_job", ("?", "?"))
                failures.append(f"{code}/{step_id}: {entry['error']}")
                print(f"  ! {failures[-1]}")
                continue
            entries.append(entry)
    if not entries:
        print("\nno rubrics generated")
        return 1

    order = {(t["task_code"], s["step_id"]): (i, j)
             for i, t in enumerate(tasks) for j, s in enumerate(t["steps"])}
    entries.sort(key=lambda e: order.get((e["task_code"], e["step_id"]), (99, 99)))
    write_outputs(entries, tasks, args.model)

    print(f"\n{len(entries)} step rubrics written to {OUT_DIR.relative_to(ROOT)}"
          + (f" · ${spend:.2f}" if spend else ""))
    if failures:
        print(f"{len(failures)} step(s) failed to draft")
    return run_validation(None if args.all else [t["task_code"] for t in tasks])


if __name__ == "__main__":
    raise SystemExit(main())
