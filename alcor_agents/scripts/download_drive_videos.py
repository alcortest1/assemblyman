#!/usr/bin/env python3
"""Download the first-person task videos linked from the pilot workbook.

Reads  data/processed/tasks.csv  (produced by scripts/xlsx_to_csv.py)
Writes data/videos/<ACS_CODE>/<original-filename>
       data/processed/videos_manifest.csv

Google Drive serves large files from drive.usercontent.google.com with
`confirm=t`; the real filename comes back in Content-Disposition. Downloads
resume (curl -C -) and are skipped when the local size already matches, so the
script is safe to re-run.

    python3 scripts/download_drive_videos.py --probe   # list sizes, download nothing
    python3 scripts/download_drive_videos.py           # download everything
"""

from __future__ import annotations

import argparse
import csv
import re
import subprocess
import sys
import unicodedata
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TASKS_CSV = ROOT / "data" / "processed" / "tasks.csv"
VIDEO_DIR = ROOT / "data" / "videos"
MANIFEST = ROOT / "data" / "processed" / "videos_manifest.csv"

DRIVE_ID = re.compile(r"/file/d/([\w-]+)")
ENDPOINT = "https://drive.usercontent.google.com/download?id={id}&export=download&confirm=t"


def head(url: str) -> dict[str, str]:
    """Follow redirects and return the final response headers, lowercased."""
    proc = subprocess.run(
        ["curl", "-sIL", "--max-time", "60", url],
        capture_output=True, text=True,
    )
    headers: dict[str, str] = {}
    for line in proc.stdout.splitlines():
        if ":" in line:
            key, _, value = line.partition(":")
            headers[key.strip().lower()] = value.strip()
        elif line.startswith("HTTP/"):
            headers["status"] = line.split()[1]
    return headers


def filename_from(headers: dict[str, str], fallback: str) -> str:
    disp = headers.get("content-disposition", "")
    match = re.search(r'filename="([^"]+)"', disp) or re.search(r"filename=([^;]+)", disp)
    return match.group(1).strip() if match else fallback


def safe_name(name: str) -> str:
    """Lowercase ASCII filename, original extension preserved."""
    name = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode()
    stem, dot, ext = name.rpartition(".")
    if not dot:
        stem, ext = name, "mp4"
    stem = re.sub(r"[^A-Za-z0-9]+", "_", stem).strip("_").lower()
    return f"{stem or 'video'}.{ext.lower()}"


def collect_targets() -> list[dict]:
    """One entry per (ACS code, Drive file ID) pair, deduplicated."""
    rows = list(csv.DictReader(TASKS_CSV.open(encoding="utf-8")))
    targets, seen = [], set()
    for row in rows:
        for url in row["first_person_video_urls"].split(" | "):
            match = DRIVE_ID.search(url)
            if not match:
                continue
            file_id = match.group(1)
            key = (row["acs_code"], file_id)
            if key in seen:
                continue
            seen.add(key)
            targets.append(
                {
                    "acs_code": row["acs_code"],
                    "task": row["task"],
                    "subject": row["subject"],
                    "drive_file_id": file_id,
                    "source_url": url,
                }
            )
    return targets


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--probe", action="store_true", help="report sizes without downloading")
    args = parser.parse_args()

    if not TASKS_CSV.exists():
        print(f"missing {TASKS_CSV}; run scripts/xlsx_to_csv.py first", file=sys.stderr)
        return 1

    targets = collect_targets()
    print(f"{len(targets)} Drive files referenced by {len({t['acs_code'] for t in targets})} tasks\n")

    total_bytes = 0
    failures = 0

    for i, target in enumerate(targets, 1):
        url = ENDPOINT.format(id=target["drive_file_id"])
        headers = head(url)
        ctype = headers.get("content-type", "")
        size = int(headers.get("content-length") or 0)

        # An HTML body means Drive served a sign-in / quota page, not the file.
        if not ctype.startswith(("video/", "application/octet-stream", "image/")):
            target.update(status="unavailable", reason=ctype or headers.get("status", "no response"),
                          filename="", bytes=0, path="")
            print(f"[{i:>2}/{len(targets)}] {target['acs_code']:<13} UNAVAILABLE ({ctype or 'no content-type'})")
            failures += 1
            continue

        name = safe_name(filename_from(headers, f"{target['drive_file_id']}.mp4"))
        dest_dir = VIDEO_DIR / target["acs_code"]
        dest = dest_dir / name
        total_bytes += size
        target.update(
            status="ok", reason="", filename=name, bytes=size,
            path=str(dest.relative_to(ROOT)), content_type=ctype,
        )

        if args.probe:
            print(f"[{i:>2}/{len(targets)}] {target['acs_code']:<13} {size/1e6:>8.1f} MB  {name}")
            continue

        if dest.exists() and dest.stat().st_size == size:
            print(f"[{i:>2}/{len(targets)}] {target['acs_code']:<13} skip (complete)  {name}")
            continue

        dest_dir.mkdir(parents=True, exist_ok=True)
        print(f"[{i:>2}/{len(targets)}] {target['acs_code']:<13} {size/1e6:>8.1f} MB  {name} ...", flush=True)
        proc = subprocess.run(
            ["curl", "-sL", "-C", "-", "--retry", "3", "--retry-delay", "2", "-o", str(dest), url]
        )
        actual = dest.stat().st_size if dest.exists() else 0
        if proc.returncode != 0 or (size and actual != size):
            target.update(status="incomplete", reason=f"got {actual} of {size} bytes")
            print(f"          ! incomplete: {actual} of {size} bytes")
            failures += 1

    MANIFEST.parent.mkdir(parents=True, exist_ok=True)
    cols = ["acs_code", "subject", "task", "drive_file_id", "filename", "bytes",
            "path", "status", "reason", "source_url"]
    with MANIFEST.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=cols, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(targets)

    verb = "would download" if args.probe else "downloaded"
    print(f"\n{verb} {total_bytes/1e9:.2f} GB across {len(targets) - failures} files"
          f"{f', {failures} failed' if failures else ''}")
    print(f"manifest: {MANIFEST.relative_to(ROOT)}")
    return 1 if failures and not args.probe else 0


if __name__ == "__main__":
    raise SystemExit(main())
