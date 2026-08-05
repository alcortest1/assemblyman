#!/usr/bin/env python3
"""Gate the generated rubrics, and write `validation_report.md`.

    python3 packs/criteria_lint.py                    # every task in criteria.json
    python3 packs/criteria_lint.py --scope AM.I.D.S1  # coverage limited to one task

Run against the files on disk, not against the generator's own record of what it
did, and re-deriving the subtasks and manual pages from `criteria_sources.py`
rather than trusting the store. That is what lets it catch the failure that
matters most: a criterion resting on a page the model was never shown, or a
measurement that appears in no source at all. Both read exactly like sound work.

Exit status is 1 when any check fails, so this can gate a regeneration.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from difflib import SequenceMatcher
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import criteria_sources as src  # noqa: E402

# A criterion that opens with a bare imperative is grading the action, not the
# artefact, and cannot be answered from a photograph of finished work. The first
# word alone does not settle it — "Cut end shows a continuous opening" and "Bend
# curve is smooth" both open with a word from this set and are both perfectly
# gradeable, because the word is a noun or an adjective there. What separates
# them is a finite verb: a statement about an artefact has one, an instruction
# does not.
STATE_VERBS = re.compile(
    r"\b(?:is|are|was|were|shows?|has|have|sits?|remains?|measures?|appears?|"
    r"aligns?|matches?|extends?|covers?|reads?|carries|contains?|displays?|"
    r"exhibits?|lies?|rests?|spans?|faces?|engages?|seats?|holds?|runs?|"
    r"terminates?|protrudes?|clears?|bears?|falls?|equals?|corresponds?|"
    r"retains?|keeps?|stays?|presents?|exposes?|points?|meets?|passes?|"
    r"crosses?|follows?|forms?|encircles?|surrounds?|ends?|reaches?)\b", re.I)

# What follows the first word settles it. An imperative takes its object
# straight away — "Cut the tubing", "Check the flare" — so a determiner in
# second position marks the instruction. A noun phrase does not: "Cut end
# retains its round cross-section" and "Bend curve is smooth" are both
# descriptions of an artefact that merely start with a word this set contains.
DETERMINERS = {"the", "a", "an", "all", "each", "both", "any", "every", "this",
               "these", "that", "those", "your", "its"}

IMPERATIVE_STARTS = {
    "cut", "bend", "install", "use", "perform", "ensure", "verify", "check",
    "measure", "clean", "remove", "apply", "attach", "tighten", "route",
    "deburr", "flare", "place", "connect", "record", "mark", "confirm",
    "inspect", "observe", "compare", "hold", "slide", "align", "press", "pull",
    "push", "take", "photograph", "show", "demonstrate", "make", "avoid",
    "note", "select", "position", "fit", "torque", "test", "lay", "set",
}

# A critical defect must fire on something present in the frame. These phrasings
# fire on the absence of confirmation instead, which condemns correct work
# whenever the camera angle is poor.
ABSENCE_PATTERNS = [
    r"\bcannot be (?:confirmed|verified|determined|seen|assessed|established)\b",
    r"\bnot (?:visible|shown|demonstrated|confirmed|verifiable|discernible|evident)\b",
    r"\bno evidence\b", r"\bunable to (?:determine|confirm|verify|assess)\b",
    r"\bis unclear\b", r"\bnot in (?:the )?frame\b", r"\bobscured\b", r"\boccluded\b",
    r"\bindeterminate\b", r"\bimpossible to (?:tell|judge|confirm)\b",
]

# The absence of a measuring instrument from the frame. Distinct from the
# absence of required hardware from the work, which is a real defect.
INSTRUMENT_ABSENCE = (
    r"\b(?:no|without any|missing)\b[^.]{0,60}?\b(?:tape measure|ruler|rule|scale|"
    r"gauge|calipers?|template|drawing|straightedge|square|protractor|"
    r"reference (?:object|item))\b")

# Percentage *weights*. A tolerance — "not less than 75 percent of the original
# outside diameter" — is a standard from the handbook and must survive.
WEIGHT_PATTERNS = [
    r"\(\s*\d+\s*%\s*\)", r"\b\d+\s*%\s*(?:weight|of the (?:grade|score|total|mark))\b",
    r"\bworth\s+\d+\b", r"\b\d+\s*points?\b", r"\bweight(?:ed|ing)?\s*[:=]\s*\d+",
    r"\bweighted\s+\d+\s*%",
]

# Phrasings that grade the performance rather than its result.
ACTION_PATTERNS = [
    r"\bthe (?:student|technician|mechanic|candidate|operator)\b",
    r"\bis being\b", r"\bwas (?:used|performed|applied|taken|observed)\b",
    r"\bwere used\b", r"\bwhile (?:the|being)\b", r"\bduring the\b", r"\bin progress\b",
]

NUMBER = re.compile(r"\d{1,3}(?:,\d{3})+|\d+/\d+|\d*\.\d+|\d+")
FRACTION_FORMS = {"1/2": ["½", "half"], "1/4": ["¼", "quarter"], "3/4": ["¾"],
                  "1/3": ["⅓", "third"], "2/3": ["⅔"], "1/8": ["⅛"], "3/8": ["⅜"],
                  "5/8": ["⅝"], "1/16": ["1/16"]}
DUPLICATE_RATIO = 0.90


def normalize_source(text: str) -> str:
    for glyph, plain in (("½", "1/2"), ("¼", "1/4"), ("¾", "3/4"), ("⅓", "1/3"),
                         ("⅔", "2/3"), ("⅛", "1/8"), ("⅜", "3/8"), ("⅝", "5/8"),
                         ("“", '"'), ("”", '"'), ("’", "'"), ("–", "-"), ("—", "-")):
        text = text.replace(glyph, plain)
    return text.lower()


def number_supported(value: str, source: str) -> bool:
    variants = {value}
    if value.startswith("0."):
        variants.add(value[1:])
    if value.startswith("."):
        variants.add("0" + value)
    variants.update(FRACTION_FORMS.get(value, []))
    variants.add(value.replace(",", ""))
    return any(v.lower() in source for v in variants)


class Findings:
    def __init__(self) -> None:
        self.checks: dict[str, list[str]] = {}
        self.info: dict[str, list[str]] = {}

    def fail(self, check: str, detail: str) -> None:
        self.checks.setdefault(check, []).append(detail)

    def note(self, check: str, detail: str) -> None:
        self.info.setdefault(check, []).append(detail)

    @property
    def failures(self) -> int:
        return sum(len(v) for v in self.checks.values())


CHECKS = [
    "Every discovered task code has an output file",
    "Every procedural subtask has a rubric",
    "Every rubric is 100–300 words",
    "No rubric contains percentage weighting",
    "No rubric uses INSUFFICIENT IMAGE",
    "Every criterion is phrased for PASS/FAIL grading",
    "Every critical defect is based on affirmative visible evidence",
    "Each rubric has at least one procedure source",
    "Unsupported exact measurements are not introduced",
    "Duplicate criteria are removed",
    "Criteria do not grade procedural actions",
    "Every rubric follows the required template",
    "Markdown and JSON agree",
    "Every subtask has a standalone .txt rubric",
    "The combined page carries every rubric",
]


def validate(scope: list[str] | None = None) -> tuple[Findings, dict]:
    out = src.OUT_DIR
    store_path = out / "criteria.json"
    findings = Findings()
    if not store_path.exists():
        findings.fail(CHECKS[0], f"`{store_path}` does not exist — nothing to validate.")
        return findings, {"entries": [], "tasks": [], "scope": scope or []}

    entries = json.loads(store_path.read_text())
    tasks = src.discover()
    covered = {e["task_code"] for e in entries}
    in_scope = set(scope) if scope else {t["task_code"] for t in tasks}

    # 1 — coverage
    for task in tasks:
        if task["task_code"] not in in_scope:
            findings.note(CHECKS[0], f"{task['task_code']} is outside this run's scope "
                                     "and was not generated.")
            continue
        if task["task_code"] not in covered:
            findings.fail(CHECKS[0], f"{task['task_code']} has no entry in criteria.json.")
        elif not (out / f"{task['task_code']}.md").exists():
            findings.fail(CHECKS[0], f"{task['task_code']}.md is missing.")

    # 2 — every subtask present
    by_task: dict[str, list[dict]] = {}
    for entry in entries:
        by_task.setdefault(entry["task_code"], []).append(entry)
    subtask_index = {}
    for task in tasks:
        for subtask in task["subtasks"]:
            subtask_index[(task["task_code"], subtask["id"])] = (task, subtask)
        if task["task_code"] not in in_scope:
            continue
        expected = [s["id"] for s in task["subtasks"]]
        got = [e["subtask_id"] for e in by_task.get(task["task_code"]) or []]
        for subtask_id in expected:
            if subtask_id not in got:
                findings.fail(CHECKS[1],
                              f"{task['task_code']}/{subtask_id} has no rubric.")
        if got and got != [s for s in expected if s in got]:
            findings.fail(CHECKS[1], f"{task['task_code']} rubrics are not in procedure order.")

    markdown_cache: dict[str, str] = {}
    words_by_entry: dict[tuple[str, str], int] = {}

    for entry in entries:
        code, subtask_id = entry["task_code"], entry["subtask_id"]
        label = f"{code}/{subtask_id}"
        rendered = src.render_rubric(entry)
        text = " ".join(entry["criteria"] + entry["critical_defects"])
        lowered = normalize_source(text)

        # 3 — length
        words = src.word_count(rendered)
        words_by_entry[(code, subtask_id)] = words
        if not 100 <= words <= 300:
            findings.fail(CHECKS[2], f"{label} is {words} words (needs 100–300).")

        # 4 — percentage weighting
        for pattern in WEIGHT_PATTERNS:
            hit = re.search(pattern, rendered, re.I)
            if hit:
                findings.fail(CHECKS[3], f"{label}: “{hit.group(0)}”")
                break

        # 5 — INSUFFICIENT IMAGE
        if "insufficient image" in rendered.lower():
            findings.fail(CHECKS[4], f"{label} uses INSUFFICIENT IMAGE.")

        # 6 — PASS/FAIL phrasing
        for criterion in entry["criteria"]:
            words = [re.sub(r"[^a-z]", "", w.lower()) for w in criterion.split(" ")[:2]]
            imperative = (words[0] in IMPERATIVE_STARTS
                          and len(words) > 1 and words[1] in DETERMINERS)
            if imperative:
                findings.fail(CHECKS[5], f"{label}: reads as an instruction, not a "
                                         f"description of the finished work — “{criterion[:70]}”")
            elif criterion.rstrip().endswith("?"):
                findings.fail(CHECKS[5], f"{label}: phrased as a question — “{criterion[:70]}”")
            elif not STATE_VERBS.search(criterion):
                findings.note(CHECKS[5], f"{label}: no finite verb found, so it may read as "
                                         f"a fragment rather than a testable statement — "
                                         f"“{criterion[:70]}”")

        # 7 — affirmative defects
        for defect in entry["critical_defects"]:
            for pattern in ABSENCE_PATTERNS:
                hit = re.search(pattern, defect, re.I)
                if hit:
                    findings.fail(CHECKS[6], f"{label}: fires on absence of evidence "
                                             f"(“{hit.group(0)}”) — “{defect[:70]}”")
                    break
            else:
                # A missing measuring instrument is a property of the
                # photograph, not of the work. Condemning it makes a framing
                # problem a critical defect, when the criterion that needs the
                # instrument in frame already fails on its own.
                if re.search(INSTRUMENT_ABSENCE, defect, re.I):
                    findings.fail(CHECKS[6], f"{label}: condemns the photograph rather than "
                                             f"the work — “{defect[:70]}”")
                # Missing hardware is a legitimate defect, but a defect that
                # opens on the absence itself fires just as readily on a badly
                # framed photograph of correct work. Flagged for a reviewer
                # rather than failed, because the phrasing can be sound —
                # "No blue sleeve ring shows at the flare" is the procedure
                # sheet's own go/no-go check for an over-flared end.
                elif re.match(r"^(?:no|nothing|none)\b", defect.strip(), re.I):
                    findings.note(CHECKS[6], f"{label}: opens on an absence — confirm it "
                                             f"names the visible article — “{defect[:70]}”")

        # 8 — procedure source
        if not entry.get("procedure_sources"):
            findings.fail(CHECKS[7], f"{label} cites no procedure source.")

        # 9 — measurements traceable to a source that was actually supplied
        task, subtask = subtask_index.get((code, subtask_id), (None, None))
        if subtask is not None:
            source = normalize_source("\n".join(
                [src.read_text(src.ROOT / task["procedure_file"]), task["prerequisites"]]
                + [m["text"] for m in subtask["manuals"]]))
            unsupported = sorted({n for n in NUMBER.findall(text)
                                  if not number_supported(n, source)})
            if unsupported:
                findings.fail(CHECKS[8], f"{label}: {', '.join(unsupported)} "
                                         "appear in no supplied source.")
            for citation in entry.get("manual_sources") or []:
                if citation not in {m["citation"] for m in subtask["manuals"]}:
                    findings.fail(CHECKS[8], f"{label} cites “{citation}”, which was not "
                                             "among the pages supplied to the model.")
        else:
            findings.fail(CHECKS[1], f"{label} is in criteria.json but not in the procedure.")

        # 10 — duplicates
        keys = [re.sub(r"[^a-z0-9 ]", "", c.lower()) for c in entry["criteria"]]
        for i, left in enumerate(keys):
            for right in keys[i + 1:]:
                if SequenceMatcher(None, left, right).ratio() > DUPLICATE_RATIO:
                    findings.fail(CHECKS[9], f"{label}: near-duplicate criteria — "
                                             f"“{left[:50]}” / “{right[:50]}”")

        # 11 — result, not performance
        for criterion in entry["criteria"]:
            for pattern in ACTION_PATTERNS:
                hit = re.search(pattern, criterion, re.I)
                if hit:
                    findings.fail(CHECKS[10], f"{label}: grades the action "
                                              f"(“{hit.group(0)}”) — “{criterion[:70]}”")
                    break

        # 12 — template
        for marker in ("**Criteria**", "**Critical defects**", "**Overall decision**",
                       "**Source basis**", "— VLM GRADING CRITERIA",
                       "“FAIL — not demonstrated in image.”"):
            if marker not in rendered:
                findings.fail(CHECKS[11], f"{label} is missing `{marker}`.")
        if entry.get("overall_rule") != src.OVERALL_RULE:
            findings.fail(CHECKS[11], f"{label} has a non-standard overall_rule.")
        if entry.get("unverified_rule") != src.UNVERIFIED_RULE:
            findings.fail(CHECKS[11], f"{label} has a non-standard unverified_rule.")
        if not 4 <= len(entry["criteria"]) <= 7:
            findings.note(CHECKS[11], f"{label} has {len(entry['criteria'])} criteria "
                                      "(4–7 expected).")
        if not 3 <= len(entry["critical_defects"]) <= 7:
            findings.note(CHECKS[11], f"{label} has {len(entry['critical_defects'])} "
                                      "critical defects (3–7 expected).")

        # 13 — the Markdown on disk carries this rubric
        if code not in markdown_cache:
            markdown_cache[code] = src.read_text(out / f"{code}.md")
        if rendered.strip() not in markdown_cache[code]:
            findings.fail(CHECKS[12], f"{label} in criteria.json does not match "
                                      f"{code}.md.")

        # 14 — standalone text file
        text_path = out.parent / code / f"{code}__{subtask_id}.txt"
        if not text_path.exists():
            findings.fail(CHECKS[13], f"{text_path.name} is missing.")
        elif subtask_id not in src.read_text(text_path):
            findings.fail(CHECKS[13], f"{text_path.name} does not name its subtask code.")

        # 15 — the combined page. Checked against the same rendered text as the
        # per-task file, so the two cannot drift: a page assembled from a stale
        # run would still look complete on its own.
        if "combined" not in markdown_cache:
            markdown_cache["combined"] = src.read_text(out / "all_tasks.md")
        if not markdown_cache["combined"]:
            if "missing" not in markdown_cache:
                markdown_cache["missing"] = "yes"
                findings.fail(CHECKS[14], "all_tasks.md does not exist.")
        elif rendered.strip() not in markdown_cache["combined"]:
            findings.fail(CHECKS[14], f"{label} is missing from all_tasks.md or differs "
                                      "from the per-task file.")

    return findings, {"entries": entries, "tasks": tasks, "scope": sorted(in_scope),
                      "words": words_by_entry}


REPORT_HEADER = """\
# Validation report — generated grading criteria

