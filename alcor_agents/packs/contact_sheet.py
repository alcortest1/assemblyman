#!/usr/bin/env python3
"""Tile sampled frames into labelled contact sheets for pass-1 boundary review.

Reviewing a clip frame by frame means looking at every frame, but opening them
one at a time does not scale past a short clip. A contact sheet keeps the
every-frame guarantee while making the review tractable: each tile carries its
own timestamp label, so a boundary spotted on a sheet is already expressed in
the `t000012_25.jpg` filename convention the segments file and the pack cite.

Sheets are deliberately built from the 480px index frames, not the 960px detail
frames -- pass 1 only has to find where a sub-subtask starts and ends. Pass 2
opens the full-resolution frame at those boundaries to describe the work.

    python packs/contact_sheet.py build/index/AM.II.K.S3/elect_conn_2 \
            --out build/sheets/AM.II.K.S3/elect_conn_2
    python packs/contact_sheet.py <frames_dir> --out <dir> --cols 6 --rows 5
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

from PIL import Image, ImageDraw

FRAME_RE = re.compile(r"^t(\d{6})_(\d{2})\.jpg$")

LABEL_H = 16
PAD = 3
BG = (22, 22, 26)
FG = (245, 245, 245)
# Frames whose timestamp is a whole second get a brighter label, so the eye can
# find its place on a dense sheet without reading every number.
FG_DIM = (150, 150, 158)


def frame_seconds(path: Path) -> float | None:
    """Recover the source timestamp a frame filename encodes."""
    m = FRAME_RE.match(path.name)
    if not m:
        return None
    return int(m.group(1)) + int(m.group(2)) / 100.0


def list_frames(frames_dir: Path) -> list[tuple[Path, float]]:
    out = []
    for p in sorted(frames_dir.iterdir()):
        secs = frame_seconds(p)
        if secs is not None:
            out.append((p, secs))
    return out


def build_sheet(
    batch: list[tuple[Path, float]], cols: int, rows: int, tile_w: int
) -> Image.Image:
    with Image.open(batch[0][0]) as probe:
        aspect = probe.height / probe.width
    tile_h = int(round(tile_w * aspect))

    cell_w = tile_w + PAD * 2
    cell_h = tile_h + LABEL_H + PAD * 2
    sheet = Image.new("RGB", (cell_w * cols, cell_h * rows), BG)
    draw = ImageDraw.Draw(sheet)

    for i, (path, secs) in enumerate(batch):
        cx = (i % cols) * cell_w + PAD
        cy = (i // cols) * cell_h + PAD
        with Image.open(path) as im:
            sheet.paste(im.convert("RGB").resize((tile_w, tile_h), Image.LANCZOS), (cx, cy))
        whole = abs(secs - round(secs)) < 0.001
        draw.text(
            (cx + 1, cy + tile_h + 2),
            f"{secs:8.2f}s",
            fill=FG if whole else FG_DIM,
        )
    return sheet


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("frames_dir", type=Path)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--cols", type=int, default=6)
    ap.add_argument("--rows", type=int, default=5)
    ap.add_argument("--tile-width", type=int, default=300)
    args = ap.parse_args()

    if not args.frames_dir.is_dir():
        print(f"not a directory: {args.frames_dir}", file=sys.stderr)
        return 1

    frames = list_frames(args.frames_dir)
    if not frames:
        print(f"no timestamped frames in {args.frames_dir}", file=sys.stderr)
        return 1

    args.out.mkdir(parents=True, exist_ok=True)
    per_sheet = args.cols * args.rows

    for n, start in enumerate(range(0, len(frames), per_sheet), 1):
        batch = frames[start : start + per_sheet]
        sheet = build_sheet(batch, args.cols, args.rows, args.tile_width)
        dest = args.out / f"sheet{n:02d}.jpg"
        sheet.save(dest, quality=88)
        print(
            f"{dest}: {len(batch)} frames  "
            f"{batch[0][1]:.2f}s-{batch[-1][1]:.2f}s"
        )

    print(f"{len(frames)} frames -> {args.out}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
