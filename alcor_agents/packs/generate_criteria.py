#!/usr/bin/env python3
"""Generate a VLM grading rubric for every subtask of every aviation-maintenance task.

    python3 packs/generate_criteria.py --dry-run       # what would be graded, no calls
    python3 packs/generate_criteria.py --all           # draft every task
    python3 packs/generate_criteria.py AM.I.D.S1       # one task
    python3 packs/generate_criteria.py --all --reuse   # re-render from the last drafts
    python3 packs/generate_criteria.py --validate-only # re-run the checks over the outputs

Outputs, all regenerated from scratch on every run:

    criteria/<TASK_CODE>/<TASK_CODE>__<subtask_id>.txt   one folder per task code,
                                                  one plain-text rubric per subtask
    criteria/generated_criteria/<TASK_CODE>.md    every rubric for a task, in procedure order
    criteria/generated_criteria/criteria.json     the machine-readable store
    criteria/generated_criteria/source_mapping.csv  subtask -> procedure section -> manual section
    criteria/generated_criteria/validation_report.md

The subtask list, the manual citations and the rendered template are all
deterministic — `criteria_sources.py` derives them with no model involved. The
model is asked for one thing only: the wording of the criteria and the critical
defects, grounded in the procedure text and the manual pages put in front of it.

**Machine-drafted, not reviewed.** No subject-matter expert has seen any of this
output. A rubric here is a proposal about what a photograph can settle, and the
validation report records where that proposal is weakest.
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

import criteria_lint  # noqa: E402  (same directory)
import criteria_sources as src  # noqa: E402
import vlm  # noqa: E402

GENERATOR = "packs/generate_criteria.py"
RAW_DIR = src.OUT_DIR / ".raw"

# Criteria this near-identical to one already in the same rubric are the same
# criterion in different words, and a grader asked both would double-count it.
DUPLICATE_RATIO = 0.90

# Source notes this file writes itself, recognised on the way back in. A saved
# draft is replayed through `build_entry`, so without this the generator's own
# notes are re-read as if the model had written them and a second copy is
# appended on every `--reuse` — the provenance sentence compounding once per run
# while looking like the model said it twice.
GENERATOR_NOTE = re.compile(
    r"not cited by the procedure sheet|duplicate statement\(s\) removed|"
    r"citation\(s\) not among the pages supplied|"
    r"trailing statement\(s\) removed", re.I)


# ------------------------------------------------------------------ drafting


def subtask_prompt(subtask: dict) -> str:
    return f"{subtask['id']} — {subtask['title']}"


def normalize(text: str) -> str:
    text = re.sub(r"^\s*(?:[-*•]|\d+[.)])\s*", "", str(text or "").strip())
    return re.sub(r"\s+", " ", text).strip().rstrip(";")


def dedupe(items: list[str]) -> tuple[list[str], list[str]]:
    """Drop restatements, keeping the first wording. Returns (kept, dropped)."""
    kept: list[str] = []
    dropped: list[str] = []
    for item in items:
        key = re.sub(r"[^a-z0-9 ]", "", item.lower())
        if any(SequenceMatcher(None, key, re.sub(r"[^a-z0-9 ]", "", k.lower())).ratio()
               > DUPLICATE_RATIO for k in kept):
            dropped.append(item)
            continue
        kept.append(item)
    return kept, dropped


def citation_matches(manual: dict, claimed: str) -> bool:
    """Whether a claimed citation names a page that was actually supplied.

    Compared on the handbook designation and the printed page label rather than
    on the string, because the model writes the same page a dozen ways —
    "FAA-H-8083-30B ch. 9, p. 9-2 (Tube Cutting)" against the supplied
    "FAA-H-8083-30B ch. 9 p. 9-2". Substring matching failed on the comma alone,
    which silently discarded sound citations and left rubrics reading "Manual:
    none cited" while their notes reported dropping the very page they rest on.
    """
    text = claimed.lower()
    designation = re.search(r"faa-h-\d+-\d+[a-z]?", manual["handbook"].lower())
    if designation and designation.group(0) not in text.replace(" ", ""):
        if designation.group(0) not in text:
            return False
    label = str(manual.get("label") or "").lower()
    if not label:
        return True
    return bool(re.search(rf"(?<![\w-]){re.escape(label)}(?![\w-])", text))


def build_entry(task: dict, subtask: dict, drafted: dict) -> dict:
    criteria = [normalize(c) for c in drafted.get("criteria") or []]
    defects = [normalize(d) for d in drafted.get("critical_defects") or []]
    criteria, dropped_criteria = dedupe([c for c in criteria if c])
    defects, dropped_defects = dedupe([d for d in defects if d])

    # The procedure section is always a source — the rubric grades the sheet's
    # own work — so it is asserted here rather than trusted to the reply. The
    # manual list is intersected with what was actually in the prompt, so a page
    # the model never saw cannot appear in the source basis.
    procedure_sources = [f"{task['procedure_file']} § {subtask['title']}"]
    for extra in drafted.get("procedure_sources") or []:
        text = normalize(extra)
        # The model restates the section it was given far more often than it
        # names a second one, and two spellings of the same citation in the
        # source basis read as two sources.
        if text and task["task_code"] in text and subtask["title"].lower() not in text.lower():
            procedure_sources.append(text)

    manual_sources, unsupported = [], []
    for claimed in drafted.get("manual_sources") or []:
        text = normalize(claimed)
        if not text:
            continue
        match = next((m["citation"] for m in subtask["manuals"]
                      if citation_matches(m, text)), None)
        if match and match not in manual_sources:
            manual_sources.append(match)
        elif not match:
            unsupported.append(text)

    notes = [normalize(n) for n in drafted.get("source_notes") or []]
    notes = [n for n in notes if n and n.lower() not in {"none", "none.", "no conflicts"}
             and not GENERATOR_NOTE.search(n)]
    if unsupported:
        notes.append("Manual citation(s) not among the pages supplied were dropped: "
                     + "; ".join(unsupported))
    if dropped_criteria or dropped_defects:
        notes.append(f"{len(dropped_criteria) + len(dropped_defects)} duplicate "
                     "statement(s) removed.")

    # One note for all provisional pages, not one per page. Three citations from
    # the same un-cited extract produced three identical sentences, which on
    # AM.I.E.S1's turnbuckle rubric pushed it past the 300-word ceiling on
    # boilerplate alone — and said the same thing the model had already said.
    provisional = [m for m in subtask["manuals"]
                   if m["citation"] in manual_sources and not m["cited_by_source"]]
    if provisional:
        how = {"label": "page label", "search": "content search"}
        methods = sorted({how.get(m["located_by"], m["located_by"]) for m in provisional})
        notes.append(
            f"{'; '.join(m['citation'] for m in provisional)} — located by "
            f"{' and '.join(methods)}, not cited by the procedure sheet; standards taken "
            f"from {'them' if len(provisional) > 1 else 'it'} are provisional.")
    notes, _ = dedupe(notes)

    description = normalize(drafted.get("subtask_description") or "") or subtask["title"].lower()
    return {
        "task_code": task["task_code"],
        "task_title": task["task_title"],
        "subtask_id": subtask["id"],
        "subtask_title": subtask["title"],
        "subtask_description": description,
        "criteria": criteria,
        "critical_defects": defects,
        "overall_rule": src.OVERALL_RULE,
        "unverified_rule": src.UNVERIFIED_RULE,
        "procedure_sources": procedure_sources,
        "manual_sources": manual_sources,
        "source_notes": dict.fromkeys(notes),
    }


def trim_to_ceiling(entry: dict, ceiling: int = 300) -> list[str]:
    """Last-resort deterministic shortening, keeping the template floor intact.

    Only reached when the model has already been asked twice to come in under the
    ceiling. Dropping trailing items is crude, but a rubric that silently exceeds
    the limit is worse: the length band exists so a grader reads the whole thing.
    """
    dropped = []
    while src.word_count(src.render_rubric(entry)) > ceiling and len(entry["critical_defects"]) > 3:
        entry["critical_defects"].pop()
        dropped.append("critical defect")
    while src.word_count(src.render_rubric(entry)) > ceiling and len(entry["criteria"]) > 4:
        entry["criteria"].pop()
        dropped.append("criterion")
    # Source notes go last, and only once the rubric is already at its floor of
    # 4 criteria and 3 defects. Dropping a note loses a recorded limitation, so
    # it is the last thing to go — but a rubric that silently exceeds the
    # ceiling is worse, and without this the loop cannot converge at all.
    notes = list(entry["source_notes"])
    while src.word_count(src.render_rubric(entry)) > ceiling and len(notes) > 1:
        notes.pop()
        entry["source_notes"] = dict.fromkeys(notes)
        dropped.append("source note")
    return dropped


def draft_subtask(task: dict, subtask: dict, model: str, attempts: int = 3) -> dict:
    """One rubric, re-asked until the rendered Markdown lands in the 100–300 band."""
    bundle = src.source_bundle(task["task_code"], task["task_title"], subtask,
                               subtask["manuals"], task["prerequisites"])
    prompt = subtask_prompt(subtask)
    adjust, spend, last = None, 0.0, {}

    for attempt in range(attempts):
        result = vlm.draft_subtask_rubric(model=model, sources=bundle,
                                          subtask=prompt, adjust=adjust)
        spend += result.get("cost_usd") or 0.0
        if result.get("error"):
            if attempt < attempts - 1:
                time.sleep(2 * (attempt + 1))
                continue
            return {"error": result["error"], "message": result.get("message", ""),
                    "cost_usd": spend}
        last = result
        entry = build_entry(task, subtask, result)
        # The model's own reply, kept apart from the built entry so what gets
        # saved for `--reuse` is what the model said, not what this file made of
        # it. Replaying a built entry feeds the generator's own additions back
        # in as if they were the model's.
        entry["_raw"] = {key: result.get(key) for key in
                         ("subtask_description", "criteria", "critical_defects",
                          "procedure_sources", "manual_sources", "source_notes")}
        words = src.word_count(src.render_rubric(entry))
        if 100 <= words <= 300:
            entry["_words"], entry["_cost"] = words, spend
            return entry
        if words > 300:
            adjust = (f"The rendered rubric came to {words} words against a hard ceiling of "
                      "300. Return the same judgement in fewer words: keep 4 criteria and 3 "
                      "critical defects, each under 16 words, and drop the least "
                      "load-bearing ones rather than compressing every line.")
        else:
            adjust = (f"The rendered rubric came to only {words} words against a floor of 100. "
                      "Add the criteria and defects the sources support but you left out, up "
                      "to 6 criteria and 5 defects, naming what is visible in each.")

    entry = build_entry(task, subtask, last)
    entry["_raw"] = {key: last.get(key) for key in
                     ("subtask_description", "criteria", "critical_defects",
                      "procedure_sources", "manual_sources", "source_notes")}
    trimmed = trim_to_ceiling(entry)
    if trimmed:
        entry["source_notes"] = dict.fromkeys(
            list(entry["source_notes"]) + [f"{len(trimmed)} trailing statement(s) removed to "
                                           "meet the 300-word ceiling."])
    entry["_words"] = src.word_count(src.render_rubric(entry))
    entry["_cost"] = spend
    return entry


# -------------------------------------------------------------------- output


def plain_text(entry: dict) -> str:
    """The rubric as a standalone .txt, carrying its own task and subtask codes."""
    body = src.render_rubric(entry)
    body = re.sub(r"^###\s*", "", body).replace("**", "")
    header = "\n".join([
        f"TASK CODE:    {entry['task_code']}",
        f"TASK TITLE:   {entry['task_title']}",
        f"SUBTASK CODE: {entry['subtask_id']}",
        f"SUBTASK:      {entry['subtask_title']}",
        "",
        "Machine-drafted from the procedure sheet and FAA handbook. Not reviewed by a",
        "subject-matter expert.",
        "",
        "=" * 78,
        "",
    ])
    return header + body


MARKDOWN_HEADER = """\
# {code} — {title}

