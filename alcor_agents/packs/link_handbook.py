#!/usr/bin/env python3
"""Resolve every pilot task to the handbook pages that govern it.

A photo criterion is only as defensible as the standard behind it, and the
numeric standards — twists per inch, strip lengths, wrap counts, thread
exposure — live in the FAA handbooks rather than in AIM's skill sheets. So
before any criterion can be drafted, each task needs its handbook section
extracted and, crucially, *labelled with how it was found*.

Most sheets say so themselves, in the "You Need to" section:

    Review pages 9-86 to 9-89 from chapter 9 in the FAA-H-8083-31B.

That is a citation by the campus and carries real authority. Three other cases
do not, and conflating them is exactly the failure this file exists to prevent:

  cited        the sheet names an FAA handbook we hold  -> label lookup, authoritative
  unavailable  the sheet cites AC 43.13-1B, absent from data/ -> nearest FAA
               handbook section located by content search, flagged provisional
  uncited      the sheet cites nothing at all -> content search, flagged provisional

Everything not in the first case is written `cited_by_source: false`, which the
drafting prompt (inspector/vlm.py) turns into "treat any standard taken from it
as provisional and say so", and which pack_lint.py requires the pack to carry as
an explicit assumption.

    python3 packs/link_handbook.py                # link every task
    python3 packs/link_handbook.py AM.III.F.S11   # one task
    python3 packs/link_handbook.py --dry-run      # report citations, write nothing
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "packs"))

import handbook_search  # noqa: E402
from extract_handbook import write_reference  # noqa: E402
from handbook_index import HANDBOOKS  # noqa: E402

TASK_DIR = ROOT / "tasks"
TASKS_CSV = ROOT / "data" / "processed" / "tasks.csv"

# How a sheet writes a handbook it wants read. "FAA-H-" is optional because
# AM.I.D.S8 writes only "in the 8083-30B".
FAA_RE = re.compile(r"(?:FAA[-\s]?H[-\s]?)?8083[-\s]?(30B|31B|32B)", re.I)
# AC 43.13-1B is cited by two sheets and is not in data/. Recognised precisely
# so it is reported as a known gap rather than silently ignored.
AC_RE = re.compile(r"AC[-\s]?43\.13[-\s]?1B", re.I)
# A printed page label: chapter-page, as it appears in the handbook footer.
PAGE_RE = re.compile(r"\b(\d{1,2}-\d{1,3})\b")
# How many search-located pages to keep when there is no citation to follow.
SEARCH_WINDOW = 3


def task_rows() -> list[dict]:
    with TASKS_CSV.open() as handle:
        return list(csv.DictReader(handle))


def you_need_to_lines(acs: str) -> list[str]:
    """Every 'You Need to' line in a task's steps.json, across all variants."""
    data = json.loads((TASK_DIR / acs / "steps.json").read_text())
    out = []
    for variant in data.get("variants") or []:
        for section in variant.get("sections") or []:
            for item in section.get("you_need_to") or []:
                text = (item.get("text") or "").strip()
                if text:
                    out.append(text)
    return out


def parse_citations(lines: list[str]) -> list[dict]:
    """Pull handbook citations out of the 'You Need to' prose.

    A single line can name two documents ("...in the FAA-H-8083-30B and page
    9-18 from chapter 9 in the AC-43.13-1B"), so fragments are split on `and`
    before parsing. The document designation is removed from the fragment
    before page labels are scanned, because "8083-30B" itself contains
    something that looks exactly like a page label.
    """
    citations = []
    for line in lines:
        if not (FAA_RE.search(line) or AC_RE.search(line)):
            continue
        for fragment in re.split(r"\band\b", line):
            faa, ac = FAA_RE.search(fragment), AC_RE.search(fragment)
            if not (faa or ac):
                continue
            stripped = (FAA_RE.sub(" ", fragment) if faa else fragment)
            stripped = AC_RE.sub(" ", stripped)
            # "from chapter 9" is not a page; only chapter-page labels survive
            # the pattern, and a bare chapter number cannot match it.
            labels = PAGE_RE.findall(stripped)
            if not labels:
                continue
            citations.append({
                "document": f"FAA-H-8083-{faa.group(1).upper()}" if faa else "AC 43.13-1B",
                "handbook_key": faa.group(1).upper() if faa else None,
                "available": bool(faa),
                "pages_spec": f"{labels[0]}..{labels[-1]}",
                "text": fragment.strip(),
            })
    return citations