*Written by `packs/criteria_lint.py` over the files in `criteria/generated_criteria/`
and `criteria/`. Re-run with `python3 packs/criteria_lint.py`.*

The checks below run against the artefacts on disk. The subtask list and the manual
pages are re-derived from `packs/criteria_sources.py` rather than read back from the
store, so a criterion resting on a page the model never saw, or a measurement that
appears in no source, is caught here rather than believed.

Word counts are of the rendered rubric with Markdown syntax stripped
(`criteria_sources.word_count`).

**{status}** — {failures} failure(s) across {check_count} checks, {rubrics} rubric(s),
{tasks} task code(s) in scope.

"""


def write_report(findings: Findings, context: dict) -> Path:
    entries = context["entries"]
    words = context.get("words") or {}
    lines = [REPORT_HEADER.format(
        status="PASS" if findings.failures == 0 else "FAIL",
        failures=findings.failures, check_count=len(CHECKS),
        rubrics=len(entries), tasks=len(context["scope"]))]

    lines.append("## Checks\n")
    lines.append("| Check | Result | Detail |")
    lines.append("|---|---|---|")
    for check in CHECKS:
        problems = findings.checks.get(check) or []
        notes = findings.info.get(check) or []
        if problems:
            result = f"**FAIL ({len(problems)})**"
            detail = problems[0][:110] + (" …" if len(problems) > 1 else "")
        elif notes:
            result = "pass"
            detail = f"{len(notes)} note(s)"
        else:
            result = "pass"
            detail = "—"
        lines.append(f"| {check} | {result} | {detail.replace('|', '/')} |")

    if findings.checks:
        lines.append("\n## Failures\n")
        for check, problems in findings.checks.items():
            lines.append(f"### {check}\n")
            for problem in problems:
                lines.append(f"- {problem}")
            lines.append("")

    if findings.info:
        lines.append("\n## Notes\n")
        lines.append("Not failures. Recorded so a reviewer can see where the generator "
                     "used judgement.\n")
        for check, notes in findings.info.items():
            lines.append(f"### {check}\n")
            for note in notes:
                lines.append(f"- {note}")
            lines.append("")

    if entries:
        lines.append("\n## Rubrics\n")
        lines.append("| Task | Subtask | Criteria | Critical defects | Words | Manual sources |")
        lines.append("|---|---|--:|--:|--:|---|")
        for entry in entries:
            manuals = "; ".join(entry.get("manual_sources") or []) or "—"
            lines.append(
                f"| {entry['task_code']} | `{entry['subtask_id']}` | "
                f"{len(entry['criteria'])} | {len(entry['critical_defects'])} | "
                f"{words.get((entry['task_code'], entry['subtask_id']), 0)} | {manuals} |")

    lines.append("\n## What this report does not establish\n")
    lines.append("No subject-matter expert has reviewed these rubrics. Every check above is "
                 "structural: it confirms a criterion is *shaped* like something a "
                 "photograph can settle and rests on a source that was actually supplied. "
                 "Whether the criterion is the right one for an airworthy article is a "
                 "judgement none of these checks make.")

    path = src.OUT_DIR / "validation_report.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--scope", nargs="*", default=None,
                        help="limit coverage checking to these task codes")
    args = parser.parse_args(argv)

    findings, context = validate(args.scope)
    path = write_report(findings, context)
    print(f"\nvalidation: {'PASS' if findings.failures == 0 else 'FAIL'} — "
          f"{findings.failures} failure(s) · {path.relative_to(src.ROOT)}")
    for check, problems in findings.checks.items():
        print(f"  ! {check}: {len(problems)}")
        for problem in problems[:4]:
            print(f"      {problem}")
        if len(problems) > 4:
            print(f"      … {len(problems) - 4} more")
    return 1 if findings.failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
