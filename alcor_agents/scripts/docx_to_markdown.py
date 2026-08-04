#!/usr/bin/env python3
"""Convert the AIM procedure .docx skill sheets to Markdown.

Reads  data/drive-download-aim-procedures-confidential/*.docx
Writes data/processed/procedures/*.md  (+ index.md)

Stdlib only (a .docx is a zip of XML) so it runs with no dependencies:

    python3 scripts/docx_to_markdown.py

Structure recovered from the source documents:
  * first paragraph            -> H1 title
  * bold paragraph ending ':'  -> H2 section  ("Before You Begin:", "Cut the Tubing:")
  * plain paragraph ending ':' -> H3 label    ("Step Instructions:", "Senior Mechanic Notes:")
  * w:pStyle=ListParagraph     -> list item, ordered/bulleted per word/numbering.xml
Bold and italic runs are preserved. Trailing digits inside step text (e.g.
"Deburr the tubing ends2-3.") are cross-references to the numbered Senior
Mechanic Notes and are left verbatim; a header note explains the convention.
"""

from __future__ import annotations

import re
import sys
import zipfile
import xml.etree.ElementTree as ET
from pathlib import Path

W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"

ROOT = Path(__file__).resolve().parent.parent
SRC_DIR = ROOT / "data" / "drive-download-aim-procedures-confidential"
OUT_DIR = ROOT / "data" / "processed" / "procedures"

ACS_RE = re.compile(r"^(AM\.[IVX]+\.[A-Z]+\.S\d+)")
HEADER_NOTE = (
    "> Converted from the source .docx skill sheet. Trailing digits in step text "
    "(e.g. `ends2-3`) are cross-references to the numbered **Senior Mechanic Notes** "
    "in the same section, and are preserved verbatim."
)


def numbering_formats(zf: zipfile.ZipFile) -> dict[str, str]:
    """numId -> numFmt ('decimal', 'bullet', ...) for the list's first level."""
    try:
        root = ET.fromstring(zf.read("word/numbering.xml"))
    except KeyError:
        return {}
    abstract: dict[str, str] = {}
    for a in root.findall(W + "abstractNum"):
        lvl = a.find(W + "lvl")
        fmt = "decimal"
        if lvl is not None and lvl.find(W + "numFmt") is not None:
            fmt = lvl.find(W + "numFmt").get(W + "val")
        abstract[a.get(W + "abstractNumId")] = fmt
    out = {}
    for num in root.findall(W + "num"):
        ref = num.find(W + "abstractNumId")
        if ref is not None:
            out[num.get(W + "numId")] = abstract.get(ref.get(W + "val"), "decimal")
    return out


def escape(text: str) -> str:
    """Escape the few markdown metacharacters that appear in this content."""
    return re.sub(r"(?<!\\)([*_`])", r"\\\1", text)


def run_text(run: ET.Element) -> str:
    """Inline text for one run, with bold/italic markers applied."""
    parts = []
    for node in run:
        if node.tag == W + "t":
            parts.append(node.text or "")
        elif node.tag == W + "tab":
            parts.append(" ")
        elif node.tag in (W + "br", W + "cr"):
            parts.append(" ")
    text = "".join(parts)
    if not text.strip():
        return text

    rpr = run.find(W + "rPr")
    bold = rpr is not None and rpr.find(W + "b") is not None
    italic = rpr is not None and rpr.find(W + "i") is not None
    lead = len(text) - len(text.lstrip())
    trail = len(text) - len(text.rstrip())
    core = escape(text.strip())
    if bold:
        core = f"**{core}**"
    if italic:
        core = f"_{core}_"
    return text[:lead] + core + text[len(text) - trail :] if trail else text[:lead] + core