def search_query(row: dict, acs: str) -> str:
    """Build a content-search query from the task title and its section names.

    Section headings ("Tie a Hitch Along the Bundle") are the task's own
    vocabulary and discriminate far better than the one-line title alone.
    """
    parts = [row.get("task") or ""]
    try:
        data = json.loads((TASK_DIR / acs / "steps.json").read_text())
        for variant in data.get("variants") or []:
            for section in variant.get("sections") or []:
                name = section.get("section") or ""
                if name and name not in {"Before You Begin", "Safety and Equipment"}:
                    parts.append(name)
    except Exception:
        pass
    return " ".join(parts)


def already_linked(acs: str) -> bool:
    directory = TASK_DIR / acs / "references" / "handbook"
    return directory.is_dir() and any(directory.glob("*.json"))


def link_task(row: dict, dry_run: bool = False) -> dict:
    acs = row["acs_code"]
    citations = parse_citations(you_need_to_lines(acs))
    usable = [c for c in citations if c["available"]]
    unavailable = [c for c in citations if not c["available"]]

    written: list[dict] = []
    notes: list[str] = []

    for citation in usable:
        if dry_run:
            written.append({**citation, "located_by": "label", "cited_by_source": True})
            continue
        try:
            meta = write_reference(
                acs, citation["handbook_key"], pages_spec=citation["pages_spec"],
                cited_by_source=True,
            )
            written.append(meta)
        except LookupError as error:
            # 30B/32B footers do not always survive text extraction, so a label
            # lookup can fail on a citation that is perfectly real. Fall back
            # rather than dropping the task's only authoritative reference.
            notes.append(f"label lookup failed ({error}); fell back to content search")

    for citation in unavailable:
        notes.append(
            f"cites {citation['document']} {citation['pages_spec']}, which is not in data/"
        )

    # Search only when nothing authoritative landed. A task that cites a real
    # handbook *and* an absent AC keeps just the real one: a second, provisional
    # extract of pages already covered adds noise to every drafting prompt
    # without adding a standard. The missing AC stays recorded as a note, which
    # is what carries it into the pack's assumptions.
    if not written:
        subject = row.get("subject") or ""
        key = handbook_search.SUBJECT_HANDBOOK.get(subject)
        hits = handbook_search.search(
            search_query(row, acs), [key] if key else None, top=1, window=SEARCH_WINDOW
        )
        if hits and not dry_run:
            hit = hits[0]
            meta = write_reference(
                acs, hit["handbook"], indices=hit["pdf_indices"],
                index_labels=hit["labels"],
                spec_label=f"content search: {' '.join(hit['matched_terms'][:6])}",
                cited_by_source=False,
            )
            written.append(meta)
        elif hits:
            written.append({"handbook": hits[0]["name"], "labels": hits[0]["labels"],
                            "located_by": "search", "cited_by_source": False})
        elif not written:
            notes.append("no citation and no content-search hit — no handbook reference")

    return {"acs_code": acs, "citations": citations, "references": written, "notes": notes}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("acs_code", nargs="*", help="tasks to link (default: all)")
    parser.add_argument("--dry-run", action="store_true",
                        help="report what would be extracted, write nothing")
    parser.add_argument("--force", action="store_true",
                        help="re-link tasks that already have a handbook reference")
    args = parser.parse_args()

    rows = task_rows()
    if args.acs_code:
        wanted = set(args.acs_code)
        rows = [r for r in rows if r["acs_code"] in wanted]
        missing = wanted - {r["acs_code"] for r in rows}
        if missing:
            parser.error(f"unknown task(s): {', '.join(sorted(missing))}")

    for row in rows:
        # AM.I.E.S1's reference was located and reviewed during hand
        # compilation; re-deriving it would replace a considered choice with a
        # search hit. Existing references are left alone unless asked for.
        if already_linked(row["acs_code"]) and not args.force:
            print(f"\n{row['acs_code']} — already linked, skipping (use --force to redo)")
            continue
        result = link_task(row, args.dry_run)
        print(f"\n{result['acs_code']} — {row.get('task', '')}")
        for citation in result["citations"]:
            mark = "cited" if citation["available"] else "cited but UNAVAILABLE"
            print(f"  {mark}: {citation['document']} {citation['pages_spec']}")
        if not result["citations"]:
            print("  no handbook citation in the procedure sheet")
        for reference in result["references"]:
            pages = ", ".join(p for p in (reference.get("labels") or []) if p)
            flag = "" if reference.get("cited_by_source") else "  [provisional]"
            print(f"  -> {reference.get('handbook')} {pages} "
                  f"via {reference.get('located_by')}{flag}")
        for note in result["notes"]:
            print(f"  ! {note}")

    print(f"\n{len(rows)} task(s) linked" + (" (dry run — nothing written)" if args.dry_run else ""))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
