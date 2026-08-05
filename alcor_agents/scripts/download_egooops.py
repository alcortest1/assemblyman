#!/usr/bin/env python3
"""Download the EgoOops dataset (mistake detection from egocentric video).

50 egocentric videos across five procedural domains, annotated with
video-text alignment, mistake labels, and mistake descriptions. Small enough to
take whole (~5.24 GiB), which is why it is the first dataset worth wiring into
the eval harness: it is the only one here whose *entire* label set is mistake
annotations tied to procedural text, which is the shape of the AIM assessment
problem.

Downloads verify against Content-Length and resume by byte range, so an
interrupted run continues rather than leaving a stub.

    python3 scripts/download_egooops.py --dry-run
    python3 scripts/download_egooops.py
    python3 scripts/download_egooops.py --extract

Licence: CC BY-SA 4.0 — attribution required, derivatives share-alike.
         Commercial use IS permitted (unlike Assembly101 / AssemblyHands).
Source:  https://y-haneji.github.io/EgoOops-project-page/
Paper:   https://arxiv.org/abs/2410.05343
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT_DIR = ROOT / "data" / "external" / "egooops"
BASE = "https://www.lsta.media.kyoto-u.ac.jp/resource/data/EgoOops"
GiB = 1024 ** 3

FILES = [
    ("EgoOops-annotations.zip", f"{BASE}/EgoOops-annotations.zip",
     "video-text alignment, mistake labels, mistake descriptions"),
    ("videos-processed-720p.zip", f"{BASE}/videos-processed-720p.zip",
     "50 egocentric videos, 720p RGB"),
]

# Annotations are also mirrored at
# https://github.com/Y-Haneji/EgoOops-annotations/ if the lab host is down.


def human(n: float) -> str:
    return f"{n / GiB:.2f} GiB"


def remote_size(url: str) -> int:
    proc = subprocess.run(["curl", "-sIL", "--max-time", "60", url],
                          capture_output=True, text=True)
    size = 0
    for line in proc.stdout.splitlines():
        if line.lower().startswith("content-length:"):
            size = int(line.split(":", 1)[1].strip())
    return size


def download(url: str, dest: Path, target: int, attempts: int = 10) -> int:
    """Fetch until dest reaches `target` bytes, resuming by byte range.

    Long transfers get reset mid-stream; curl's --retry does not resume a
    stream that already started, and a truncated file otherwise looks like a
    success. So verify the size and resume explicitly.
    """
    dest.parent.mkdir(parents=True, exist_ok=True)
    for attempt in range(1, attempts + 1):
        have = dest.stat().st_size if dest.exists() else 0
        if target and have >= target:
            break
        proc = subprocess.run(
            ["curl", "-fL", "--retry", "5", "--retry-delay", "3",
             "--max-time", "3600", "-C", "-", "-o", str(dest), url]
        )
        now = dest.stat().st_size if dest.exists() else 0
        if target and now >= target:
            break
        if now == have:
            print(f"      attempt {attempt}: no progress at {human(now)} "
                  f"(curl {proc.returncode})", file=sys.stderr)
            if attempt == attempts:
                break
        else:
            print(f"      attempt {attempt}: {human(now)} / {human(target)} — resuming",
                  flush=True)
    return dest.stat().st_size if dest.exists() else 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--extract", action="store_true",
                    help="unzip after download (needs roughly 2x the space)")
    args = ap.parse_args()

    plan = []
    for name, url, what in FILES:
        size = remote_size(url)
        plan.append({"name": name, "url": url, "what": what, "size": size})

    total = sum(p["size"] for p in plan)
    print("EgoOops — whole dataset\n")
    for p in plan:
        print(f"  {p['name']:<32} {human(p['size']):>10}  {p['what']}")
    print(f"\n  total: {human(total)}")

    if args.dry_run:
        return 0

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    manifest, incomplete = [], []
    for p in plan:
        dest = OUT_DIR / p["name"]
        if dest.exists() and p["size"] and dest.stat().st_size >= p["size"]:
            print(f"\n  {p['name']} — already complete ({human(p['size'])})")
            got = dest.stat().st_size
        else:
            print(f"\n  {p['name']} ({human(p['size'])}) ...", flush=True)
            got = download(p["url"], dest, p["size"])
        ok = bool(p["size"]) and got >= p["size"]
        print(f"    got {human(got)}" + ("" if ok else "  ** INCOMPLETE **"))
        if not ok:
            incomplete.append(p["name"])
        manifest.append({"file": p["name"], "what": p["what"], "bytes": got,
                         "expected_bytes": p["size"], "complete": ok,
                         "path": str(dest.relative_to(ROOT)), "url": p["url"]})

    if args.extract:
        for m in manifest:
            src = OUT_DIR / m["file"]
            if not m["complete"] or src.suffix != ".zip":
                continue
            print(f"  extracting {src.name} ...", flush=True)
            try:
                with zipfile.ZipFile(src) as zf:
                    zf.extractall(OUT_DIR / "unpacked")
            except zipfile.BadZipFile as exc:
                print(f"    ! {src.name}: {exc}")

    downloaded = sum(m["bytes"] for m in manifest)
    (OUT_DIR / "manifest.json").write_text(json.dumps({
        "dataset": "EgoOops",
        "source": "https://y-haneji.github.io/EgoOops-project-page/",
        "paper": "https://arxiv.org/abs/2410.05343",
        "annotations_mirror": "https://github.com/Y-Haneji/EgoOops-annotations/",
        "license": "CC BY-SA 4.0 — attribution + share-alike; commercial use permitted",
        "content": "50 egocentric videos, 5 procedural domains; annotations are "
                   "video-text alignment, mistake labels, mistake descriptions",
        "total_gib": round(total / GiB, 2),
        "downloaded_gib": round(downloaded / GiB, 2),
        "complete": not incomplete,
        "files": manifest,
    }, indent=2), encoding="utf-8")

    print(f"\ndownloaded {human(downloaded)} -> {OUT_DIR.relative_to(ROOT)}")
    if incomplete:
        print(f"INCOMPLETE: {', '.join(incomplete)} — re-run to resume.")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
