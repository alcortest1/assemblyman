#!/usr/bin/env python3
"""Download a size-capped slice of the IndEgo dataset from Hugging Face.

IndEgo is 3,460 egocentric recordings (~197 h) plus 1,092 exocentric (~97 h) of
industrial work: assembly/disassembly, logistics, inspection and repair,
woodworking. The full repo is ~17.75 TiB across 22,890 files, so a budget is
mandatory — this is 700x the size of EgoOops.

Selection is by relevance to maintenance-style assessment rather than by size
alone: metadata and the VQA benchmark first (they are almost free), then
inspection/repair and disassembly, then whatever Mistake_Detection files fit.
Mistake_Detection is 3.73 TiB but spread across 10,690 files, so it samples
well under a small budget.

The repo is public (not gated), but HF_TOKEN is used when set to avoid
anonymous rate limits.

    python3 scripts/download_indego.py --budget-gb 25 --dry-run
    python3 scripts/download_indego.py --budget-gb 25

Licence: see the dataset card — confirm before any commercial use.
Source:  https://huggingface.co/datasets/FraunhoferIPK/IndEgo
Paper:   https://arxiv.org/abs/2511.19684  (NeurIPS 2025 Datasets & Benchmarks)
Code:    https://github.com/Vivek9Chavan/IndEgo/
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT_DIR = ROOT / "data" / "external" / "indego"
REPO = "FraunhoferIPK/IndEgo"
GiB = 1024 ** 3

# Lower rank downloads first. Ordered by usefulness for procedural-error
# assessment, not by size.
PRIORITY = [
    ("README.md", "dataset card"),
    ("annotated_keysteps.txt", "key-step annotation index"),
    ("VQA/", "reasoning-based question answering benchmark"),
    ("2_Inspection_Repair/", "inspection and repair — closest to AMT maintenance"),
    ("1_Disassembly/", "disassembly recordings"),
    ("4_Woodworking/", "woodworking recordings"),
    ("Mistake_Detection/", "mistake detection benchmark (sampled)"),
    ("1_Assembly/", "assembly recordings"),
    ("2_Inspection_Repair", "inspection and repair"),
    ("8_Singular_Actions/", "isolated single actions"),
]


def rank(path: str) -> int:
    for i, (prefix, _) in enumerate(PRIORITY):
        if path == prefix or path.startswith(prefix):
            return i
    return len(PRIORITY)


def blurb(path: str) -> str:
    for prefix, what in PRIORITY:
        if path == prefix or path.startswith(prefix):
            return what
    return ""


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--budget-gb", type=float, default=25.0)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    from huggingface_hub import HfApi, hf_hub_download

    token = os.environ.get("HF_TOKEN")  # optional: public repo, avoids rate limits
    api = HfApi(token=token)
    try:
        info = api.repo_info(REPO, repo_type="dataset", files_metadata=True)
    except Exception as exc:
        print(f"cannot read {REPO}: {str(exc)[:300]}", file=sys.stderr)
        return 1

    files = [(s.rfilename, s.size or 0) for s in info.siblings]
    total = sum(size for _, size in files)
    files.sort(key=lambda fs: (rank(fs[0]), fs[1], fs[0]))  # cheap files first within a group

    budget = int(args.budget_gb * GiB)
    chosen, spent = [], 0
    for name, size in files:
        if size and spent + size > budget:
            continue  # keep scanning; smaller files later may still fit
        chosen.append((name, size))
        spent += size

    groups: dict[str, list[int]] = {}
    for name, size in chosen:
        groups.setdefault(blurb(name) or "other", []).append(size)

    print(f"IndEgo — budget {args.budget_gb:.0f} GiB of {total/1024**4:.2f} TiB "
          f"({len(files)} files)\n")
    for what, sizes in sorted(groups.items(), key=lambda kv: -sum(kv[1])):
        print(f"  {what:<52} {sum(sizes)/GiB:>7.2f} GiB  {len(sizes):>5} files")
    print(f"\n  total planned: {spent/GiB:.2f} GiB across {len(chosen)} files")

    if args.dry_run:
        return 0

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    got: list[dict] = []
    failed = 0
    for i, (name, size) in enumerate(chosen, 1):
        try:
            path = hf_hub_download(REPO, name, repo_type="dataset", token=token,
                                   local_dir=str(OUT_DIR))
            got.append({"file": name, "bytes": Path(path).stat().st_size})
        except Exception as exc:
            print(f"  ! {name}: {str(exc)[:150]}")
            failed += 1
            continue
        if i % 25 == 0 or size > GiB:
            print(f"  [{i}/{len(chosen)}] {sum(g['bytes'] for g in got)/GiB:.2f} GiB",
                  flush=True)

    downloaded = sum(g["bytes"] for g in got)
    (OUT_DIR / "manifest.json").write_text(json.dumps({
        "dataset": "IndEgo",
        "source": f"https://huggingface.co/datasets/{REPO}",
        "paper": "https://arxiv.org/abs/2511.19684",
        "code": "https://github.com/Vivek9Chavan/IndEgo/",
        "license": "see dataset card — confirm before commercial use",
        "full_dataset_tib": round(total / 1024**4, 2),
        "budget_gib": args.budget_gb,
        "downloaded_gib": round(downloaded / GiB, 2),
        "file_count": len(got),
        "failed": failed,
        "files": got,
    }, indent=2), encoding="utf-8")

    print(f"\ndownloaded {downloaded/GiB:.2f} GiB across {len(got)} files "
          f"-> {OUT_DIR.relative_to(ROOT)}")
    if failed:
        print(f"{failed} file(s) failed — re-run to retry (completed files are skipped).")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
