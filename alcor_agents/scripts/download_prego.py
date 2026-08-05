#!/usr/bin/env python3
"""Fetch the PREGO codebase and its precomputed features.

PREGO (CVPR 2024) is the reference implementation of *one-class online mistake
detection*: train only on correct executions, flag the deviation as it happens.
That is exactly the constraint recorded in docs/evals.md — the AIM task videos
are correct reference demonstrations, and we cannot wait on labelled failures to
start measuring. PREGO also defines two online benchmarks re-derived from
existing data, each test video truncated at the frame the procedure is
compromised:

  Assembly101-O  egocentric view only, train split is correct procedures only
  Epic-Tent-O    14 train / 15 test, procedural errors only (order, omit,
                 correction, repeat)

Note this pulls *TSN features*, not video — a few GiB, not the 3.5 TiB of raw
Assembly101. You do not need download_assembly101.py to run these benchmarks.

The step-anticipation branch calls a Llama model through unsloth and needs
separate Meta access; --skip-anticipation-deps leaves that out. The step
recognition benchmark runs without it.

Licence: see the upstream repo (code); Assembly101-derived features inherit
         CC BY-NC 4.0 — NON-COMMERCIAL. Epic-Tent terms apply to Epic-Tent-O.
Paper:   https://arxiv.org/abs/2404.01933
Repo:    https://github.com/aleflabo/PREGO
Project: https://www.pinlab.org/prego

    python3 scripts/download_prego.py --dry-run
    python3 scripts/download_prego.py
    python3 scripts/download_prego.py --no-features   # code only
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT_DIR = ROOT / "data" / "external" / "prego"
REPO_DIR = OUT_DIR / "PREGO"

REPO = "https://github.com/aleflabo/PREGO.git"
# TSN features for Assembly101-O and Epic-Tent-O, per the upstream README.
FEATURES_DRIVE = (
    "https://drive.google.com/drive/u/1/folders/"
    "1gcOIEXhwysCE2o8-5C4vQnTShJ7p3CKH"
)
GiB = 1024 ** 3

# The layout the training configs expect, relative to the repo root.
EXPECTED = [
    "Assembly101-O/rgb_anet_resnet50",
    "Assembly101-O/rgb_as_flow",
    "Assembly101-O/target_perframe",
    "Epic-tent-O/rgb_anet_resnet50",
    "Epic-tent-O/rgb_as_flow",
    "Epic-tent-O/target_perframe",
]


def have(binary: str) -> bool:
    return shutil.which(binary) is not None


def run(cmd: list[str], cwd: Path | None = None) -> int:
    print("    $ " + " ".join(cmd), flush=True)
    return subprocess.run(cmd, cwd=str(cwd) if cwd else None).returncode


def dir_bytes(path: Path) -> int:
    if not path.exists():
        return 0
    return sum(p.stat().st_size for p in path.rglob("*") if p.is_file())


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--no-features", action="store_true",
                    help="clone the code but skip the Google Drive features")
    ap.add_argument("--skip-anticipation-deps", action="store_true",
                    help="do not install unsloth (needs Meta Llama access)")
    args = ap.parse_args()

    plan = [
        ("git clone --depth 1", REPO, str(REPO_DIR.relative_to(ROOT))),
    ]
    if not args.no_features:
        plan.append(("gdown --folder", FEATURES_DRIVE,
                     str(REPO_DIR.relative_to(ROOT)) + "/{Assembly101-O,Epic-tent-O}"))

    print("PREGO — one-class online mistake detection\n")
    for how, what, where in plan:
        print(f"  {how:<22} {what}")
        print(f"  {'':<22} -> {where}")
    print()

    missing = [b for b in (["git"] + ([] if args.no_features else ["gdown"]))
               if not have(b)]
    if missing:
        print(f"  missing on PATH: {', '.join(missing)}")
        if "gdown" in missing:
            print("    pip install gdown")
        if args.dry_run is False:
            return 1

    if args.dry_run:
        print("  (dry run — nothing fetched)")
        return 0

    OUT_DIR.mkdir(parents=True, exist_ok=True)

    if REPO_DIR.exists():
        print(f"  {REPO_DIR.name} already present — pulling")
        run(["git", "pull", "--ff-only"], cwd=REPO_DIR)
    else:
        print("  cloning PREGO ...")
        if run(["git", "clone", "--depth", "1", REPO, str(REPO_DIR)]) != 0:
            print("  ! clone failed", file=sys.stderr)
            return 1

    if not args.no_features:
        print("\n  fetching TSN features from Google Drive "
              "(large; resumable, re-run if it stalls) ...")
        rc = run(["gdown", "--folder", "--remaining-ok", FEATURES_DRIVE],
                 cwd=REPO_DIR)
        if rc != 0:
            print("  ! gdown exited nonzero. Drive folder downloads rate-limit;"
                  " re-run to resume, or fetch by hand from\n    "
                  + FEATURES_DRIVE, file=sys.stderr)

    print("\n  layout check:")
    ok = True
    for rel in EXPECTED:
        path = REPO_DIR / rel
        mark = "ok " if path.exists() else "MISSING"
        if not path.exists():
            ok = False
        print(f"    {mark}  {rel:<38} {dir_bytes(path) / GiB:>7.2f} GiB")

    if not ok:
        print("\n  gdown flattens some Drive folders. Move the feature "
              "directories so they sit directly under the repo root in the "
              "layout above before running the configs.")

    manifest = {
        "name": "PREGO",
        "paper": "https://arxiv.org/abs/2404.01933",
        "repo": REPO,
        "features": FEATURES_DRIVE,
        "license": "code: see upstream; Assembly101-O features inherit "
                   "CC BY-NC 4.0 (non-commercial); Epic-Tent terms apply to "
                   "Epic-tent-O",
        "why": "one-class protocol — train on correct executions only, detect "
               "deviation online; test videos truncated at the compromise "
               "frame. Matches docs/evals.md: our AIM videos are correct "
               "reference demonstrations.",
        "benchmarks": ["Assembly101-O", "Epic-tent-O"],
        "downloaded_gib": round(dir_bytes(REPO_DIR) / GiB, 2),
        "layout_complete": ok,
    }
    (OUT_DIR / "manifest.json").write_text(
        json.dumps(manifest, indent=2), encoding="utf-8")

    print(f"\n  {dir_bytes(REPO_DIR) / GiB:.2f} GiB -> "
          f"{REPO_DIR.relative_to(ROOT)}")
    print(f"  manifest: {(OUT_DIR / 'manifest.json').relative_to(ROOT)}")

    print("\nnext:")
    print("  python3.10 -m venv .venv && source .venv/bin/activate")
    print(f"  pip install -r {(REPO_DIR / 'requirements.txt').relative_to(ROOT)}")
    if not args.skip_anticipation_deps:
        print("  pip install unsloth   # step_anticipation only; the Llama "
              "weights need Meta access at https://www.llama.com/llama-downloads/")
    print("  cd " + str(REPO_DIR.relative_to(ROOT)))
    print("  python step_recognition/main.py \\")
    print("      --config step_recognition/configs/miniroad_assembly101-O.yaml")
    print("\nRead the truncation logic in the Epic-tent-O loader before "
          "porting: the protocol only counts a detection if it fires at or "
          "before the compromise frame, which is the timing semantics our "
          "own defect-recall metric is currently missing.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
