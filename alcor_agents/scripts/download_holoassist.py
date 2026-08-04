#!/usr/bin/env python3
"""Download a size-capped slice of the HoloAssist dataset.

HoloAssist ships one monolithic .tar per modality (video_compress.tar alone is
145 GB), so a byte budget cannot simply pick whole files. Small streams are
taken complete; the video tar is taken as a HTTP byte-range *prefix*. A tar is a
sequential archive, so a prefix still contains complete early members — tar
extracts them and reports an unexpected-EOF error at the truncation point,
which is expected and handled.

Licence: CDLAv2 (permissive, commercial use allowed).
Source:  https://holoassist.github.io/

    python3 scripts/download_holoassist.py --budget-gb 25
    python3 scripts/download_holoassist.py --budget-gb 25 --dry-run
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT_DIR = ROOT / "data" / "external" / "holoassist"
BASE = "https://hl2data.z5.web.core.windows.net/holoassist-data-release"

GiB = 1024 ** 3

# Priority order: annotations first, then the cheap sensor streams, then video.
# 'partial: True' means the file may be truncated to fit the remaining budget.
FILES = [
    {"name": "data-annotation-trainval-v1_1.json", "gib": 0.10, "partial": False,
     "what": "action/step annotations for train+val"},
    {"name": "eyes.tar", "gib": 2.45, "partial": False, "what": "eye-gaze stream"},
    {"name": "head.tar", "gib": 4.67, "partial": False, "what": "head-pose stream"},
    {"name": "imu.tar", "gib": 4.62, "partial": False, "what": "accelerometer/gyro/magnetometer"},
    {"name": "video_compress.tar", "gib": 144.62, "partial": True,
     "what": "RGB video, 256px wide"},
    {"name": "cam_info.tar", "gib": 10.07, "partial": True,
     "what": "RGB + depth camera calibration"},
    {"name": "hands.tar", "gib": 219.23, "partial": True, "what": "hand-pose stream"},
    {"name": "video_pitch_shifted.tar", "gib": 184.20, "partial": True,
     "what": "full-resolution RGB video"},
    {"name": "ahat_depth.tar", "gib": 560.45, "partial": True, "what": "Ahat depth frames"},
]


def human(n_bytes: float) -> str:
    return f"{n_bytes / GiB:.2f} GiB"


def remote_size(url: str) -> int | None:
    proc = subprocess.run(["curl", "-sIL", "--max-time", "60", url],
                          capture_output=True, text=True)
    size = None
    for line in proc.stdout.splitlines():
        if line.lower().startswith("content-length:"):
            size = int(line.split(":", 1)[1].strip())
    return size


def download(url: str, dest: Path, target: int, attempts: int = 8) -> int:
    """Fetch url to dest until it is exactly `target` bytes.

    The Azure endpoint resets long connections (curl exit 56), so a single call
    routinely stops early. Each attempt issues an explicit Range request for the
    bytes still missing and appends them, retrying until the file reaches target
    or an attempt makes no progress at all.
    """
    dest.parent.mkdir(parents=True, exist_ok=True)

    for attempt in range(1, attempts + 1):
        have = dest.stat().st_size if dest.exists() else 0
        if have >= target:
            break
        before = have
        # Range is inclusive on both ends; ask only for what is still missing.
        proc = subprocess.run(
            ["curl", "-sS", "-L", "--retry", "3", "--retry-delay", "2",
             "--max-time", "3600", "-r", f"{have}-{target - 1}", url],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        )
        if proc.stdout:
            with dest.open("ab") as fh:
                fh.write(proc.stdout)
        now = dest.stat().st_size if dest.exists() else 0
        if now >= target:
            break
        if now == before:
            err = (proc.stderr or b"").decode(errors="replace").strip()[:120]
            print(f"      attempt {attempt}: no progress at {human(now)}"
                  f"{f' ({err})' if err else ''}", file=sys.stderr)
            if attempt == attempts:
                break
        else:
            print(f"      attempt {attempt}: {human(now)} / {human(target)} "
                  f"(reconnecting)", flush=True)

    return dest.stat().st_size if dest.exists() else 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--budget-gb", type=float, default=25.0,
                    help="total download cap in GiB")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    budget = int(args.budget_gb * GiB)
    print(f"HoloAssist — budget {args.budget_gb:.0f} GiB "
          f"(full dataset is ~1,130 GiB)\n")

    plan = []
    spent = 0
    for entry in FILES:
        full = int(entry["gib"] * GiB)
        left = budget - spent
        if left <= 0:
            plan.append({**entry, "take": 0, "mode": "skipped (budget spent)"})
            continue
        if full <= left:
            plan.append({**entry, "take": full, "mode": "complete"})
            spent += full
        elif entry["partial"] and left > GiB:  # only worth a prefix above 1 GiB
            plan.append({**entry, "take": left, "mode": "prefix"})
            spent += left
        else:
            plan.append({**entry, "take": 0, "mode": "skipped (does not fit)"})

    for item in plan:
        mark = {"complete": "full", "prefix": "PREFIX"}.get(item["mode"], "-")
        size = human(item["take"]) if item["take"] else "-"
        print(f"  {item['name']:<38} {size:>11}  {mark:<7} {item['what']}")
    print(f"\n  total planned: {human(spent)}")

    if args.dry_run:
        return 0

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    manifest = []
    incomplete: list[str] = []
    for item in plan:
        if not item["take"]:
            manifest.append({**{k: item[k] for k in ("name", "what", "mode")},
                             "bytes": 0, "path": ""})
            continue
        url = f"{BASE}/{item['name']}"
        dest = OUT_DIR / item["name"]
        # For a complete file, trust the server's own length over the table.
        target = item["take"]
        if item["mode"] == "complete":
            target = remote_size(url) or target
        print(f"\n  {item['name']} ({item['mode']}, {human(target)}) ...", flush=True)
        got = download(url, dest, target)

        complete = got >= target
        print(f"    got {human(got)}" + ("" if complete else f"  ** INCOMPLETE of {human(target)} **"))
        if not complete:
            incomplete.append(item["name"])
        manifest.append({
            "name": item["name"], "what": item["what"], "mode": item["mode"],
            "bytes": got, "expected_bytes": target,
            "complete": complete, "path": str(dest.relative_to(ROOT)),
            "full_size_gib": item["gib"], "url": url,
        })

    (OUT_DIR / "manifest.json").write_text(json.dumps({
        "dataset": "HoloAssist",
        "source": "https://holoassist.github.io/",
        "license": "CDLAv2 (permissive)",
        "full_dataset_gib": round(sum(f["gib"] for f in FILES), 2),
        "budget_gib": args.budget_gb,
        "downloaded_gib": round(sum(m["bytes"] for m in manifest) / GiB, 2),
        "files": manifest,
    }, indent=2), encoding="utf-8")

    total = sum(m["bytes"] for m in manifest)
    print(f"\ndownloaded {human(total)} -> {OUT_DIR.relative_to(ROOT)}")
    print(f"manifest: {(OUT_DIR / 'manifest.json').relative_to(ROOT)}")
    if incomplete:
        print(f"\n{len(incomplete)} file(s) INCOMPLETE: {', '.join(incomplete)}")
        print("Re-run to resume; each file continues from where it stopped.")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
