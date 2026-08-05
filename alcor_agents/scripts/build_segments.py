#!/usr/bin/env python3
"""Turn a hand-authored segment table into a validated .segments.json.

Segment boundaries are a judgement made while reading frames, but the frame
filenames, frame counts and coverage arithmetic around them are not -- and
getting those wrong by hand is how a segmentation ends up citing a frame that
does not exist, or silently skipping a sub-subtask. This takes the reviewer's
(t_start, t_end, ...) rows, resolves each boundary against the frames actually
on disk, and refuses to write a file that does not cover the clip.

Usage: import and call `write_segments(...)` from a per-clip authoring script.
"""

from __future__ import annotations

import json
from pathlib import Path

FPS = 4.0


def frame_name(secs: float) -> str:
    """Render a timestamp in the `t000012_25.jpg` convention."""
    whole = int(secs)
    hundredths = int(round((secs - whole) * 100))
    return f"t{whole:06d}_{hundredths:02d}.jpg"


def write_segments(
    *,
    frames_dir: Path,
    out_path: Path,
    video: str,
    duration_s: float,
    rows: list[dict],
    notes: str,
    variant: str | None = None,
) -> None:
    available = {p.name for p in frames_dir.iterdir() if p.suffix == ".jpg"}
    if not available:
        raise SystemExit(f"no frames in {frames_dir}")

    segments = []
    problems: list[str] = []

    for seq, row in enumerate(rows, 1):
        t0, t1 = float(row["t_start"]), float(row["t_end"])
        if t1 < t0:
            problems.append(f"seg {seq}: t_end {t1} before t_start {t0}")

        f0, f1 = frame_name(t0), frame_name(t1)
        for f in (f0, f1):
            if f not in available:
                problems.append(f"seg {seq}: boundary frame {f} not on disk")

        segments.append(
            {
                "seq": seq,
                "step_id": row.get("step_id"),
                "step_title": row["step_title"],
                "substep_label": row["substep_label"],
                "t_start": t0,
                "t_end": t1,
                "frame_start": f0,
                "frame_end": f1,
                "frame_count": int(round((t1 - t0) * FPS)) + 1,
                "short_description": row["short_description"],
                "boundary_reason": row["boundary_reason"],
                "confidence": row["confidence"],
            }
        )

    # Coverage: a gap between segments means a sub-subtask never got described.
    for a, b in zip(segments, segments[1:]):
        gap = b["t_start"] - a["t_end"]
        if gap > 1.0 / FPS + 1e-6:
            problems.append(
                f"gap {a['t_end']:.2f}s -> {b['t_start']:.2f}s "
                f"(between seg {a['seq']} and {b['seq']})"
            )
        if gap < 0:
            problems.append(f"overlap between seg {a['seq']} and {b['seq']}")

    if segments[0]["t_start"] > 1e-6:
        problems.append(f"clip does not start at 0.0 (starts {segments[0]['t_start']})")
    tail = duration_s - segments[-1]["t_end"]
    if tail > 1.0:
        problems.append(f"last segment ends {tail:.2f}s before duration {duration_s}")

    if problems:
        raise SystemExit("segmentation rejected:\n  " + "\n  ".join(problems))

    total = sum(s["frame_count"] for s in segments)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(
        json.dumps(
            {
                "video": video,
                "variant": variant,
                "duration_s": duration_s,
                "frames_reviewed": len(available),
                "segments": segments,
                "notes": notes,
            },
            indent=2,
        )
        + "\n"
    )
    print(
        f"{out_path}: {len(segments)} segments, "
        f"{total} frame-slots over {len(available)} frames on disk"
    )
