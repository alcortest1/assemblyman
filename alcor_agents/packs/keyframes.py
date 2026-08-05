#!/usr/bin/env python3
"""Sample keyframes from a task's first-person videos into its pack.

Reads data/processed/videos_manifest.csv (written by scripts/download_drive_videos.py)
Writes tasks/<ACS_CODE>/keyframes/<video-stem>/frame_<mmss>.jpg
       tasks/<ACS_CODE>/keyframes/index.json

Frames are sampled on a fixed interval so the output is deterministic and
re-runnable. Labeling frames against pack steps is a separate, human step —
this script only produces candidates and records their timestamps.

Requires ffmpeg on PATH. It is not bundled; install with `brew install ffmpeg`
(or download a static build). Without it the script reports what it would do
and exits non-zero rather than silently producing an empty keyframe set.

    python3 packs/keyframes.py AM.I.E.S1 --interval 5
    python3 packs/keyframes.py --all --dry-run
"""

from __future__ import annotations

import argparse
import csv
import json
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MANIFEST = ROOT / "data" / "processed" / "videos_manifest.csv"
TASK_DIR = ROOT / "tasks"


def have_ffmpeg() -> bool:
    return shutil.which("ffmpeg") is not None


def videos_for(acs_code: str) -> list[dict]:
    if not MANIFEST.exists():
        return []
    return [
        row for row in csv.DictReader(MANIFEST.open(encoding="utf-8"))
        if row["acs_code"] == acs_code and row["status"] == "ok" and (ROOT / row["path"]).exists()
    ]


def duration(path: Path) -> float | None:
    if not shutil.which("ffprobe"):
        return None
    proc = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=nw=1:nk=1", str(path)],
        capture_output=True, text=True,
    )
    try:
        return float(proc.stdout.strip())
    except ValueError:
        return None


def sample(acs_code: str, interval: int, dry_run: bool) -> dict:
    videos = videos_for(acs_code)
    out_root = TASK_DIR / acs_code / "keyframes"
    entries = []

    for video in videos:
        src = ROOT / video["path"]
        stem = src.stem
        dest_dir = out_root / stem
        secs = duration(src)
        entry = {
            "video": video["path"],
            "video_sha_source": video["drive_file_id"],
            "stem": stem,
            "interval_seconds": interval,
            "duration_seconds": secs,
            "expected_frames": int(secs // interval) + 1 if secs else None,
            "dir": str(dest_dir.relative_to(TASK_DIR / acs_code)),
            "frames": [],
        }

        if dry_run or not have_ffmpeg():
            entries.append(entry)
            continue

        dest_dir.mkdir(parents=True, exist_ok=True)
        # -vf fps=1/N gives one frame every N seconds; %04d keeps them ordered.
        proc = subprocess.run(
            ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
             "-i", str(src), "-vf", f"fps=1/{interval}", "-q:v", "3",
             str(dest_dir / "frame_%04d.jpg")],
            capture_output=True, text=True,
        )
        if proc.returncode != 0:
            entry["error"] = proc.stderr.strip()[:300]
        frames = sorted(dest_dir.glob("frame_*.jpg"))
        entry["frames"] = [
            {"file": f.name, "timestamp_seconds": i * interval}
            for i, f in enumerate(frames)
        ]
        entries.append(entry)

    result = {"acs_code": acs_code, "interval_seconds": interval,
              "video_count": len(videos), "videos": entries}

    if not dry_run and have_ffmpeg() and videos:
        out_root.mkdir(parents=True, exist_ok=True)
        (out_root / "index.json").write_text(json.dumps(result, indent=2), encoding="utf-8")

    total = sum(len(e["frames"]) for e in entries)
    expected = sum(e["expected_frames"] or 0 for e in entries)
    print(f"  {acs_code}: {len(videos)} video(s), "
          f"{total} frames written" + (f" (~{expected} expected)" if expected else ""))
    return result


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("acs_code", nargs="?")
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--interval", type=int, default=5, help="seconds between frames")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    if not MANIFEST.exists():
        print(f"missing {MANIFEST.relative_to(ROOT)}; "
              f"run scripts/download_drive_videos.py first", file=sys.stderr)
        return 1

    if args.all:
        codes = sorted({r["acs_code"] for r in csv.DictReader(MANIFEST.open(encoding="utf-8"))})
    elif args.acs_code:
        codes = [args.acs_code]
    else:
        ap.print_help()
        return 1

    if not have_ffmpeg() and not args.dry_run:
        print("ffmpeg not found on PATH — no frames can be extracted.", file=sys.stderr)
        print("Install it with:  brew install ffmpeg", file=sys.stderr)
        print("\nWhat would be sampled:", file=sys.stderr)
        for code in codes:
            sample(code, args.interval, dry_run=True)
        return 1

    for code in codes:
        sample(code, args.interval, args.dry_run)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
