#!/usr/bin/env python3
"""Download the TrainWithAIM YouTube videos referenced in the pilot workbook.

Reads  data/processed/tasks.csv (produced by scripts/xlsx_to_csv.py)
Writes data/videos/youtube/<VIDEO_ID>.<ext>
       data/processed/youtube_manifest.csv

One video can serve several ACS codes, so files are stored once by video id and
the manifest carries the task mapping.

ffmpeg is not required: the script asks yt-dlp for the best *progressive*
format (video and audio already muxed), so nothing needs merging. That caps
quality at whatever pre-muxed stream YouTube offers, typically 360p-720p. With
ffmpeg installed, pass --best for full-quality separate streams.

    python3 scripts/download_youtube_videos.py --probe
    python3 scripts/download_youtube_videos.py
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TASKS_CSV = ROOT / "data" / "processed" / "tasks.csv"
OUT_DIR = ROOT / "data" / "videos" / "youtube"
MANIFEST = ROOT / "data" / "processed" / "youtube_manifest.csv"

# Matches watch?v=ID, youtu.be/ID, and the workbook's scheme-less watch?si=...&v=ID
ID_RE = re.compile(r"(?:v=|youtu\.be/|/shorts/)([A-Za-z0-9_-]{11})")


def video_id(url: str) -> str | None:
    match = ID_RE.search(url)
    return match.group(1) if match else None


def collect() -> dict[str, dict]:
    """video id -> {url, tasks: [...]}, deduplicated across tasks."""
    videos: dict[str, dict] = {}
    for row in csv.DictReader(TASKS_CSV.open(encoding="utf-8")):
        url = (row.get("trainwithaim_video_url") or "").strip()
        if not url:
            continue
        vid = video_id(url)
        if not vid:
            print(f"  ! unparseable YouTube URL for {row['acs_code']}: {url}", file=sys.stderr)
            continue
        entry = videos.setdefault(vid, {"video_id": vid, "url": f"https://www.youtube.com/watch?v={vid}", "tasks": []})
        entry["tasks"].append({"acs_code": row["acs_code"], "task_no": row["task_no"], "task": row["task"]})
    return videos


# YouTube rejects most of yt-dlp's default player clients from this host with
# "The page needs to be reloaded". android_vr currently answers reliably; the
# rest are kept as fallbacks because which one works shifts over time.
PLAYER_CLIENTS = ["android_vr", "web_safari", "tv", "mweb", "web"]


def ytdlp(args: list[str], client: str | None = None) -> subprocess.CompletedProcess:
    extra = ["--extractor-args", f"youtube:player_client={client}"] if client else []
    return subprocess.run(
        [sys.executable, "-m", "yt_dlp", *extra, *args], capture_output=True, text=True
    )


def ytdlp_any_client(args: list[str]) -> tuple[subprocess.CompletedProcess, str | None]:
    """Try each player client until one succeeds."""
    last = None
    for client in PLAYER_CLIENTS:
        proc = ytdlp(args, client)
        if proc.returncode == 0:
            return proc, client
        last = proc
    return last, None


def probe(entry: dict) -> dict:
    proc, client = ytdlp_any_client(["-J", "--no-warnings", "--skip-download", entry["url"]])
    if client is None:
        reason = proc.stderr.strip().splitlines()[-1][:200] if proc and proc.stderr.strip() else "probe failed"
        return {"status": "unavailable", "reason": reason}
    try:
        info = json.loads(proc.stdout)
    except json.JSONDecodeError:
        return {"status": "unavailable", "reason": "could not parse yt-dlp output"}
    return {
        "status": "ok",
        "title": info.get("title", ""),
        "uploader": info.get("uploader", ""),
        "duration_seconds": info.get("duration"),
        "player_client": client,
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--probe", action="store_true", help="report metadata without downloading")
    ap.add_argument("--best", action="store_true",
                    help="best quality (needs ffmpeg to merge separate streams)")
    args = ap.parse_args()

    if not TASKS_CSV.exists():
        print(f"missing {TASKS_CSV}; run scripts/xlsx_to_csv.py first", file=sys.stderr)
        return 1

    videos = collect()
    task_count = sum(len(v["tasks"]) for v in videos.values())
    print(f"{len(videos)} unique YouTube videos across {task_count} task references\n")

    if args.best and not shutil.which("ffmpeg"):
        print("--best needs ffmpeg on PATH (brew install ffmpeg)", file=sys.stderr)
        return 1

    # Without ffmpeg we must land a single already-muxed file. Requiring both
    # codecs present excludes the video-only/audio-only DASH streams, which
    # "best[ext=mp4]" would otherwise pick and then fail to merge.
    fmt = ("bestvideo+bestaudio/best" if args.best
           else "best[acodec!=none][vcodec!=none][ext=mp4]/"
                "best[acodec!=none][vcodec!=none]/18/best")

    rows: list[dict] = []
    failures = 0

    for i, entry in enumerate(sorted(videos.values(), key=lambda e: e["video_id"]), 1):
        meta = probe(entry)
        codes = ", ".join(t["acs_code"] for t in entry["tasks"])

        if meta["status"] != "ok":
            print(f"[{i:>2}/{len(videos)}] {entry['video_id']}  UNAVAILABLE — {meta['reason']}")
            failures += 1
            rows.append({**entry, **meta, "acs_codes": codes, "filename": "", "path": "", "bytes": 0})
            continue

        dur = meta.get("duration_seconds") or 0
        label = f"{meta['title'][:48]:<48}"
        if args.probe:
            print(f"[{i:>2}/{len(videos)}] {entry['video_id']}  {dur//60:>3}m{dur%60:02d}s  {label} [{codes}]")
            rows.append({**entry, **meta, "acs_codes": codes, "filename": "", "path": "", "bytes": 0})
            continue

        OUT_DIR.mkdir(parents=True, exist_ok=True)
        existing = list(OUT_DIR.glob(f"{entry['video_id']}.*"))
        existing = [p for p in existing if p.suffix != ".part"]
        if existing:
            path = existing[0]
            print(f"[{i:>2}/{len(videos)}] {entry['video_id']}  skip (present)  {label}")
        else:
            print(f"[{i:>2}/{len(videos)}] {entry['video_id']}  {dur//60:>3}m{dur%60:02d}s  {label} ...", flush=True)
            proc, _ = ytdlp_any_client(
                ["-f", fmt, "--no-warnings", "--no-playlist", "-c",
                 "-o", str(OUT_DIR / "%(id)s.%(ext)s"), entry["url"]]
            )
            found = [p for p in OUT_DIR.glob(f"{entry['video_id']}.*") if p.suffix != ".part"]
            if proc.returncode != 0 or not found:
                tail = proc.stderr.strip().splitlines()[-1][:200] if proc.stderr.strip() else "download failed"
                print(f"          ! {tail}")
                failures += 1
                rows.append({**entry, **meta, "status": "failed", "reason": tail,
                             "acs_codes": codes, "filename": "", "path": "", "bytes": 0})
                continue
            path = found[0]

        rows.append({**entry, **meta, "acs_codes": codes, "filename": path.name,
                     "path": str(path.relative_to(ROOT)), "bytes": path.stat().st_size})

    MANIFEST.parent.mkdir(parents=True, exist_ok=True)
    cols = ["video_id", "acs_codes", "title", "uploader", "duration_seconds",
            "filename", "path", "bytes", "status", "reason", "url"]
    with MANIFEST.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=cols, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            row.setdefault("reason", "")
            writer.writerow(row)

    total = sum(r.get("bytes", 0) for r in rows)
    verb = "probed" if args.probe else "downloaded"
    print(f"\n{verb} {len(rows) - failures}/{len(rows)} videos"
          + (f", {total/1e6:.0f} MB" if total else "")
          + (f", {failures} failed" if failures else ""))
    print(f"manifest: {MANIFEST.relative_to(ROOT)}")
    return 1 if failures and not args.probe else 0


if __name__ == "__main__":
    raise SystemExit(main())
