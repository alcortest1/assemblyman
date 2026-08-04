#!/usr/bin/env python3
"""Convert the Alcor pilot selections workbook into tidy CSVs.

Reads  data/Alcor_Pilot_AMS_Selections (1).xlsx
Writes data/processed/{task_delivery,tasks,instructors}.csv

Uses only the standard library (an .xlsx is a zip of XML), so it runs with no
dependencies installed. Re-run after the workbook is updated:

    python3 scripts/xlsx_to_csv.py
"""

from __future__ import annotations

import csv
import re
import sys
import zipfile
import xml.etree.ElementTree as ET
from pathlib import Path

M = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"

ROOT = Path(__file__).resolve().parent.parent
XLSX = ROOT / "data" / "Alcor_Pilot_AMS_Selections (1).xlsx"
OUT_DIR = ROOT / "data" / "processed"

# Sheet name -> worksheet part. Sheet order in workbook.xml maps 1:1 to
# worksheets/sheetN.xml for this file.
SHEETS = {"Pilot Overview": 1, "Task Delivery": 2, "Summary": 3}


def col_index(ref: str) -> int:
    """'AB12' -> 27 (zero-based column index)."""
    letters = re.match(r"[A-Z]+", ref).group(0)
    n = 0
    for ch in letters:
        n = n * 26 + ord(ch) - 64
    return n - 1


def read_sheet(zf: zipfile.ZipFile, index: int, shared: list[str]) -> list[list[str]]:
    """Return the sheet as a dense list of rows of strings."""
    sheet = ET.fromstring(zf.read(f"xl/worksheets/sheet{index}.xml"))
    rows = []
    for row in sheet.iter(M + "row"):
        cells: dict[int, str] = {}
        for c in row.findall(M + "c"):
            t = c.get("t")
            v = c.find(M + "v")
            inline = c.find(M + "is")
            if t == "s" and v is not None:
                val = shared[int(v.text)]
            elif inline is not None:
                val = "".join(x.text or "" for x in inline.iter(M + "t"))
            elif v is not None:
                val = v.text or ""
            else:
                val = ""
            if val.strip():
                cells[col_index(c.get("r"))] = val.strip()
        width = max(cells) + 1 if cells else 0
        rows.append([cells.get(i, "") for i in range(width)])
    return rows


def load_shared_strings(zf: zipfile.ZipFile) -> list[str]:
    root = ET.fromstring(zf.read("xl/sharedStrings.xml"))
    return ["".join(t.text or "" for t in si.iter(M + "t")) for si in root]


def urls(cell: str) -> str:
    """Multi-URL cells are newline separated; normalise to ' | '."""
    parts = [p.strip() for p in re.split(r"[\n\r]+", cell) if p.strip()]
    return " | ".join(parts)


def n_urls(cell: str) -> int:
    parts = [p for p in re.split(r"[\n\r]+", cell) if p.strip().startswith("http")]
    return len(parts)


def parse_week_day(cell: str) -> tuple[str, str]:
    """'Week 4; Day 3' -> ('4', '3')."""
    week = re.search(r"Week\s*(\d+)", cell)
    day = re.search(r"Day\s*(\d+)", cell)
    return (week.group(1) if week else "", day.group(1) if day else "")


def as_int(cell: str) -> str:
    """Excel stores these as floats ('1.0'); emit clean integers."""
    try:
        f = float(cell)
    except (TypeError, ValueError):
        return cell
    return str(int(f)) if f.is_integer() else cell


def parse_task_delivery(rows: list[list[str]]) -> list[dict]:
    records: list[dict] = []
    section = ""
    capstone_group = ""

    for row in rows:
        first = row[0] if row else ""
        populated = [c for c in row if c]

        if first.upper().startswith("SECTION"):
            section = "block" if "BLOCK" in first.upper() else "capstone"
            capstone_group = ""
            continue
        if first == "#":  # repeated header row
            continue
        if len(populated) == 1 and "Capstone" in first:
            # e.g. "Ahmad Erakat:  Capstone  (all 11 tasks)"
            capstone_group = first.split(":")[0].strip()
            continue
        if not first or len(row) < 6:
            continue

        cell = lambda i: row[i] if i < len(row) else ""  # noqa: E731
        week, day = parse_week_day(cell(8))
        instructors_raw = cell(5)

        records.append(
            {
                "delivery_type": section,
                "capstone_instructor": capstone_group,
                "task_no": as_int(first),
                "subject": cell(1),
                "block": as_int(cell(2)),
                "acs_code": cell(3),
                "task": cell(4),
                "instructors": instructors_raw,
                "instructor_count": len([p for p in instructors_raw.split(";") if p.strip()]),
                "photo_assessment_fit": cell(6),
                "photo_fit_level": cell(6).split(":")[0].strip(),
                "notes": cell(7),
                "week": week,
                "day": day,
                "doc_procedure_urls": urls(cell(9)),
                "doc_procedure_count": n_urls(cell(9)),
                "first_person_video_urls": urls(cell(10)),
                "first_person_video_count": n_urls(cell(10)),
                "trainwithaim_video_url": urls(cell(11)),
            }
        )
    return records


def parse_instructors(rows: list[list[str]]) -> list[dict]:
    """The Summary tab lists instructor -> role in columns D/E."""
    out = []
    for row in rows:
        name = row[4] if len(row) > 4 else ""
        role = row[5] if len(row) > 5 else ""
        if not name or not role or name == "Instructor":
            continue
        if name.startswith("Capstone instructors"):  # trailing footnote
            continue
        kind, _, scope = role.partition(":")
        out.append(
            {
                "instructor": name,
                "role": role,
                "role_type": kind.strip().lower(),
                "coverage": scope.strip(),
            }
        )
    return out


def write_csv(path: Path, rows: list[dict]) -> None:
    if not rows:
        print(f"  skipped {path.name} (no rows)")
        return
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)
    print(f"  wrote {path.relative_to(ROOT)} ({len(rows)} rows)")


def main() -> int:
    if not XLSX.exists():
        print(f"missing workbook: {XLSX}", file=sys.stderr)
        return 1

    with zipfile.ZipFile(XLSX) as zf:
        shared = load_shared_strings(zf)
        delivery_rows = read_sheet(zf, SHEETS["Task Delivery"], shared)
        summary_rows = read_sheet(zf, SHEETS["Summary"], shared)

    deliveries = parse_task_delivery(delivery_rows)

    # One row per distinct ACS task, taken from the block-delivery section
    # (it carries the real block instructors; capstone rows repeat the task).
    task_cols = [
        "task_no", "subject", "block", "acs_code", "task", "instructors",
        "photo_assessment_fit", "photo_fit_level", "notes", "week", "day",
        "doc_procedure_urls", "doc_procedure_count",
        "first_person_video_urls", "first_person_video_count",
        "trainwithaim_video_url",
    ]
    tasks = [
        {k: r[k] for k in task_cols}
        for r in deliveries
        if r["delivery_type"] == "block"
    ]

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    write_csv(OUT_DIR / "task_delivery.csv", deliveries)
    write_csv(OUT_DIR / "tasks.csv", tasks)
    write_csv(OUT_DIR / "instructors.csv", parse_instructors(summary_rows))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
