#!/usr/bin/env python3
"""Find the handbook pages that bear on a task, without reparsing 400 MB of PDF.

`extract_handbook.py` locates pages by printed label, which is exact but needs
someone to already know the citation. Nine of the eleven pilot tasks have no
compiled pack and therefore no citation at all, so criteria for them have to
start by finding the relevant handbook material from the task itself.

Content search over the PDFs is the obvious way to do that and the naive version
is unusable: `page_texts_all` re-extracts every page on each call, which is tens
of seconds per handbook per query. This keeps a plain-text cache — one gzipped
JSON per handbook, built once — so a query is a scan over strings.

    python packs/handbook_search.py --build            # one-time, a few minutes
    python packs/handbook_search.py "safety wire" --handbooks 30B
"""

from __future__ import annotations

import argparse
import gzip
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "packs"))

from handbook_index import HANDBOOKS, build_index  # noqa: E402

CACHE_DIR = ROOT / "data" / "processed" / "handbook_text"

# Which handbook a task's subject belongs to. Searching the right one first
# matters: "safety wire" appears in all three, but the General handbook is the
# one an airframe-and-powerplant General task is actually taught from.
SUBJECT_HANDBOOK = {"General": "30B", "Airframe": "31B", "Powerplant": "32B"}

# Words that appear on nearly every page and would swamp the scoring.
STOPWORDS = {
    "the", "a", "an", "and", "or", "of", "to", "in", "on", "for", "with", "is",
    "are", "be", "as", "at", "by", "from", "that", "this", "it", "if", "any",
    "not", "may", "must", "should", "can", "when", "which", "then", "than",
    "into", "over", "under", "each", "all", "one", "two", "use", "used", "using",
    "aircraft", "maintenance", "figure", "chapter", "page", "handbook",
}


def cache_path(key: str) -> Path:
    return CACHE_DIR / f"{key}.json.gz"


def invert_labels(key: str) -> dict[int, str]:
    """PDF page index -> printed label ("7-79").

    `build_index` returns a record whose labels live under a `labels` key, not
    at the top level. Walking the top level instead yields nothing, and the
    symptom is quiet: search still works but every hit cites an opaque PDF
    offset, so a drafted criterion cannot name the page it rests on.
    """
    try:
        labels = (build_index(key) or {}).get("labels") or {}
    except Exception:
        return {}
    out: dict[int, str] = {}
    for label, index in labels.items():
        if isinstance(index, int):
            out.setdefault(index, label)
    return out


def relabel_cache(key: str) -> int:
    """Refresh only the labels of an existing cache, without re-extracting text."""
    cache = load_cache(key)
    cache["labels"] = {str(k): v for k, v in invert_labels(key).items()}
    with gzip.open(cache_path(key), "wt", encoding="utf-8") as handle:
        json.dump(cache, handle)
    _LOADED[key] = cache
    return len(cache["labels"])


def build_cache(key: str, force: bool = False) -> int:
    """Extract every page of one handbook to a gzipped text cache."""
    destination = cache_path(key)
    if destination.exists() and not force:
        return len(load_cache(key)["pages"])

    from pypdf import PdfReader

    pdf_path, name, _ = HANDBOOKS[key]
    reader = PdfReader(str(pdf_path))
    pages = []
    for page in reader.pages:
        try:
            pages.append(page.extract_text() or "")
        except Exception:
            pages.append("")

    # The label index maps printed labels to PDF indices; invert it so a hit can
    # be reported as "7-79" rather than an opaque PDF offset.
    labels = invert_labels(key)

    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    with gzip.open(destination, "wt", encoding="utf-8") as handle:
        json.dump({"handbook": key, "name": name,
                   "labels": {str(k): v for k, v in labels.items()},
                   "pages": pages}, handle)
    return len(pages)


_LOADED: dict[str, dict] = {}


def load_cache(key: str) -> dict:
    if key not in _LOADED:
        path = cache_path(key)
        if not path.exists():
            raise FileNotFoundError(
                f"No text cache for {key}. Run: python packs/handbook_search.py --build"
            )
        with gzip.open(path, "rt", encoding="utf-8") as handle:
            _LOADED[key] = json.load(handle)
    return _LOADED[key]


