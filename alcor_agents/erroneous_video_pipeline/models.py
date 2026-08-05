"""Choosing a generation model from live capabilities, and pricing the job.

Nothing here hardcodes a model. `/videos/models` is fetched at runtime and every
constraint — aspect ratio, duration, resolution, reference type — is checked
against what the service reports today, because the roster changes and a model
that accepted a 4:3 source last week may not next week.

Three facts shape the scoring:

* **Aspect ratio is the hard filter.** Most AIM clips are 1440x1080 (4:3) and
  several strong models (Veo, Sora, Gen-4.5) offer only 16:9/9:16. Generating
  16:9 for a 4:3 source and cropping back would change framing, which is exactly
  the "camera preservation" the QA pass is there to protect. So a model that
  cannot do the source ratio is rejected rather than adapted.
* **Video-to-video is not advertised as a field.** The models endpoint has no
  "accepts video reference" flag; `runway/aleph-2` only reveals it by rejecting
  a request without one. It is inferred from pricing SKUs and description text,
  and the inference is recorded on the capability so a reviewer can see why.
* **Pricing SKUs come in incompatible shapes** — cents-per-second, dollars-per-
  second, per-token, with and without resolution and audio variants. An estimate
  that cannot be computed returns None, and the caller must then confirm rather
  than guess.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from .media import MediaInfo, nearest_aspect_ratio

# Generation modes, best first. Splicing is not a mode: the sources run 27-128s
# and no model generates past 20s, so a segment is always regenerated and always
# spliced. What differs is how that segment is conditioned.
MODE_VIDEO_REFERENCE = "video_reference"
MODE_FIRST_LAST_FRAME = "first_last_frame"
MODE_FIRST_FRAME = "first_frame"
MODE_ORDER = [MODE_VIDEO_REFERENCE, MODE_FIRST_LAST_FRAME, MODE_FIRST_FRAME]

_V2V_SKU = re.compile(r"video_continuation|video_to_video|v2v", re.I)
_V2V_TEXT = re.compile(
    r"in-context video editing|existing footage|video continuation|"
    r"reference videos|instruction-guided edit|video-to-video", re.I)

# Models observed to genuinely take a source video, by probing the live API
# rather than by reading marketing copy. `runway/aleph-2` refuses any request
# without one: "This model only supports video-to-video generation. Include
# exactly one input reference of type video_url" (checked 2026-08-05).
#
# This matters because the weaker signals below are guesses. A description
# saying "instruction-guided edits" does not establish that `/videos` will
# accept a `video_url`, and picking such a model for tier 1 on price alone
# trades a verified capability for an unverified one. Entries here are evidence,
# so they outrank both inferences; everything is still filtered against live
# capabilities, and this set is never the only way in.
VERIFIED_VIDEO_REFERENCE = {
    "runway/aleph-2": "API rejects submissions without a video_url reference",
}
# Higher is stronger. Used before cost when ranking video-reference candidates.
_BASIS_RANK = {"verified": 0, "pricing sku": 1, "description": 2, "none": 3}

_RES_PIXELS = {"480p": 480, "720p": 720, "1080p": 1080, "1024p": 1024,
               "2k": 1440, "4k": 2160}


@dataclass
class Capability:
    id: str
    name: str
    aspect_ratios: list[str]
    resolutions: list[str]
    durations: list[int]
    frame_images: list[str]
    seed: bool
    audio: bool
    pricing: dict
    supports_video_reference: bool
    video_reference_basis: str

    @classmethod
    def from_api(cls, row: dict) -> "Capability":
        skus = row.get("pricing_skus") or {}
        description = row.get("description") or ""
        model_id = row.get("id", "?")
        verified = model_id in VERIFIED_VIDEO_REFERENCE
        by_sku = any(_V2V_SKU.search(k) for k in skus)
        by_text = bool(_V2V_TEXT.search(description))
        return cls(
            id=model_id,
            name=row.get("name") or model_id,
            aspect_ratios=list(row.get("supported_aspect_ratios") or []),
            resolutions=list(row.get("supported_resolutions") or []),
            durations=list(row.get("supported_durations") or []),
            frame_images=list(row.get("supported_frame_images") or []),
            seed=bool(row.get("seed")),
            audio=bool(row.get("generate_audio")),
            pricing=skus,
            supports_video_reference=verified or by_sku or by_text,
            video_reference_basis=("verified" if verified else
                                   "pricing sku" if by_sku else
                                   "description" if by_text else "none"),
        )

    @property
    def video_reference_rank(self) -> int:
        return _BASIS_RANK.get(self.video_reference_basis, 3)

    # `None`/empty from the API means "unconstrained", not "supports nothing" —
    # aleph-2 reports no durations because it takes them from the input clip.
    def allows_aspect(self, label: str) -> bool:
        return not self.aspect_ratios or label in self.aspect_ratios

    def allows_duration(self, seconds: float) -> tuple[bool, int | None]:
        if not self.durations:
            return True, None
        pick = min(self.durations, key=lambda d: (abs(d - seconds), d))
        return abs(pick - seconds) <= max(2.0, seconds * 0.5), pick

    def best_resolution(self, height: int) -> str | None:
        if not self.resolutions:
            return None
        return min(self.resolutions,
                   key=lambda r: abs(_RES_PIXELS.get(r.lower(), 720) - height))

    def modes(self) -> list[str]:
        out = []
        if self.supports_video_reference:
            out.append(MODE_VIDEO_REFERENCE)
        if "first_frame" in self.frame_images and "last_frame" in self.frame_images:
            out.append(MODE_FIRST_LAST_FRAME)
        if "first_frame" in self.frame_images:
            out.append(MODE_FIRST_FRAME)
        return out

    def estimate_cost(self, seconds: float, resolution: str | None,
                      *, with_audio: bool = False, video_reference: bool = False
                      ) -> float | None:
        """Dollars for one generation, or None when the SKUs cannot be read.

        Deliberately picks the *most expensive* matching SKU when several apply,
        so the budget guard errs toward refusing rather than overspending.
        """
        skus = {k.lower(): v for k, v in (self.pricing or {}).items()}
        if not skus:
            return None
        res = (resolution or "").lower()

        def value(key: str) -> float | None:
            try:
                return float(skus[key])
            except (KeyError, TypeError, ValueError):
                return None

        candidates: list[float] = []
        for key, raw in skus.items():
            try:
                amount = float(raw)
            except (TypeError, ValueError):
                continue
            if "per_generation" in key or "reference_images" in key or "image_input" in key:
                continue
            if "token" in key:
                continue  # per-token video pricing is not estimable from duration
            if res and any(r in key for r in _RES_PIXELS) and res not in key:
                continue
            if "continuation" in key and not video_reference:
                continue
            if "with_audio" in key and not with_audio:
                continue
            if "without_audio" in key and with_audio:
                continue
            per_second = amount / 100.0 if "cents" in key else amount
            candidates.append(per_second * seconds)

        if not candidates:
            return None
        estimate = max(candidates)
        floor = value("minimum_cents_per_generation")
        if floor is not None:
            estimate = max(estimate, floor / 100.0)
        return round(estimate, 4)


@dataclass
class Selection:
    capability: Capability
    mode: str
    aspect_ratio: str
    resolution: str | None
    duration_s: int | None
    estimated_cost: float | None
    reasons: list[str] = field(default_factory=list)

    @property
    def model_id(self) -> str:
        return self.capability.id

    def as_dict(self) -> dict:
        return {
            "model": self.model_id,
            "model_name": self.capability.name,
            "mode": self.mode,
            "aspect_ratio": self.aspect_ratio,
            "resolution": self.resolution,
            "duration_s": self.duration_s,
            "estimated_cost_usd": self.estimated_cost,
            "supports_seed": self.capability.seed,
            "video_reference_basis": self.capability.video_reference_basis,
            "reasons": self.reasons,
        }


class NoSuitableModel(RuntimeError):
    pass


def select_model(rows: list[dict], info: MediaInfo, window_s: float, *,
                 allow_video_reference: bool = False,
                 requested: str | None = None,
                 with_audio: bool = False) -> Selection:
    """Pick the best model/mode for this source and edit window.

    `requested` (from --video-model or OPENROUTER_VIDEO_MODEL) is honoured but
    still validated: an explicitly named model that cannot hold the source aspect
    ratio is an error, not a silent downgrade.
    """
    caps = [Capability.from_api(row) for row in rows]
    if not caps:
        raise NoSuitableModel("/videos/models returned nothing")

    label, error = nearest_aspect_ratio(info.width, info.height)
    notes: list[str] = []
    if error > 0.02:
        notes.append(
            f"source {info.width}x{info.height} is {error:.1%} off {label}; "
            "output will be resampled back to source dimensions after generation")

    if requested:
        match = next((c for c in caps if c.id == requested), None)
        if match is None:
            raise NoSuitableModel(
                f"{requested!r} is not on /videos/models today. "
                f"Available: {', '.join(sorted(c.id for c in caps))}")
        caps = [match]

    scored: list[tuple[tuple, Selection]] = []
    rejected: list[str] = []
    for cap in caps:
        if not cap.allows_aspect(label):
            rejected.append(f"{cap.id}: no {label} (offers {', '.join(cap.aspect_ratios) or 'none'})")
            continue
        fits, duration = cap.allows_duration(window_s)
        if not fits:
            rejected.append(f"{cap.id}: cannot do ~{window_s:.1f}s "
                            f"(offers {cap.durations})")
            continue
        modes = [m for m in cap.modes()
                 if m != MODE_VIDEO_REFERENCE or allow_video_reference]
        if not modes:
            why = ("video-reference only, and hosting is disabled"
                   if cap.modes() else "exposes no usable reference input")
            rejected.append(f"{cap.id}: {why}")
            continue
        mode = min(modes, key=MODE_ORDER.index)
        resolution = cap.best_resolution(info.height)
        cost = cap.estimate_cost(duration or window_s, resolution,
                                 with_audio=with_audio and cap.audio,
                                 video_reference=mode == MODE_VIDEO_REFERENCE)
        reasons = list(notes)
        reasons.append(f"aspect {label} supported")
        if mode == MODE_VIDEO_REFERENCE:
            basis = cap.video_reference_basis
            detail = (VERIFIED_VIDEO_REFERENCE.get(cap.id) if basis == "verified"
                      else f"inferred from {basis}")
            reasons.append(f"mode {mode} ({basis}: {detail})")
        else:
            reasons.append(f"mode {mode}")
        if resolution:
            reasons.append(f"resolution {resolution} nearest source height {info.height}")
        if cost is None:
            reasons.append("cost not estimable from published SKUs — confirm before submitting")
        selection = Selection(cap, mode, label, resolution, duration, cost, reasons)
        # Mode rank first: fidelity to the source beats price. Then, for
        # video-reference candidates, how well established that support actually
        # is — a verified capability is worth more than a cheaper guess, because
        # a model that turns out not to accept a video_url wastes the whole job.
        # Cost breaks the remaining ties, with unknown cost sorted last.
        rank = (MODE_ORDER.index(mode),
                cap.video_reference_rank if mode == MODE_VIDEO_REFERENCE else 0,
                0 if cost is not None else 1,
                cost if cost is not None else 0.0, cap.id)
        scored.append((rank, selection))

    if not scored:
        raise NoSuitableModel(
            "no model on /videos/models fits this source.\n  " + "\n  ".join(rejected))
    scored.sort(key=lambda pair: pair[0])
    return scored[0][1]


def describe_rejections(rows: list[dict], info: MediaInfo, window_s: float,
                        allow_video_reference: bool = False) -> list[str]:
    """Why each model was or was not eligible — for `discover`/`plan` output."""
    label, _ = nearest_aspect_ratio(info.width, info.height)
    lines = []
    for cap in (Capability.from_api(row) for row in rows):
        fits_ar = cap.allows_aspect(label)
        fits_dur, pick = cap.allows_duration(window_s)
        modes = [m for m in cap.modes()
                 if m != MODE_VIDEO_REFERENCE or allow_video_reference]
        verdict = ("eligible" if (fits_ar and fits_dur and modes) else "rejected")
        why = []
        if not fits_ar:
            why.append(f"no {label}")
        if not fits_dur:
            why.append(f"no duration near {window_s:.1f}s")
        if not modes:
            why.append("no usable reference mode")
        lines.append(f"{cap.id:34} {verdict:9} {'; '.join(why) or ', '.join(modes)}")
    return lines
