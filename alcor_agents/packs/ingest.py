#!/usr/bin/env python3
"""Compile a task's source material into a pack skeleton.

For one ACS code, combines the workbook row (data/processed/tasks.csv) with the
matching AIM procedure .docx files and writes:

    tasks/<ACS_CODE>/procedure.md   human-readable procedure(s), markdown
    tasks/<ACS_CODE>/steps.json     structured steps + senior-mechanic notes
    tasks/<ACS_CODE>/sources.json   every input with sha256, size and mtime

steps.json is the drafting aid for pack.yaml: it carries each step verbatim
along with the note cross-references embedded in the source text (a trailing
"2-3" on a step points at Senior Mechanic Notes 2 and 3).

    python3 packs/ingest.py AM.I.E.S1
    python3 packs/ingest.py --all
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
import sys
import zipfile
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

from docx_to_markdown import W, convert, numbering_formats, paragraph  # noqa: E402

TASKS_CSV = ROOT / "data" / "processed" / "tasks.csv"
DOCX_DIR = ROOT / "data" / "drive-download-aim-procedures-confidential"
TASK_DIR = ROOT / "tasks"

ACS_RE = re.compile(r"^(AM\.[IVX]+\.[A-Z]+\.S\d+)")
MAX_NOTE_SPAN = 6  # widest "2-7" style cross-reference we will accept


def split_note_ref(text: str, next_note: int) -> tuple[str, list[int]]:
    """Peel the Senior Mechanic Notes cross-reference off the end of a step.

    Steps carry their note reference as bare trailing digits ("Thread the wire
    into the bolt1."). Parsing those digits alone is ambiguous when the step
    text itself ends in a number: "Fill out block 13." is block *1*, note *3*.

    Notes are numbered sequentially in step order, so we disambiguate by only
    accepting a reference that starts at the next unconsumed note number, and
    preferring the longer range form ("2-3") over the single ("2").
    """
    stripped = text.rstrip()
    trailing_dot = stripped.endswith(".")
    body = stripped[:-1].rstrip() if trailing_dot else stripped

    candidates = [f"{next_note}-{hi}" for hi in range(next_note + MAX_NOTE_SPAN, next_note, -1)]
    candidates.append(str(next_note))

    for cand in candidates:
        if body.endswith(cand):
            clean = body[: -len(cand)].rstrip(" ,;")
            if not clean:  # the whole step was digits — not a reference
                continue
            lo, _, hi = cand.partition("-")
            refs = list(range(int(lo), int(hi or lo) + 1))
            return clean + ("." if trailing_dot else ""), refs

    return text, []


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def source_record(path: Path, kind: str) -> dict:
    stat = path.stat()
    return {
        "kind": kind,
        "path": str(path.relative_to(ROOT)),
        "sha256": sha256(path),
        "bytes": stat.st_size,
        "modified": int(stat.st_mtime),
    }


def docx_for(acs_code: str) -> list[Path]:
    """All procedure sheets whose filename starts with this ACS code."""
    out = []
    for path in sorted(DOCX_DIR.glob("*.docx")):
        match = ACS_RE.match(path.stem.replace("–", "-"))
        if match and match.group(1) == acs_code:
            out.append(path)
    return out


def parse_structure(path: Path) -> dict:
    """Split a procedure .docx into sections of steps and senior-mechanic notes."""
    zf = zipfile.ZipFile(path)
    fmts = numbering_formats(zf)
    body = ET.fromstring(zf.read("word/document.xml")).find(W + "body")
    paras = [p for p in (paragraph(p) for p in body.findall(W + "p")) if p["raw"]]

    title = paras[0]["raw"] if paras else path.stem
    sections: list[dict] = []
    section = None
    bucket = None  # "steps" | "notes" | "safety" | "equipment" | "you_need_to"

    def new_section(label: str) -> dict:
        sec = {"section": label, "steps": [], "notes": [],
               "safety": [], "equipment": [], "you_need_to": [], "_next_note": 1}
        sections.append(sec)
        return sec

    # Labels normally end in ':', but the source sheets contain typos (one reads
    # 'Senior Mechanic Notes"'), so match the known labels on their text alone.
    bucket_for = {
        "senior mechanic notes": "notes",
        "step instructions": "steps",
        "safety": "safety",
        "equipment": "equipment",
        "you need to": "you_need_to",
    }

    for para in paras[1:]:
        raw, style = para["raw"], para["style"]
        if style == "ListParagraph":
            label = None
        else:
            label = raw.rstrip(" \t:;.\"'”’’").strip()

        if label is not None and label.lower() in bucket_for:
            bucket = bucket_for[label.lower()]
            continue

        if label is not None and raw.endswith(":") and len(raw) < 80:
            # A bold heading opens a new section ("Cut the Tubing:").
            section = new_section(label)
            bucket = None
            continue

        if style == "ListParagraph" and bucket:
            # Short sheets (the safety-wire variants) carry no bold heading at
            # all — open an implicit section rather than dropping their steps.
            if section is None:
                section = new_section("Procedure")
            text = para["raw"]
            entry: dict = {"text": text}
            if bucket == "steps":
                clean, refs = split_note_ref(text, section["_next_note"])
                entry["text_clean"] = clean
                entry["note_refs"] = refs
                if refs:
                    section["_next_note"] = refs[-1] + 1
            section[bucket].append(entry)

    for sec in sections:
        sec.pop("_next_note", None)  # bookkeeping only

    return {
        "source": str(path.relative_to(ROOT)),
        "title": title,
        "variant": title,
        "sections": sections,
        "step_count": sum(len(s["steps"]) for s in sections),
        "note_count": sum(len(s["notes"]) for s in sections),
    }


def workbook_row(acs_code: str) -> dict:
    for row in csv.DictReader(TASKS_CSV.open(encoding="utf-8")):
        if row["acs_code"] == acs_code:
            return row
    raise KeyError(f"{acs_code} not found in {TASKS_CSV.name}")


def ingest(acs_code: str) -> Path:
    row = workbook_row(acs_code)
    sheets = docx_for(acs_code)
    if not sheets:
        raise FileNotFoundError(f"no .docx procedure sheet for {acs_code}")

    out_dir = TASK_DIR / acs_code
    out_dir.mkdir(parents=True, exist_ok=True)

    # procedure.md — one document, one H1 section per source variant.
    chunks = [f"# {acs_code} — {row['task'].rstrip('.')}", ""]
    chunks.append(
        f"*Subject:* {row['subject']} · *Block:* {row['block']} · "
        f"*Taught:* week {row['week']}, day {row['day']} · "
        f"*Photo-assessment fit:* {row['photo_assessment_fit']}"
    )
    chunks.append("")
    if len(sheets) > 1:
        chunks.append(f"This ACS code is delivered as {len(sheets)} procedure variants.")
        chunks.append("")
    for sheet in sheets:
        chunks.append("---")
        chunks.append("")
        # Demote the sheet's own H1 so procedure.md keeps a single top-level title.
        chunks.append(re.sub(r"^# ", "## ", convert(sheet), count=1))
        chunks.append("")
    (out_dir / "procedure.md").write_text("\n".join(chunks), encoding="utf-8")

    structures = [parse_structure(sheet) for sheet in sheets]
    (out_dir / "steps.json").write_text(
        json.dumps({"acs_code": acs_code, "task": row["task"], "variants": structures},
                   indent=2, ensure_ascii=False),
        encoding="utf-8",
    )

    sources = [source_record(TASKS_CSV, "workbook_csv")]
    sources += [source_record(s, "procedure_docx") for s in sheets]
    workbook = ROOT / "data" / "Alcor_Pilot_AMS_Selections (1).xlsx"
    if workbook.exists():
        sources.insert(0, source_record(workbook, "workbook_xlsx"))
    (out_dir / "sources.json").write_text(
        json.dumps({"acs_code": acs_code, "sources": sources}, indent=2), encoding="utf-8"
    )

    total_steps = sum(s["step_count"] for s in structures)
    total_notes = sum(s["note_count"] for s in structures)
    print(f"  {acs_code}: {len(sheets)} sheet(s), {total_steps} steps, "
          f"{total_notes} notes -> tasks/{acs_code}/")
    return out_dir


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("acs_code", nargs="?")
    ap.add_argument("--all", action="store_true")
    args = ap.parse_args()

    if args.all:
        codes = [r["acs_code"] for r in csv.DictReader(TASKS_CSV.open(encoding="utf-8"))]
    elif args.acs_code:
        codes = [args.acs_code]
    else:
        ap.print_help()
        return 1

    failed = 0
    for code in codes:
        try:
            ingest(code)
        except (FileNotFoundError, KeyError) as exc:
            print(f"  {code}: SKIPPED ({exc})")
            failed += 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