*Generated by `{generator}`. One rubric per procedure subtask, in procedure order.*

> **Machine-drafted, not reviewed.** Every criterion below was proposed by
> `{model}` from `{procedure}` and the FAA handbook extract linked to this task.
> No subject-matter expert has seen it. A passing grade against these rubrics
> tests the pipeline, not a student.

**Subtasks:** {subtasks}

---

"""


COMBINED_HEADER = """\
# VLM grading criteria — all aviation maintenance tasks

*Generated by `{generator}` from the AIM procedure sheets and the FAA handbooks.
{tasks} task codes · {subtasks} subtasks · {criteria} criteria · {defects} critical defects.*

> **Machine-drafted, not reviewed.** Every criterion on this page was proposed by
> `{model}` from the procedure document and the handbook pages linked to its task.
> No subject-matter expert has seen any of it. A passing grade against these
> rubrics tests the pipeline, not a student.

Each rubric grades **one photograph of one completed subtask**. A criterion that the
image does not show is graded `FAIL — not demonstrated in image.`, never excused.
Where the procedure sheet and the handbook disagree, the conflict is recorded in that
rubric's **Notes** and the procedure sheet is followed, since that is what the student
was taught.

## Contents

| Task | Title | Subtasks | Criteria | Defects | Governing manual |
|---|---|--:|--:|--:|---|
{contents}

