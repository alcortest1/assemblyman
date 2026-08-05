#!/usr/bin/env python3
"""Shared helpers: map FAA handbook page labels to PDF page indices.

The procedure sheets cite handbook pages by their printed chapter-relative
label ("pages 9-92 to 9-94 from chapter 9 in the FAA-H-8083-31B"), which is not
the PDF page index. The printed label appears in the page footer, so it lands at
the END of the extracted text — that position is what distinguishes a real
footer from a body reference like "Figure 9-92".

Building an index scans every page (~75s for a 1,000-page handbook), so results
are cached under data/processed/handbook_index/<key>.json.

    python3 packs/handbook_index.py --build            # index every known handbook
    python3 packs/handbook_index.py --lookup 31B 9-92
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import warnings
from pathlib import Path

warnings.filterwarnings("ignore")

ROOT = Path(__file__).resolve().parent.parent
CACHE_DIR = ROOT / "data" / "processed" / "handbook_index"

# Short key -> (pdf path, official designation, human title)
HANDBOOKS = {
    "31B": (
        ROOT / "data" / "FAA-H-8083-31B_Aviation_Maintenance_Technician_Handbook.pdf",
        "FAA-H-8083-31B",
        "Aviation Maintenance Technician Handbook - Airframe",
    ),
    "30B": (
        ROOT / "data" / "amtg_handbook.pdf",
        "FAA-H-8083-30B",
        "Aviation Maintenance Technician Handbook - General",
    ),
    "32B": (
        ROOT / "data" / "amt_powerplant_handbook.pdf",
        "FAA-H-8083-32B",
        "Aviation Maintenance Technician Handbook - Powerplant",
    ),
}

# Printed labels look like "9-92" (chapter-page).
LABEL_RE = re.compile(r"\b(\d{1,2})-(\d{1,3})\b")
WINDOW = 60  # chars at the edge of a page where the printed label lives

# Text extraction does not preserve visual layout, so the printed page label
# lands at the END of the text for some handbooks (31B) and at the START for
# others (30B, 32B). Detect which per handbook rather than assuming.


def _label_in(text: str, position: str) -> str | None:
    window = text[-WINDOW:] if position == "tail" else text[:WINDOW]
    match = None
    for match in LABEL_RE.finditer(window):
        if position == "head":
            break  # first label in the header window
    if not match:
        return None
    return f"{int(match.group(1))}-{int(match.group(2))}"


def detect_position(texts: list[str]) -> str:
    """Pick head or tail by whichever yields more distinct labels."""
    counts = {}
    for position in ("tail", "head"):
        seen = set()
        for text in texts:
            label = _label_in(text, position)
            if label:
                seen.add(label)
        counts[position] = len(seen)
    return max(counts, key=counts.get)


def page_texts(pdf_path: Path):
    from pypdf import PdfReader

    reader = PdfReader(str(pdf_path))
    for i, page in enumerate(reader.pages):
        try:
            yield i, page.extract_text() or ""
        except Exception:  # a few pages in these scans fail to decode
            yield i, ""


def build_index(key: str, force: bool = False) -> dict:
    pdf_path, designation, title = HANDBOOKS[key]
    cache = CACHE_DIR / f"{key}.json"
    if cache.exists() and not force:
        return json.loads(cache.read_text())

    if not pdf_path.exists():
        raise FileNotFoundError(pdf_path)

    texts = [text for _, text in page_texts(pdf_path)]
    position = detect_position(texts)

    labels: dict[str, int] = {}
    for i, text in enumerate(texts):
        label = _label_in(text, position)
        if label:
            labels.setdefault(label, i)  # first occurrence wins

    index = {
        "key": key,
        "designation": designation,
        "title": title,
        "pdf": str(pdf_path.relative_to(ROOT)),
        "page_count": len(texts),
        "label_position": position,
        "labels": labels,
    }
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cache.write_text(json.dumps(index, indent=1, sort_keys=True))
    return index


def expand_range(start: str, end: str) -> list[str]:
    """'9-92', '9-94' -> ['9-92', '9-93', '9-94'] (same chapter only)."""
    sc, sp = (int(x) for x in start.split("-"))
    ec, ep = (int(x) for x in end.split("-"))
    if sc != ec:
        raise ValueError(f"cross-chapter range not supported: {start}..{end}")
    return [f"{sc}-{p}" for p in range(sp, ep + 1)]


def resolve(key: str, labels: list[str]) -> list[tuple[str, int | None]]:
    index = build_index(key)
    return [(lab, index["labels"].get(lab)) for lab in labels]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--build", action="store_true")
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--lookup", nargs=2, metavar=("KEY", "LABEL"))
    args = ap.parse_args()

    if args.lookup:
        key, label = args.lookup
        for lab, idx in resolve(key, [label]):
            print(f"{key} {lab} -> pdf page index {idx}")
        return 0

    if args.build:
        for key in HANDBOOKS:
            pdf, designation, _ = HANDBOOKS[key]
            if not pdf.exists():
                print(f"  {key}: missing {pdf.name}, skipped")
                continue
            index = build_index(key, force=args.force)
            print(f"  {key} ({designation}): {index['page_count']} pages, "
                  f"{len(index['labels'])} labels -> {CACHE_DIR.name}/{key}.json")
        return 0

    ap.print_help()
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