def paragraph(p: ET.Element) -> dict:
    """Flatten a w:p into {text, raw, bold, style, num_id}."""
    raw_parts, md_parts, bold_flags = [], [], []
    # Runs can be nested inside hyperlinks / smartTags; iter() catches those too.
    for run in p.iter(W + "r"):
        raw = "".join(t.text or "" for t in run.findall(W + "t"))
        raw_parts.append(raw)
        md_parts.append(run_text(run))
        if raw.strip():
            rpr = run.find(W + "rPr")
            bold_flags.append(rpr is not None and rpr.find(W + "b") is not None)

    ppr = p.find(W + "pPr")
    style = num_id = None
    if ppr is not None:
        pstyle = ppr.find(W + "pStyle")
        style = pstyle.get(W + "val") if pstyle is not None else None
        numpr = ppr.find(W + "numPr")
        if numpr is not None and numpr.find(W + "numId") is not None:
            num_id = numpr.find(W + "numId").get(W + "val")

    return {
        "raw": "".join(raw_parts).strip(),
        "md": "".join(md_parts).strip(),
        "bold": bool(bold_flags) and all(bold_flags),
        "style": style,
        "num_id": num_id,
    }


def convert(path: Path) -> str:
    zf = zipfile.ZipFile(path)
    fmts = numbering_formats(zf)
    body = ET.fromstring(zf.read("word/document.xml")).find(W + "body")
    paras = [paragraph(p) for p in body.findall(W + "p")]
    paras = [p for p in paras if p["raw"]]

    lines: list[str] = []
    title_done = False
    list_num_id: str | None = None
    list_counter = 0

    def close_list() -> None:
        nonlocal list_num_id, list_counter
        list_num_id, list_counter = None, 0

    for para in paras:
        text, raw = para["md"], para["raw"]

        if para["style"] == "ListParagraph" and para["num_id"]:
            if para["num_id"] != list_num_id:
                close_list()
                list_num_id = para["num_id"]
                if lines and lines[-1] != "":
                    lines.append("")
            ordered = fmts.get(para["num_id"], "decimal") != "bullet"
            list_counter += 1
            marker = f"{list_counter}." if ordered else "-"
            # Strip the bold markers Word sometimes carries into list items.
            lines.append(f"{marker} {text}")
            continue

        close_list()
        if lines and lines[-1] != "":
            lines.append("")

        if not title_done:
            lines.append(f"# {raw}")
            lines.append("")
            lines.append(HEADER_NOTE)
            title_done = True
        elif raw.endswith(":") and len(raw) < 80:
            level = "##" if para["bold"] else "###"
            lines.append(f"{level} {raw.rstrip(':')}")
        else:
            lines.append(text)

    md = "\n".join(lines).strip() + "\n"
    return re.sub(r"\n{3,}", "\n\n", md)


def slug(path: Path) -> str:
    name = path.stem
    name = name.replace("–", "-").replace("—", "-").replace("’", "")
    name = re.sub(r"[^A-Za-z0-9]+", "_", name).strip("_")
    return re.sub(r"_+", "_", name) + ".md"


def main() -> int:
    if not SRC_DIR.is_dir():
        print(f"missing source directory: {SRC_DIR}", file=sys.stderr)
        return 1

    sources = sorted(SRC_DIR.glob("*.docx"))
    if not sources:
        print(f"no .docx files in {SRC_DIR}", file=sys.stderr)
        return 1

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    index: list[tuple[str, str, str]] = []

    for src in sources:
        md = convert(src)
        out = OUT_DIR / slug(src)
        out.write_text(md, encoding="utf-8")
        acs = ACS_RE.match(src.stem.replace("–", "-"))
        title = md.splitlines()[0].lstrip("# ").strip()
        index.append((acs.group(1) if acs else "", title, out.name))
        print(f"  wrote {out.relative_to(ROOT)} ({len(md.splitlines())} lines)")

    lines = ["# AIM Procedure Skill Sheets", "", "| ACS code | Procedure | File |", "| --- | --- | --- |"]
    for acs, title, fname in sorted(index):
        lines.append(f"| {acs} | {title} | [{fname}]({fname}) |")
    (OUT_DIR / "index.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"  wrote {(OUT_DIR / 'index.md').relative_to(ROOT)} ({len(index)} procedures)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
