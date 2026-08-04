"""Turn pass-1 segment boundaries into per-substep frame subsequences.

Pass 1 reads every frame of a video at low resolution and reports where each
sub-subtask starts and ends. This slices the full-resolution frames along those
boundaries so pass 2 can study one sub-subtask at a time, and validates that the
segmentation actually covers the video — a silent gap would mean a whole
sub-subtask never gets described.

    python packs/slice_segments.py build/analysis/AM.I.E.S1/<video>.segments.json \
        --frames build/frames/AM.I.E.S1 --out build/segments/AM.I.E.S1
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# Frames land every 1/FPS seconds; a boundary may miss an exact frame time by a
# rounding hair, so treat anything within half a frame interval as inside.
DEFAULT_FPS = 4.0


def timestamp_of(frame: Path) -> float:
    """`t000012_25.jpg` -> 12.25"""
    return float(frame.stem[1:].replace("_", "."))


def slice_video(
    segments_file: Path, frames_root: Path, out_root: Path, fps: float
) -> dict:
    data = json.loads(segments_file.read_text())
    video = data["video"]
    frames = sorted((frames_root / video).glob("t*.jpg"), key=timestamp_of)
    if not frames:
        raise SystemExit(f"no frames found under {frames_root / video}")

    tolerance = 0.5 / fps
    report: dict[str, object] = {"video": video, "segments": [], "problems": []}
    covered: set[Path] = set()

    previous_end: float | None = None
    for segment in data["segments"]:
        start, end = float(segment["t_start"]), float(segment["t_end"])
        if previous_end is not None and start - previous_end > 1.0 / fps + tolerance:
            report["problems"].append(
                f"gap between {previous_end:.2f}s and {start:.2f}s "
                f"(before seq {segment['seq']})"
            )
        previous_end = end

        members = [
            f for f in frames if start - tolerance <= timestamp_of(f) <= end + tolerance
        ]
        if not members:
            report["problems"].append(
                f"seq {segment['seq']} ({segment['substep_label']}) matched no frames"
            )
            continue
        covered.update(members)

        # One directory per sub-subtask, named so it sorts in playback order and
        # reads as an identifier in the pack.
        name = f"{segment['seq']:03d}_{segment['substep_label']}"
        target = out_root / video / name
        target.mkdir(parents=True, exist_ok=True)
        for existing in target.glob("t*.jpg"):
            existing.unlink()
        for frame in members:
            link = target / frame.name
            link.symlink_to(frame.resolve())

        claimed = segment.get("frame_count")
        if claimed is not None and int(claimed) != len(members):
            report["problems"].append(
                f"seq {segment['seq']} claimed {claimed} frames, sliced {len(members)}"
            )

        report["segments"].append(
            {
                "seq": segment["seq"],
                "dir": str(target),
                "substep_label": segment["substep_label"],
                "step_id": segment.get("step_id"),
                "t_start": start,
                "t_end": end,
                "frames": len(members),
            }
        )

    missing = len(frames) - len(covered)
    if missing:
        report["problems"].append(f"{missing} of {len(frames)} frames fall in no segment")
    report["frames_total"] = len(frames)
    report["frames_covered"] = len(covered)
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("segments", type=Path, nargs="+")
    parser.add_argument("--frames", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--fps", type=float, default=DEFAULT_FPS)
    args = parser.parse_args()

    problems = 0
    for segments_file in args.segments:
        report = slice_video(segments_file, args.frames, args.out, args.fps)
        print(
            f"{report['video']}: {len(report['segments'])} sub-subtasks, "
            f"{report['frames_covered']}/{report['frames_total']} frames covered"
        )
        for problem in report["problems"]:
            print(f"  ! {problem}")
            problems += 1
        (args.out / f"{report['video']}.slices.json").write_text(
            json.dumps(report, indent=2)
        )
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