def available() -> list[str]:
    return [key for key in HANDBOOKS if cache_path(key).exists()]


def terms_from(*texts: str) -> list[str]:
    """Pull distinctive search terms out of a task title and step text."""
    words = re.findall(r"[a-z]{3,}", " ".join(t.lower() for t in texts if t))
    seen, out = set(), []
    for word in words:
        if word in STOPWORDS or word in seen:
            continue
        seen.add(word)
        out.append(word)
    return out


def search(query: str | list[str], handbooks: list[str] | None = None,
           top: int = 3, window: int = 2) -> list[dict]:
    """Score every cached page against the query terms; return the best runs.

    Scoring requires at least half the terms to appear on a page, rather than
    all of them as the label-based extractor does. Requiring all is right when
    the query is a known citation phrase, but a query built from a task title
    ("Install safety wire on nuts, bolts, and turnbuckles") has no single page
    containing every word, and demanding that returns nothing at all.
    """
    terms = terms_from(query) if isinstance(query, str) else [t.lower() for t in query]
    if not terms:
        return []
    keys = [k for k in (handbooks or available()) if cache_path(k).exists()]
    needed = max(1, len(terms) // 2)

    hits = []
    for key in keys:
        cache = load_cache(key)
        pages, labels = cache["pages"], cache.get("labels", {})
        for index, text in enumerate(pages):
            low = text.lower()
            if not low.strip():
                continue
            present = [t for t in terms if t in low]
            if len(present) < needed:
                continue
            # Distinct terms matter more than repetition: a page using four of
            # the query words once each is more on-topic than one repeating a
            # single common word twenty times.
            score = len(present) * 10 + sum(low.count(t) for t in present)
            hits.append({"handbook": key, "name": cache["name"], "pdf_index": index,
                         "label": labels.get(str(index)), "score": score,
                         "matched_terms": present})

    hits.sort(key=lambda h: -h["score"])
    chosen, taken = [], set()
    for hit in hits:
        if len(chosen) >= top:
            break
        # Skip anything adjacent to a page already taken, so the results are
        # distinct passages rather than one passage reported several times.
        if any(abs(hit["pdf_index"] - i) <= window and hit["handbook"] == k
               for k, i in taken):
            continue
        taken.add((hit["handbook"], hit["pdf_index"]))
        cache = load_cache(hit["handbook"])
        span = range(max(0, hit["pdf_index"] - 1),
                     min(len(cache["pages"]), hit["pdf_index"] + window))
        hit["text"] = "\n\n".join(cache["pages"][i] for i in span).strip()
        hit["pdf_indices"] = list(span)
        hit["labels"] = [cache.get("labels", {}).get(str(i)) for i in span]
        chosen.append(hit)
    return chosen


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("query", nargs="?")
    parser.add_argument("--build", action="store_true")
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--relabel", action="store_true",
                        help="refresh page labels without re-extracting text")
    parser.add_argument("--handbooks", nargs="*", choices=list(HANDBOOKS))
    parser.add_argument("--top", type=int, default=3)
    args = parser.parse_args()

    if args.relabel:
        for key in (args.handbooks or list(HANDBOOKS)):
            print(f"{key}: {relabel_cache(key)} page labels")
        return 0

    if args.build:
        for key in (args.handbooks or list(HANDBOOKS)):
            print(f"{key}: extracting…", flush=True)
            print(f"{key}: {build_cache(key, args.force)} pages -> {cache_path(key)}")
        return 0

    if not args.query:
        parser.error("a query is required unless --build is given")
    for hit in search(args.query, args.handbooks, top=args.top):
        pages = ", ".join(p for p in hit["labels"] if p) or f"pdf {hit['pdf_indices']}"
        print(f"[{hit['score']:>5}] {hit['handbook']} {pages}  terms={hit['matched_terms']}")
        print("        " + " ".join(hit["text"].split())[:180] + "…")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
