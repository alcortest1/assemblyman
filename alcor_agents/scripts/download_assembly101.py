#!/usr/bin/env python3
"""Download a size-capped slice of the Assembly101 dataset from Hugging Face.

The full dataset is ~3.54 TiB, dominated by 4,321 multi-view recordings. Under a
budget we take the cheap, information-dense parts first — annotations, skill
labels, hand poses — and spend whatever is left on a few sample recordings.

The repo is gated: you must have accepted the terms on the dataset page with the
account whose token you supply. The token is read from the HF_TOKEN environment
variable and is never written to disk by this script.

    export HF_TOKEN=hf_...
    python3 scripts/download_assembly101.py --budget-gb 25 --dry-run
    python3 scripts/download_assembly101.py --budget-gb 25

Licence: CC BY-NC 4.0 — attribution required, NON-COMMERCIAL use only.
Source:  https://assembly-101.github.io/
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT_DIR = ROOT / "data" / "external" / "assembly101"
REPO = "cvml-nus/assembly101"
GiB = 1024 ** 3

# Cheapest-and-most-useful first. Anything not matched falls to the end.
PRIORITY = [
    ("annotations/", "action segment annotations"),
    ("skill_labels", "per-sequence skill labels"),
    ("poses@60fps/", "3D hand poses at 60 fps"),
    ("recordings/", "multi-view video (sampled)"),
]


def rank(path: str) -> tuple[int, str]:
    for i, (prefix, _) in enumerate(PRIORITY):
        if path.startswith(prefix):
            return (i, path)
    return (len(PRIORITY), path)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--budget-gb", type=float, default=25.0)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    token = os.environ.get("HF_TOKEN")
    if not token:
        print("HF_TOKEN is not set. Export the token of an account that has "
              "accepted the Assembly101 terms.", file=sys.stderr)
        return 1

    from huggingface_hub import HfApi, hf_hub_download

    api = HfApi(token=token)
    try:
        info = api.repo_info(REPO, repo_type="dataset", files_metadata=True)
    except Exception as exc:
        print(f"cannot read {REPO}: {str(exc)[:300]}", file=sys.stderr)
        print("If this is a 403, accept the terms at "
              f"https://huggingface.co/datasets/{REPO}", file=sys.stderr)
        return 1

    files = [(s.rfilename, s.size or 0) for s in info.siblings]
    total = sum(size for _, size in files)
    files.sort(key=lambda fs: rank(fs[0]))

    budget = int(args.budget_gb * GiB)
    chosen: list[tuple[str, int]] = []
    spent = 0
    for name, size in files:
        if size and spent + size > budget:
            continue  # skip, but keep scanning for smaller files that still fit
        chosen.append((name, size))
        spent += size

    print(f"Assembly101 — budget {args.budget_gb:.0f} GiB of "
          f"{total / 1024**4:.2f} TiB total\n")
    by_group: dict[str, list[int]] = {}
    for name, size in chosen:
        key = next((label for pre, label in PRIORITY if name.startswith(pre)), "other")
        by_group.setdefault(key, []).append(size)
    for key, sizes in by_group.items():
        print(f"  {key:<32} {sum(sizes)/GiB:>8.2f} GiB  {len(sizes):>4} files")
    print(f"\n  total planned: {spent/GiB:.2f} GiB across {len(chosen)} files")

    if args.dry_run:
        return 0

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    got: list[dict] = []
    for i, (name, size) in enumerate(chosen, 1):
        try:
            path = hf_hub_download(
                REPO, name, repo_type="dataset", token=token,
                local_dir=str(OUT_DIR),
            )
            got.append({"file": name, "bytes": Path(path).stat().st_size})
        except Exception as exc:
            print(f"  ! {name}: {str(exc)[:160]}")
            continue
        if i % 50 == 0 or size > GiB:
            done = sum(g["bytes"] for g in got)
            print(f"  [{i}/{len(chosen)}] {done/GiB:.2f} GiB", flush=True)

    downloaded = sum(g["bytes"] for g in got)
    (OUT_DIR / "manifest.json").write_text(json.dumps({
        "dataset": "Assembly101",
        "source": "https://assembly-101.github.io/",
        "huggingface_repo": REPO,
        "license": "CC BY-NC 4.0 — attribution required, non-commercial use only",
        "full_dataset_tib": round(total / 1024**4, 3),
        "budget_gib": args.budget_gb,
        "downloaded_gib": round(downloaded / GiB, 2),
        "file_count": len(got),
        "files": got,
    }, indent=2), encoding="utf-8")

    print(f"\ndownloaded {downloaded/GiB:.2f} GiB across {len(got)} files "
          f"-> {OUT_DIR.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
