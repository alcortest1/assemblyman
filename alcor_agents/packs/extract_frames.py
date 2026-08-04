"""Sample reference videos into timestamped frames for pack authoring.

The ffmpeg binary comes from the `imageio-ffmpeg` wheel, so this needs no
system package. Frames are named by their timestamp (`t000123_50.jpg` =
123.50 s) because a frame's filename is how the pack cites it as evidence.

    python packs/extract_frames.py probe data/videos/AM.I.E.S1
    python packs/extract_frames.py sample data/videos/AM.I.E.S1/clip.mp4 \
        build/frames/AM.I.E.S1/clip --fps 1 --width 1024
    python packs/extract_frames.py sample <clip> <out> --start 40 --end 70 --fps 4
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

import imageio_ffmpeg

FFMPEG = imageio_ffmpeg.get_ffmpeg_exe()
VIDEO_SUFFIXES = {".mp4", ".mov", ".m4v", ".avi"}


def probe(path: Path) -> dict:
    """Duration/size/fps via ffmpeg's stderr banner — avoids needing ffprobe."""
    out = subprocess.run(
        [FFMPEG, "-hide_banner", "-i", str(path)],
        capture_output=True,
        text=True,
    ).stderr
    info: dict[str, object] = {"file": path.name}
    for line in out.splitlines():
        line = line.strip()
        if line.startswith("Duration:"):
            clock = line.split("Duration:", 1)[1].split(",")[0].strip()
            hours, minutes, seconds = clock.split(":")
            info["duration_s"] = round(
                int(hours) * 3600 + int(minutes) * 60 + float(seconds), 2
            )
        elif " Video: " in line:
            for token in line.split(","):
                token = token.strip()
                if "x" in token and token.split("x")[0].strip().isdigit():
                    info["size"] = token.split(" ")[0]
                elif token.endswith("fps"):
                    info["fps"] = float(token.split(" ")[0])
    return info


def sample(
    video: Path,
    out_dir: Path,
    fps: float,
    width: int,
    start: float | None,
    end: float | None,
) -> int:
    """Write JPEGs at `fps` samples per second, named by source timestamp."""
    out_dir.mkdir(parents=True, exist_ok=True)
    offset = start or 0.0

    cmd = [FFMPEG, "-hide_banner", "-loglevel", "error"]
    if start is not None:
        # Seek before -i so ffmpeg jumps rather than decoding from zero.
        cmd += ["-ss", str(start)]
    if end is not None:
        cmd += ["-to", str(end)] if start is None else ["-t", str(end - start)]
    cmd += [
        "-i",
        str(video),
        "-vf",
        f"fps={fps},scale={width}:-2",
        "-q:v",
        "3",
        "-frame_pts",
        "0",
        str(out_dir / "seq%05d.jpg"),
    ]
    subprocess.run(cmd, check=True, capture_output=True)

    # ffmpeg numbers output sequentially; rename to real timestamps so a frame
    # can be cited back to the source video without a lookup table.
    written = 0
    for index, frame in enumerate(sorted(out_dir.glob("seq*.jpg"))):
        stamp = offset + index / fps
        frame.rename(out_dir / f"t{stamp:09.2f}.jpg".replace(".", "_", 1))
        written += 1
    return written


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    probe_cmd = sub.add_parser("probe", help="report duration/size/fps")
    probe_cmd.add_argument("target", type=Path, help="video file or directory")

    sample_cmd = sub.add_parser("sample", help="write frames as JPEGs")
    sample_cmd.add_argument("video", type=Path)
    sample_cmd.add_argument("out_dir", type=Path)
    sample_cmd.add_argument("--fps", type=float, default=1.0)
    sample_cmd.add_argument("--width", type=int, default=1024)
    sample_cmd.add_argument("--start", type=float)
    sample_cmd.add_argument("--end", type=float)

    args = parser.parse_args()

    if args.command == "probe":
        targets = (
            sorted(p for p in args.target.iterdir() if p.suffix.lower() in VIDEO_SUFFIXES)
            if args.target.is_dir()
            else [args.target]
        )
        for target in targets:
            print(json.dumps(probe(target)))
        return 0

    count = sample(
        args.video, args.out_dir, args.fps, args.width, args.start, args.end
    )
    print(f"{args.video.name}: {count} frames -> {args.out_dir}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
