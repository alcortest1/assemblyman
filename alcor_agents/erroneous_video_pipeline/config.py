"""Settings, secret handling and the spend guardrail.

Two properties matter here and are enforced rather than documented. The API key
is read from the environment or the gitignored `.env` and is never returned by
anything that writes a file or a log line — `redact()` is applied to every
string that leaves this package. And no generation request is submitted unless
`Budget.reserve()` has agreed to it, so a runaway retry loop cannot outspend
`MAX_GENERATION_COST`.

The repo's venv is Python 3.9 with no `httpx`/`requests` and no system `ffmpeg`,
so this package stays on `urllib` and the `imageio-ffmpeg` binary exactly as
`inspector/vlm.py` and `packs/extract_frames.py` already do. Matching that is
deliberate: a pipeline that needs a toolchain the machine cannot install is a
pipeline that never runs.
"""

from __future__ import annotations

import os
import re
import threading
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUTPUT_ROOT = ROOT / "generated_errors"

DEFAULT_BASE_URL = "https://openrouter.ai/api/v1"
# Stage 1 and the QA pass both need to read video, not just stills. Kept as a
# default rather than a hardcode so `--analysis-model` can override it.
DEFAULT_VLM_MODEL = "google/gemini-3.1-pro-preview"

_KEY_RE = re.compile(r"sk-or-v1-[A-Za-z0-9]{8,}")


def load_api_key() -> str | None:
    """Read the key from the environment, falling back to the gitignored .env.

    Same contract as `inspector.vlm.load_api_key`; duplicated rather than
    imported so this package has no dependency on the inspector's import path.
    """
    key = os.environ.get("OPENROUTER_API_KEY")
    if key:
        return key.strip()
    env_file = ROOT / ".env"
    if env_file.exists():
        for line in env_file.read_text().splitlines():
            if line.startswith("OPENROUTER_API_KEY="):
                return line.split("=", 1)[1].strip() or None
    return None


def redact(text: str) -> str:
    """Blank any API key that reached a string bound for a log or a file.

    Defence in depth. Nothing in this package deliberately writes the key, but
    provider errors quote the request they rejected, and a manifest is meant to
    be shareable — so every outbound string passes through here.
    """
    key = load_api_key()
    out = _KEY_RE.sub("sk-or-v1-<redacted>", text)
    if key:
        out = out.replace(key, "<redacted>")
    return out


def _env_float(name: str, default: float) -> float:
    raw = os.environ.get(name)
    try:
        return float(raw) if raw not in (None, "") else default
    except ValueError:
        return default


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    try:
        return int(raw) if raw not in (None, "") else default
    except ValueError:
        return default


def _env_bool(name: str, default: bool = False) -> bool:
    raw = (os.environ.get(name) or "").strip().lower()
    if not raw:
        return default
    return raw in {"1", "true", "yes", "on"}


class BudgetExceeded(RuntimeError):
    """Raised instead of submitting a job that would breach the cap."""


@dataclass
class Budget:
    """Cumulative spend guard, shared across every job in a run.

    `reserve()` charges the *estimate* before submission and `settle()` corrects
    it to the provider's reported cost afterwards. Charging on estimate is the
    conservative order: a job whose true cost is only known after it has run
    cannot be allowed to discover the cap retroactively.
    """

    limit: float | None
    spent: float = 0.0
    reserved: float = 0.0
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    @property
    def committed(self) -> float:
        return self.spent + self.reserved

    def remaining(self) -> float:
        return float("inf") if self.limit is None else max(0.0, self.limit - self.committed)

    def reserve(self, estimate: float) -> None:
        with self._lock:
            if self.limit is not None and self.spent + self.reserved + estimate > self.limit:
                raise BudgetExceeded(
                    f"job estimated at ${estimate:.2f} would take the run to "
                    f"${self.spent + self.reserved + estimate:.2f}, over the "
                    f"${self.limit:.2f} cap (MAX_GENERATION_COST). "
                    "Raise --max-cost to continue."
                )
            self.reserved += estimate

    def settle(self, estimate: float, actual: float | None) -> None:
        """Release the reservation and book what the provider actually charged."""
        with self._lock:
            self.reserved = max(0.0, self.reserved - estimate)
            self.spent += actual if actual is not None else estimate


@dataclass
class Settings:
    base_url: str = DEFAULT_BASE_URL
    video_model: str | None = None
    vlm_model: str = DEFAULT_VLM_MODEL
    max_cost: float | None = None
    max_retries: int = 3
    concurrency: int = 2
    dry_run: bool = False
    # Tier-1 video-to-video needs a public HTTPS URL for the clip; off unless a
    # host is configured, so confidential footage is never exposed by default.
    allow_video_reference: bool = False
    require_confirmation: bool = False

    @classmethod
    def from_env(cls) -> "Settings":
        return cls(
            base_url=(os.environ.get("OPENROUTER_BASE_URL") or DEFAULT_BASE_URL).rstrip("/"),
            video_model=os.environ.get("OPENROUTER_VIDEO_MODEL") or None,
            vlm_model=os.environ.get("OPENROUTER_VLM_MODEL") or DEFAULT_VLM_MODEL,
            max_cost=_env_float("MAX_GENERATION_COST", 0.0) or None,
            max_retries=_env_int("MAX_RETRIES", 3),
            concurrency=_env_int("CONCURRENCY", 2),
            dry_run=_env_bool("DRY_RUN"),
            allow_video_reference=_env_bool("ALLOW_VIDEO_REFERENCE"),
            require_confirmation=_env_bool("CONFIRM_EACH_JOB"),
        )

    def budget(self) -> Budget:
        return Budget(limit=self.max_cost)
