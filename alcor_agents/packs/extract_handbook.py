#!/usr/bin/env python3
"""Extract the handbook pages a procedure cites into a task pack reference.

Writes tasks/<ACS_CODE>/references/handbook/<handbook>_<range>.md plus a
sidecar .json describing how the pages were located.

Two location modes, because the handbooks differ:

  --pages 9-92..9-94   Label mode. Uses the cached page-label index. Exact, but
                       only works where the printed footer survives text
                       extraction (true for FAA-H-8083-31B, not for 30B/32B).

  --search "safetying" Search mode. Ranks pages by query-term frequency. Used
                       when a label lookup is impossible or when the procedure
                       sheet cites no handbook at all. Results are recorded as
                       located_by="search", which pack_lint.py requires the pack
                       to carry as an explicit assumption.

    python3 packs/extract_handbook.py AM.II.K.S3 --handbook 31B --pages 9-92..9-94
    python3 packs/extract_handbook.py AM.I.E.S1  --handbook 30B --search "safety wire" --window 3
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
sys.path.insert(0, str(ROOT / "packs"))

from handbook_index import HANDBOOKS, build_index, expand_range  # noqa: E402

TASK_DIR = ROOT / "tasks"


_READERS: dict[str, object] = {}


def _reader(key: str):
    """One PdfReader per handbook, kept open.

    Opening a 1,000-page PDF costs a few seconds, and extracting a citation
    range page by page used to pay that per page. Linking every pilot task at
    once turns that into minutes of re-parsing the same three files.
    """
    if key not in _READERS:
        from pypdf import PdfReader

        _READERS[key] = PdfReader(str(HANDBOOKS[key][0]))
    return _READERS[key]


def page_text(key: str, idx: int) -> str:
    try:
        return _reader(key).pages[idx].extract_text() or ""
    except Exception:
        return ""


def page_texts_all(key: str) -> list[str]:
    from pypdf import PdfReader

    reader = PdfReader(str(HANDBOOKS[key][0]))
    out = []
    for page in reader.pages:
        try:
            out.append(page.extract_text() or "")
        except Exception:
            out.append("")
    return out


def locate_by_label(key: str, spec: str) -> tuple[list[int], list[str]]:
    start, _, end = spec.partition("..")
    labels = expand_range(start, end or start)
    index = build_index(key)
    pages, missing = [], []
    for label in labels:
        idx = index["labels"].get(label)
        (pages.append(idx) if idx is not None else missing.append(label))
    if missing:
        raise LookupError(
            f"{key}: no page-label match for {', '.join(missing)}. "
            f"This handbook's footers may not survive text extraction — use --search."
        )
    return pages, labels


def locate_by_search(key: str, query: str, window: int) -> tuple[list[int], list[str]]:
    """Return the best-scoring page and its neighbours."""
    terms = [t for t in re.split(r"\s+", query.lower()) if t]
    texts = page_texts_all(key)
    scored = []
    for i, text in enumerate(texts):
        low = text.lower()
        if not low.strip():
            continue
        score = sum(low.count(t) for t in terms)
        # Require every term to appear, so "safety wire" doesn't match any
        # page that merely says "safety" a lot.
        if all(t in low for t in terms):
            scored.append((score, i))
    if not scored:
        raise LookupError(f"{key}: no page contains all of {terms!r}")
    scored.sort(reverse=True)
    best = scored[0][1]
    pages = [i for i in range(best, best + window) if 0 <= i < len(texts)]
    return pages, [f"pdf-page-{i}" for i in pages]


def write_reference(
    acs_code: str,
    key: str,
    *,
    pages_spec: str | None = None,
    search: str | None = None,
    indices: list[int] | None = None,
    index_labels: list[str | None] | None = None,
    spec_label: str | None = None,
    window: int = 3,
    cited_by_source: bool = False,
) -> dict:
    """Extract a handbook range into a task's references, returning the sidecar.

    Importable so `link_handbook.py` can resolve every pilot task in one pass
    rather than shelling out per citation and reopening the PDFs each time.

    Three ways to say which pages: `pages_spec` for a printed label range,
    `search` for the strict all-terms scan, and `indices` for pages already
    located elsewhere — `handbook_search.search()` ranks on half the terms,
    which is the only thing that finds anything when the query is a task title
    rather than a citation phrase.
    """
    _, designation, title = HANDBOOKS[key]

    if pages_spec:
        pages, labels = locate_by_label(key, pages_spec)
        located_by, spec = "label", pages_spec
    elif indices:
        pages = list(indices)
        labels = [
            (index_labels or [None] * len(pages))[i] or f"pdf-page-{page}"
            for i, page in enumerate(pages)
        ]
        located_by, spec = "search", spec_label or "content search"
    elif search:
        pages, labels = locate_by_search(key, search, window)
        located_by, spec = "search", search
    else:
        raise ValueError("one of pages_spec, indices or search is required")

    out_dir = TASK_DIR / acs_code / "references" / "handbook"
    out_dir.mkdir(parents=True, exist_ok=True)
    slug = re.sub(r"[^a-z0-9]+", "_", f"{designation}_{spec}".lower()).strip("_")

    body = [
        f"# {designation} — {title}",
        "",
        f"*Cited by:* {acs_code} · *Located by:* {located_by} (`{spec}`) · "
        f"*Pages:* {', '.join(labels)}",
        "",
        "> Extracted verbatim from the handbook PDF for offline reference. "
        "FAA handbooks are U.S. Government works in the public domain. "
        "Text is machine-extracted, so figure callouts and tables may be garbled; "
        "the PDF remains the authority.",
        "",
    ]
    if not cited_by_source:
        body.insert(
            4,
            "> **Assumed reference.** The procedure sheet for this task does not cite a "
            "handbook section; this page range was chosen during compilation and must be "
            "confirmed by a subject-matter expert before the pack is marked `reviewed`.\n",
        )

    for label, idx in zip(labels, pages):
        text = page_text(key, idx).strip()
        body += [f"## {label} (PDF page index {idx})", "", "```text", text, "```", ""]

    md_path = out_dir / f"{slug}.md"
    md_path.write_text("\n".join(body), encoding="utf-8")

    meta = {
        "acs_code": acs_code,
        "handbook": designation,
        "handbook_key": key,
        "pdf": str(HANDBOOKS[key][0].relative_to(ROOT)),
        "located_by": located_by,
        "spec": spec,
        "labels": labels,
        "pdf_page_indices": pages,
        "cited_by_source": bool(cited_by_source),
        "file": str(md_path.relative_to(TASK_DIR / acs_code)),
    }
    md_path.with_suffix(".json").write_text(json.dumps(meta, indent=2), encoding="utf-8")
    return meta


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("acs_code")
    ap.add_argument("--handbook", required=True, choices=sorted(HANDBOOKS))
    ap.add_argument("--pages", help="printed label range, e.g. 9-92..9-94")
    ap.add_argument("--search", help="content query, when labels are unavailable")
    ap.add_argument("--window", type=int, default=3, help="pages to keep in search mode")
    ap.add_argument("--cited-by-source", action="store_true",
                    help="the procedure sheet names this reference explicitly")
    args = ap.parse_args()

    if not (args.pages or args.search):
        ap.error("one of --pages or --search is required")

    meta = write_reference(
        args.acs_code, args.handbook, pages_spec=args.pages, search=args.search,
        window=args.window, cited_by_source=args.cited_by_source,
    )
    print(f"  {args.acs_code}: {meta['handbook']} {', '.join(meta['labels'])} "
          f"(pdf idx {meta['pdf_page_indices']}) via {meta['located_by']} "
          f"-> tasks/{args.acs_code}/{meta['file']}")
    if meta["located_by"] == "search":
        print("    ! located by search — pack must record this as an assumption")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
