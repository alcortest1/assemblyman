"""Keeps the operator's camera available as a still, on demand.

Grading is photo-level: one rubric, one photograph of one finished subtask. But the
operator is wearing the camera on their face, and a head that is looking at a piece of
work is still moving. Snapping "the current frame" the instant someone says "grade this"
lands on a motion-blurred frame often enough to matter, and a blurred frame does not
produce a bad grade — it produces `not demonstrated in image` across a whole rubric,
which reads to the student as though their work was the problem.

So this holds a short rolling buffer instead of a single frame and hands back the
sharpest one in it. The buffer is roughly the last second: long enough to have caught a
moment when the head was still, short enough that the still is unambiguously *now*.

Sharpness is variance of a Laplacian over a downscaled grey copy — the standard
cheap focus measure. It is scale-sensitive, so it can only rank frames of the same size
against each other, which is exactly and only what it is used for here.
"""

from __future__ import annotations

import asyncio
import collections
import logging
import os
from typing import Optional

import numpy as np
from livekit import rtc

logger = logging.getLogger("assemblyman-frames")

# ~1s at 15fps. Every frame held is a full-resolution RGBA buffer, so this is bounded
# deliberately rather than by time — at 1080p a 16-frame buffer is about 130MB and that
# is already more than this is worth.
BUFFER_FRAMES = int(os.getenv("ASSEMBLYMAN_FRAME_BUFFER", "12"))

# Sharpness is scored on a downscaled copy. Full resolution costs more and ranks the
# same, because motion blur is a low-frequency effect and survives the downscale.
SCORE_WIDTH = 320


def _sharpness(frame: rtc.VideoFrame) -> float:
    """Variance of the Laplacian. Higher is sharper. Comparable only across same-size frames."""
    try:
        rgba = frame.convert(rtc.VideoBufferType.RGBA)
        pixels = np.frombuffer(bytes(rgba.data), dtype=np.uint8)
        pixels = pixels.reshape(rgba.height, rgba.width, 4)

        step = max(1, rgba.width // SCORE_WIDTH)
        grey = pixels[::step, ::step, :3].astype(np.float32) @ (0.299, 0.587, 0.114)
        if grey.shape[0] < 3 or grey.shape[1] < 3:
            return 0.0

        # 4-neighbour Laplacian, computed with slices rather than a convolution to keep
        # this dependency-light and fast enough to run on every frame.
        laplacian = (
            grey[:-2, 1:-1] + grey[2:, 1:-1] + grey[1:-1, :-2] + grey[1:-1, 2:]
            - 4.0 * grey[1:-1, 1:-1]
        )
        return float(laplacian.var())
    except Exception as error:  # noqa: BLE001 - a scoring failure must not drop the frame
        logger.debug("sharpness scoring failed: %s", error)
        return 0.0


class FrameGrabber:
    """Follows a video track and yields the sharpest recent frame as JPEG bytes."""

    def __init__(self) -> None:
        self._frames: collections.deque[tuple[rtc.VideoFrame, float]] = collections.deque(
            maxlen=max(1, BUFFER_FRAMES)
        )
        self._task: Optional[asyncio.Task] = None
        self._track_sid: Optional[str] = None

    @property
    def attached(self) -> bool:
        return self._task is not None and not self._task.done()

    def attach(self, track: rtc.Track) -> None:
        """Start following `track`. A second call for the same track is a no-op.

        A different track replaces the first — the operator switching cameras mid-session
        should not leave the grader reading from the one they stopped looking through.
        """
        if self._track_sid == track.sid and self.attached:
            return
        if self._task:
            self._task.cancel()
        self._frames.clear()
        self._track_sid = track.sid
        self._task = asyncio.create_task(self._consume(track))
        logger.info("frame grabber following track %s", track.sid)

    async def aclose(self) -> None:
        if self._task:
            self._task.cancel()
            self._task = None
        self._frames.clear()

    async def _consume(self, track: rtc.Track) -> None:
        stream = rtc.VideoStream(track)
        try:
            async for event in stream:
                self._frames.append((event.frame, _sharpness(event.frame)))
        except asyncio.CancelledError:
            pass
        except Exception as error:  # noqa: BLE001 - never take the session down with it
            logger.warning("frame stream ended: %s", error)
        finally:
            await stream.aclose()

    def best_jpeg(self, *, quality: int = 92, max_edge: int = 1568) -> Optional[tuple[bytes, dict]]:
        """The sharpest buffered frame as JPEG, with a note on what was chosen.

        Returns None when no frame has arrived yet — the caller must say "I cannot see
        anything" rather than grade an absence. `max_edge` bounds the long side: past
        about 1568px Gemini downsamples anyway, so larger only costs upload time.
        """
        if not self._frames:
            return None

        frames = list(self._frames)
        frame, score = max(frames, key=lambda pair: pair[1])
        scores = [pair[1] for pair in frames]

        try:
            from PIL import Image

            rgba = frame.convert(rtc.VideoBufferType.RGBA)
            image = Image.frombytes(
                "RGBA", (rgba.width, rgba.height), bytes(rgba.data)
            ).convert("RGB")
            source = (image.width, image.height)
            if max(image.size) > max_edge:
                image.thumbnail((max_edge, max_edge), Image.LANCZOS)

            import io

            buffer = io.BytesIO()
            image.save(buffer, "JPEG", quality=quality)
        except Exception as error:  # noqa: BLE001
            logger.warning("could not encode frame: %s", error)
            return None

        return buffer.getvalue(), {
            "width": image.width,
            "height": image.height,
            "source_width": source[0],
            "source_height": source[1],
            "sharpness": round(score, 1),
            # How much better the chosen frame was than the buffer's median. Near 1.0
            # means everything in the window was equally blurred and the pick did not
            # rescue anything, which is worth knowing when a grade comes back unusable.
            "sharpness_ratio": round(score / max(float(np.median(scores)), 1e-6), 2),
            "candidates": len(frames),
        }