---

"""


def combined_page(entries: list[dict], tasks: list[dict], model: str) -> str:
    """Every rubric in the dataset on one page, task by task, in procedure order.

    The per-task files answer "what is graded for this task"; this answers "what
    does the whole pilot grade", which is the question a reviewer comparing
    tasks, or an SME booking time to review, actually has.
    """
    by_task: dict[str, list[dict]] = {}
    for entry in entries:
        by_task.setdefault(entry["task_code"], []).append(entry)

    rows, body = [], []
    for task in tasks:
        code = task["task_code"]
        rubrics = by_task.get(code) or []
        if not rubrics:
            continue
        manuals = sorted({m.split(" p. ")[0] for entry in rubrics
                          for m in entry["manual_sources"]})
        rows.append(f"| [{code}](#{code}) | {task['task_title']} | {len(rubrics)} | "
                    f"{sum(len(e['criteria']) for e in rubrics)} | "
                    f"{sum(len(e['critical_defects']) for e in rubrics)} | "
                    f"{'; '.join(manuals) or '—'} |")

        body.append(f'<a id="{code}"></a>\n\n## {code} — {task["task_title"]}\n')
        body.append(f"*Procedure: `{task['procedure_file']}` · "
                    f"{len(rubrics)} subtasks: "
                    + ", ".join(f"`{e['subtask_id']}`" for e in rubrics) + "*\n")
        body.extend(src.render_rubric(entry) for entry in rubrics)
        body.append("---\n")

    header = COMBINED_HEADER.format(
        generator=GENERATOR, model=model, tasks=len(rows), subtasks=len(entries),
        criteria=sum(len(e["criteria"]) for e in entries),
        defects=sum(len(e["critical_defects"]) for e in entries),
        contents="\n".join(rows))
    return header + "\n".join(body).rstrip() + "\n"


def write_outputs(entries: list[dict], tasks: list[dict], model: str) -> None:
    out = src.OUT_DIR
    out.mkdir(parents=True, exist_ok=True)
    criteria_dir = out.parent

    # Stale rubrics from a previous run would otherwise survive a rename — a
    # subtask id that changes leaves its old file behind, and a reviewer has no
    # way to tell the orphan from the current one.
    stale = list(criteria_dir.glob("*__*.txt")) + list(out.glob("AM.*.md"))
    stale += [path for task_dir in criteria_dir.iterdir()
              if task_dir.is_dir() and task_dir != out
              for path in task_dir.glob("*.txt")]
    for old in stale:
        old.unlink()

    by_task: dict[str, list[dict]] = {}
    for entry in entries:
        by_task.setdefault(entry["task_code"], []).append(entry)

    for task in tasks:
        code = task["task_code"]
        rows = by_task.get(code) or []
        if not rows:
            continue
        body = MARKDOWN_HEADER.format(
            code=code, title=task["task_title"], generator=GENERATOR, model=model,
            procedure=task["procedure_file"],
            subtasks=", ".join(f"`{r['subtask_id']}`" for r in rows))
        body += "\n\n".join(src.render_rubric(entry) for entry in rows)
        (out / f"{code}.md").write_text(body.rstrip() + "\n", encoding="utf-8")

        # One folder per task code, each rubric a standalone file inside it. The
        # task code stays in the filename as well as the folder, so a .txt still
        # identifies itself once it has been copied out to a reviewer.
        task_dir = criteria_dir / code
        task_dir.mkdir(parents=True, exist_ok=True)
        for entry in rows:
            (task_dir / f"{code}__{entry['subtask_id']}.txt").write_text(
                plain_text(entry), encoding="utf-8")

    # `subtask_description` is one field beyond the specified structure. It is
    # what the opening sentence of the rubric names, so without it the store
    # cannot regenerate the Markdown it came from — and a machine-readable store
    # that cannot reproduce the human-readable deliverable is not the same
    # artefact in another format, it is a lossy summary of one.
    (out / "all_tasks.md").write_text(combined_page(entries, tasks, model), encoding="utf-8")

    store = [{
        "task_code": e["task_code"],
        "task_title": e["task_title"],
        "subtask_id": e["subtask_id"],
        "subtask_title": e["subtask_title"],
        "subtask_description": e["subtask_description"],
        "criteria": e["criteria"],
        "critical_defects": e["critical_defects"],
        "overall_rule": e["overall_rule"],
        "unverified_rule": e["unverified_rule"],
        "procedure_sources": e["procedure_sources"],
        "manual_sources": e["manual_sources"],
        "source_notes": list(e["source_notes"]),
    } for e in entries]
    (out / "criteria.json").write_text(json.dumps(store, indent=2, ensure_ascii=False) + "\n",
                                       encoding="utf-8")

    with (out / "source_mapping.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["task_code", "subtask_id", "procedure_file", "procedure_section",
                         "manual_file", "manual_section", "confidence", "notes"])
        index = {(t["task_code"], s["id"]): (t, s)
                 for t in tasks for s in t["subtasks"]}
        for entry in store:
            task, subtask = index.get((entry["task_code"], entry["subtask_id"]), (None, None))
            if task is None:
                continue
            cited = {m["citation"] for m in subtask["manuals"]
                     if m["citation"] in entry["manual_sources"]}
            notes = []
            if subtask.get("id_origin") == "reference clip name":
                notes.append("subtask id preserved from reference clip name")
            if subtask.get("continuations"):
                notes.append("merged note-only heading: "
                             + "; ".join(subtask["continuations"]))
            rows = [m for m in subtask["manuals"] if m["citation"] in cited] or [None]
            for manual in rows:
                row_notes = list(notes)
                if manual is None:
                    row_notes.append("no manual section relied on by any criterion")
                    writer.writerow([entry["task_code"], entry["subtask_id"],
                                     task["procedure_file"], subtask["title"],
                                     "", "", "none", " | ".join(row_notes)])
                    continue
                if not manual["cited_by_source"]:
                    row_notes.append(f"located by {manual['located_by']}, not cited by the "
                                     "procedure sheet")
                writer.writerow([entry["task_code"], entry["subtask_id"],
                                 task["procedure_file"], subtask["title"],
                                 manual["file"], manual["citation"],
                                 manual["confidence"], " | ".join(row_notes)])


# ---------------------------------------------------------------------- CLI


def report_discovery(tasks: list[dict]) -> None:
    total = 0
    for task in tasks:
        print(f"\n{task['task_code']} — {task['task_title']}")
        print(f"  procedure: {task['procedure_file']}")
        for reference in task["references"]:
            flag = "cited" if reference["cited_by_source"] else f"{reference['located_by']}"
            print(f"  manual:    {reference['file']} ({flag}, "
                  f"{len(reference['blocks'])} blocks)")
        for subtask in task["subtasks"]:
            total += 1
            manuals = ", ".join(f"{m['citation']} [{m['confidence']}]"
                                for m in subtask["manuals"]) or "none matched"
            print(f"    {subtask['index']}. {subtask['id']:<34} "
                  f"({len(subtask['steps'])} steps) <- {subtask['title']}")
            print(f"       id: {subtask['id_origin']} · manual: {manuals}")
    print(f"\n{len(tasks)} task codes, {total} subtasks")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("acs_code", nargs="*")
    parser.add_argument("--all", action="store_true", help="every discovered task code")
    parser.add_argument("--model", default="anthropic/claude-opus-5")
    parser.add_argument("--workers", type=int, default=6)
    parser.add_argument("--dry-run", action="store_true",
                        help="report subtasks and manual matches; make no calls")
    parser.add_argument("--reuse", action="store_true",
                        help="re-render outputs from the last drafts, with no calls")
    parser.add_argument("--validate-only", action="store_true",
                        help="re-run validation over the existing outputs")
    args = parser.parse_args()

    if args.validate_only:
        return criteria_lint.main([])

    only = args.acs_code or None
    if not only and not args.all and not args.dry_run:
        parser.error("name at least one task code, or pass --all")
    tasks = src.discover(only)
    if not tasks:
        parser.error("no task codes discovered under tasks/")

    if args.dry_run:
        report_discovery(tasks)
        return 0

    if not args.reuse and not vlm.load_api_key():
        parser.error("OPENROUTER_API_KEY is not set (environment or alcor_agents/.env)")

    RAW_DIR.mkdir(parents=True, exist_ok=True)
    jobs = [(task, subtask) for task in tasks for subtask in task["subtasks"]]
    print(f"{len(tasks)} task codes, {len(jobs)} subtasks"
          + (" · re-rendering from saved drafts" if args.reuse
             else f" · drafting on {args.model}"))

    entries: list[dict] = []
    failures: list[str] = []
    spend = 0.0

    def run(job: tuple[dict, dict]) -> dict:
        task, subtask = job
        raw_path = RAW_DIR / f"{task['task_code']}__{subtask['id']}.json"
        if args.reuse:
            drafted = src.read_json(raw_path)
            if not drafted:
                return {"error": "no_saved_draft", "message": str(raw_path),
                        "_job": (task["task_code"], subtask["id"])}
            entry = build_entry(task, subtask, drafted)
            trim_to_ceiling(entry)
            entry["_words"] = src.word_count(src.render_rubric(entry))
            entry["_cost"] = 0.0
            entry["_model"] = drafted.get("model") or "an unrecorded model"
            return entry
        entry = draft_subtask(task, subtask, args.model)
        entry["_model"] = args.model
        if not entry.get("error"):
            raw_path.write_text(json.dumps({"model": args.model, **entry["_raw"]},
                                           indent=2, ensure_ascii=False) + "\n",
                                encoding="utf-8")
        else:
            entry["_job"] = (task["task_code"], subtask["id"])
        return entry

    with ThreadPoolExecutor(max_workers=max(1, min(args.workers, len(jobs)))) as pool:
        for entry in pool.map(run, jobs):
            spend += entry.get("_cost") or entry.get("cost_usd") or 0.0
            if entry.get("error"):
                code, subtask_id = entry.get("_job", ("?", "?"))
                failures.append(f"{code}/{subtask_id}: {entry['error']} "
                                f"{entry.get('message', '')[:80]}")
                print(f"  ! {failures[-1]}")
                continue
            print(f"  {entry['task_code']:<12} {entry['subtask_id']:<34} "
                  f"{len(entry['criteria'])}c/{len(entry['critical_defects'])}d "
                  f"{entry['_words']}w")
            entries.append(entry)

    if not entries:
        print("\nno rubrics generated")
        return 1

    order = {(t["task_code"], s["id"]): (i, j)
             for i, t in enumerate(tasks) for j, s in enumerate(t["subtasks"])}
    entries.sort(key=lambda e: order.get((e["task_code"], e["subtask_id"]), (99, 99)))
    write_outputs(entries, tasks, entries[0].get("_model") or args.model)

    print(f"\n{len(entries)} rubrics written to {src.OUT_DIR.relative_to(ROOT)}"
          + (f" · ${spend:.2f}" if spend else ""))
    if failures:
        print(f"{len(failures)} subtask(s) failed to draft")

    # Coverage is only a fair check over what this run was asked to produce. A
    # single-task run must not report the other ten as missing outputs.
    scope = [] if args.all else ["--scope", *sorted({t["task_code"] for t in tasks})]
    return criteria_lint.main(scope)


if __name__ == "__main__":
    raise SystemExit(main())
