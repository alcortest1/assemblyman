"""Asks the phone for a real photograph of the work, and waits for it to arrive.

Grading used to read the sharpest frame out of the video buffer. That buffer is
the relayed stream — downscaled, compressed for motion, and whatever the encoder
last produced. A criterion that turns on whether a scribed line is visible on
bare aluminium can fail on the stream and pass on a photograph of the same work,
so the grader asks the glasses to take an actual still.

Two channels are needed because they carry different things. The request is small
and needs an answer, so it is an RPC. The photograph is hundreds of kilobytes,
well past the ~15 kB a data packet carries, so it comes back as a byte stream and
is matched to its request by id.

`frame_grabber` remains the fallback. A phone that is an older build, or busy, or
whose glasses refuse the capture, still gets graded — from a worse image, and the
result says so.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
import uuid
from typing import Any

from livekit import rtc

logger = logging.getLogger("assemblyman-capture")

# Must match GradeProtocol.swift.
CAPTURE_METHOD = "assemblyman.capture"
CAPTURE_TOPIC = "assemblyman.capture"

# The glasses shutter, the transfer to the phone, and the stream back. Slower than
# it sounds — a still is not instant on this hardware — but a grade the operator
# gave up waiting for is worse than one graded from the video buffer.
CAPTURE_TIMEOUT_S = float(os.getenv("ASSEMBLYMAN_CAPTURE_TIMEOUT", "20"))

# The phone only has to acknowledge here; the photo follows on the byte stream.
RPC_TIMEOUT_S = float(os.getenv("ASSEMBLYMAN_CAPTURE_RPC_TIMEOUT", "8"))


def is_phone(identity: str) -> bool:
    """The relay publishes under a `phone-` identity; nothing else does."""
    return str(identity or "").startswith("phone-")


class PhotoCapture:
    """Requests stills from the phone and matches the replies to the requests."""

    def __init__(self, room: rtc.Room) -> None:
        self._room = room
        self._pending: dict[str, asyncio.Future[tuple[bytes, dict]]] = {}
        self._registered = False

    def register(self) -> None:
        """Start listening for incoming photographs. Safe to call more than once."""
        if self._registered:
            return
        try:
            self._room.register_byte_stream_handler(CAPTURE_TOPIC, self._on_stream)
            self._registered = True
            logger.info("listening for captures on %s", CAPTURE_TOPIC)
        except Exception as error:  # noqa: BLE001 - already registered, or unsupported
            logger.warning("could not register capture handler: %s", error)

    def _on_stream(self, reader, participant_identity: str) -> None:
        # The SDK calls this synchronously; reading the stream is async.
        asyncio.create_task(self._read(reader, participant_identity))

    async def _read(self, reader, participant_identity: str) -> None:
        info = getattr(reader, "info", None)
        attributes = dict(getattr(info, "attributes", None) or {})
        request_id = attributes.get("request_id", "")
        try:
            chunks = [chunk async for chunk in reader]
        except Exception as error:  # noqa: BLE001
            logger.warning("capture stream from %s failed: %s", participant_identity, error)
            self._fail(request_id, f"transfer failed: {error}")
            return

        jpeg = b"".join(chunks)
        logger.info("capture %s received from %s (%d bytes)",
                    request_id or "(no id)", participant_identity, len(jpeg))

        meta: dict[str, Any] = {
            "source": "glasses_photo",
            "bytes": len(jpeg),
            "identity": participant_identity,
        }
        for key in ("width", "height"):
            if attributes.get(key, "").isdigit():
                meta[key] = int(attributes[key])

        future = self._pending.pop(request_id, None)
        if future is None:
            # Arrived after its request timed out, or the phone sent one unasked.
            # Not an error worth surfacing, but worth seeing in the log when a
            # grade has just fallen back to the video buffer for no obvious reason.
            logger.info("capture %s had no waiter — discarded", request_id or "(no id)")
            return
        if not future.done():
            future.set_result((jpeg, meta))

    def _fail(self, request_id: str, message: str) -> None:
        future = self._pending.pop(request_id, None)
        if future and not future.done():
            future.set_exception(RuntimeError(message))

    def phone_identity(self) -> str | None:
        for participant in self._room.remote_participants.values():
            if is_phone(participant.identity):
                return participant.identity
        return None

    async def request(self) -> tuple[bytes, dict] | None:
        """Ask the phone for a still. None when there is no phone or it declines.

        Never raises: every failure here has a working fallback, and taking the
        session down because a photograph did not arrive would be absurd.
        """
        identity = self.phone_identity()
        if not identity:
            logger.info("no phone in the room — cannot request a photo")
            return None

        self.register()
        request_id = uuid.uuid4().hex[:12]
        future: asyncio.Future[tuple[bytes, dict]] = asyncio.get_running_loop().create_future()
        self._pending[request_id] = future
        started = time.monotonic()

        try:
            await self._room.local_participant.perform_rpc(
                destination_identity=identity,
                method=CAPTURE_METHOD,
                payload=json.dumps({"request_id": request_id}),
                response_timeout=RPC_TIMEOUT_S,
            )
        except Exception as error:  # noqa: BLE001
            self._pending.pop(request_id, None)
            logger.info("phone declined the capture request: %s", error)
            return None

        try:
            jpeg, meta = await asyncio.wait_for(future, timeout=CAPTURE_TIMEOUT_S)
        except asyncio.TimeoutError:
            self._pending.pop(request_id, None)
            logger.info("capture %s did not arrive within %.0fs", request_id, CAPTURE_TIMEOUT_S)
            return None
        except Exception as error:  # noqa: BLE001
            logger.info("capture %s failed: %s", request_id, error)
            return None

        meta["capture_s"] = round(time.monotonic() - started, 2)
        return jpeg, meta
