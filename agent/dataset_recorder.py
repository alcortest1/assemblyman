"""Turns live sessions into an evaluable dataset.

The evaluation the project needs is photo-based — `(step, image) -> complete | incomplete |
uncertain` — so the unit this records is a still, not a video. Video would have to be
re-sampled into stills before anything could be scored anyway, and every extra frame is
another thing to label.

Frames are captured at moments that already carry meaning:

- **When the operator speaks.** What they say is the label hint. "Step three done" or "is this
  the right port" tells you both which step the frame belongs to and what the operator
  believed at the time, which is exactly the annotation that is expensive to reconstruct
  later. This is the trigger that makes a session self-describing.
- **On a slow heartbeat**, off by default. Coverage for the stretches where nobody talks,
  at a rate low enough that it does not swamp the set with near-duplicates.

Each still is written next to a JSONL manifest line. The manifest *is* the dataset: labelling
means adding a `ground_truth` field to each row, and the eval harness replays from it. Nothing
here writes a label — recording and labelling are kept separate so a re-record cannot quietly
invalidate work already done.

Off unless ASSEMBLYMAN_RECORD=1, because recording every session by default is a surprise
nobody wants in an assistant.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import pathlib
import time
from typing import Optional

from livekit import rtc

logger = logging.getLogger("assemblyman-dataset")

ENABLED = os.getenv("ASSEMBLYMAN_RECORD", "0") == "1"
ROOT = pathlib.Path(os.getenv("ASSEMBLYMAN_DATASET_DIR", "../dataset")).resolve()
# 0 disables the heartbeat. Kept low by default because near-duplicate frames cost labelling
# time and add nothing to a measurement.
HEARTBEAT_SECONDS = float(os.getenv("ASSEMBLYMAN_RECORD_HEARTBEAT", "0"))
JPEG_QUALITY = int(os.getenv("ASSEMBLYMAN_RECORD_QUALITY", "92"))


class SessionRecorder:
    """Keeps the newest frame from the operator's camera and writes stills on demand."""

    def __init__(self, room_name: str) -> None:
        self.room_name = room_name
        self._latest: Optional[rtc.VideoFrame] = None
        self._sequence = 0
        self._task: Optional[asyncio.Task] = None
        self._heartbeat: Optional[asyncio.Task] = None
        self._started = time.time()

        self.session_dir = ROOT / "raw" / room_name
        self.manifest = ROOT / "manifest.jsonl"

    # -- lifecycle ---------------------------------------------------------

    def attach(self, track: rtc.Track) -> None:
        """Start following `track`, holding only the most recent frame."""
        self.session_dir.mkdir(parents=True, exist_ok=True)
        self.manifest.parent.mkdir(parents=True, exist_ok=True)
        self._task = asyncio.create_task(self._consume(track))
        if HEARTBEAT_SECONDS > 0:
            self._heartbeat = asyncio.create_task(self._tick())
        logger.info(
            "recording session %s to %s (heartbeat=%ss)",
            self.room_name,
            self.session_dir,
            HEARTBEAT_SECONDS or "off",
        )

    async def aclose(self) -> None:
        for task in (self._task, self._heartbeat):
            if task:
                task.cancel()
        self._latest = None

    async def _consume(self, track: rtc.Track) -> None:
        # Only the newest frame is kept: a capture wants what the operator is looking at now,
        # and buffering a live video track is a memory leak with extra steps.
        stream = rtc.VideoStream(track)
        try:
            async for event in stream:
                self._latest = event.frame
        except asyncio.CancelledError:
            pass
        finally:
            await stream.aclose()

    async def _tick(self) -> None:
        try:
            while True:
                await asyncio.sleep(HEARTBEAT_SECONDS)
                self.capture(trigger="heartbeat")
        except asyncio.CancelledError:
            pass

    # -- capture -----------------------------------------------------------

    def capture(self, *, trigger: str, transcript: str | None = None) -> Optional[str]:
        """Write the current frame and a manifest row. Returns the image path, or None.

        Never raises: a recorder that can break the session it is recording is worse than no
        recorder, so failures are logged and dropped.
        """
        frame = self._latest
        if frame is None:
            return None

        try:
            from PIL import Image

            rgba = frame.convert(rtc.VideoBufferType.RGBA)
            image = Image.frombytes(
                "RGBA", (rgba.width, rgba.height), bytes(rgba.data)
            ).convert("RGB")

            self._sequence += 1
            name = f"{self._sequence:04d}_{trigger}.jpg"
            path = self.session_dir / name
            image.save(path, "JPEG", quality=JPEG_QUALITY)

            row = {
                "image": str(path.relative_to(ROOT)),
                "room": self.room_name,
                "sequence": self._sequence,
                "trigger": trigger,
                # Seconds into the session — more useful than a wall clock when replaying,
                # and it survives the session being recorded on a different day.
                "elapsed_s": round(time.time() - self._started, 2),
                "captured_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
                "width": rgba.width,
                "height": rgba.height,
                # What the operator said at this moment. The starting point for a label, and
                # the thing that is impossible to recover from the image alone.
                "transcript": transcript,
                # Filled in by hand during labelling; never written here.
                "step": None,
                "ground_truth": None,
                "notes": None,
            }
            with self.manifest.open("a") as handle:
                handle.write(json.dumps(row) + "\n")

            logger.info("captured %s (%s)", name, trigger)
            return str(path)
        except Exception as error:  # noqa: BLE001 - see docstring
            logger.warning("capture failed: %s", error)
            return None
