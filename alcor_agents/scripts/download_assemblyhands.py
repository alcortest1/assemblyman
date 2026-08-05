#!/usr/bin/env python3
"""Download a size-capped slice of the AssemblyHands dataset from Google Drive.

AssemblyHands is a hand-pose benchmark derived from Assembly101 (490K egocentric
images). It is distributed as three public Drive folders. Annotations are small
and taken whole; ego image archives are taken until the budget is spent.

    python3 scripts/download_assemblyhands.py --budget-gb 25 --dry-run
    python3 scripts/download_assemblyhands.py --budget-gb 25

Licence: CC BY-NC 4.0 — attribution required, NON-COMMERCIAL use only.
Source:  https://assemblyhands.github.io/
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT_DIR = ROOT / "data" / "external" / "assemblyhands"
GiB = 1024 ** 3

FOLDERS = [
    ("annotations", "1mPif4HbxfDbmAu7_prsVxqknL7nbJulI", "3D joints, calibration, splits"),
    ("ego_images", "1slji-2LSOBo7eCYNqK6O-qpbvS7D2yXm", "rectified egocentric image archives"),
    # exo_videos is listed on the project page but its Drive folder does not
    # currently return listings to unauthenticated clients.
    ("exo_videos", "1e_TIb2et_bBoa15DoBFjDT3pV-Ivqqzl", "exocentric videos"),
]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--budget-gb", type=float, default=25.0)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    import gdown

    budget = int(args.budget_gb * GiB)
    print(f"AssemblyHands — budget {args.budget_gb:.0f} GiB\n")

    manifest: list[dict] = []
    spent = 0

    for name, folder_id, what in FOLDERS:
        print(f"== {name}: {what}")
        try:
            listing = gdown.download_folder(
                id=folder_id, skip_download=True, quiet=True, remaining_ok=True
            )
        except Exception as exc:
            print(f"   unavailable: {str(exc)[:150]}\n")
            manifest.append({"folder": name, "status": "unavailable",
                             "reason": str(exc)[:200], "files": []})
            continue

        if not listing:
            print("   no files returned (permission denied or rate limited)\n")
            manifest.append({"folder": name, "status": "unavailable",
                             "reason": "empty listing", "files": []})
            continue

        print(f"   {len(listing)} files listed")
        got: list[dict] = []
        dest_root = OUT_DIR / name

        for item in listing:
            rel = getattr(item, "path", None) or str(item)
            file_id = getattr(item, "id", None)
            if file_id is None:
                continue
            if spent >= budget:
                break

            dest = dest_root / rel
            if args.dry_run:
                got.append({"file": rel, "bytes": None})
                continue

            dest.parent.mkdir(parents=True, exist_ok=True)
            if dest.exists() and dest.stat().st_size:
                size = dest.stat().st_size
            else:
                try:
                    gdown.download(id=file_id, output=str(dest), quiet=True)
                except Exception as exc:
                    print(f"   ! {rel}: {str(exc)[:120]}")
                    continue
                size = dest.stat().st_size if dest.exists() else 0

            # Drive can serve an HTML quota page instead of the file.
            if size and dest.suffix in (".tar", ".gz", ".zip"):
                with dest.open("rb") as fh:
                    if fh.read(15).lstrip().startswith(b"<"):
                        print(f"   ! {rel}: got an HTML page, not the archive "
                              f"(Drive quota) — removing")
                        dest.unlink()
                        continue

            spent += size
            got.append({"file": rel, "bytes": size})
            print(f"   {rel}  {size/GiB:.2f} GiB   [{spent/GiB:.2f}/{args.budget_gb:.0f} GiB]",
                  flush=True)

        manifest.append({"folder": name, "status": "ok", "files": got})
        print()

    if args.dry_run:
        for entry in manifest:
            print(f"  {entry['folder']:<14} {entry['status']:<12} {len(entry['files'])} files")
        return 0

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    (OUT_DIR / "manifest.json").write_text(json.dumps({
        "dataset": "AssemblyHands",
        "source": "https://assemblyhands.github.io/",
        "license": "CC BY-NC 4.0 — attribution required, non-commercial use only",
        "derived_from": "Assembly101",
        "budget_gib": args.budget_gb,
        "downloaded_gib": round(spent / GiB, 2),
        "folders": manifest,
    }, indent=2), encoding="utf-8")

    print(f"downloaded {spent/GiB:.2f} GiB -> {OUT_DIR.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
