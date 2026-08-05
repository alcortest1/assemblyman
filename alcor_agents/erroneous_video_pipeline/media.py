"""ffmpeg work: probing, proxies, frame grabs, and the splice.

`ffprobe` is not installed on this machine and cannot be (no Homebrew), so the
probe falls back to parsing ffmpeg's stderr banner exactly as
`packs/extract_frames.py` does. When a real ffprobe *is* on PATH it is preferred,
because its JSON carries colour metadata the banner only hints at.

Two properties of the source footage drive most of this module:

* **The clips are HLG HDR, 10-bit HEVC.** Anything a generation model returns is
  8-bit Rec.709 SDR. Butt-splicing the two produces a colour jump at the cut that
  a QA grader reads — correctly — as "the scene changed". So the whole output is
  normalised to SDR through a tone-map, not just the generated part. That costs a
  full re-encode of prefix and suffix, which is why `stream_copy_ok()` exists to
  skip it for the clips that are already SDR H.264.
* **Most clips are 1440x1080 (4:3).** Generation models express size as an aspect
  ratio from a fixed menu, so the pipeline must pick a supported ratio and carry
  the mapping back on return. `nearest_aspect_ratio()` is that decision.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass, asdict
from pathlib import Path

import imageio_ffmpeg

FFMPEG = imageio_ffmpeg.get_ffmpeg_exe()
FFPROBE = shutil.which("ffprobe")

VIDEO_SUFFIXES = {".mp4", ".mov", ".mpeg", ".mpg", ".webm", ".m4v"}

# Rec.2020 HLG/PQ -> Rec.709 SDR. `desat=0` keeps saturation rather than letting
# the default desaturate highlights: the graded evidence here is a metal tube on
# a wooden bench, and washing it out costs exactly the contrast a grader needs.
TONEMAP_CHAIN = (
    "zscale=t=linear:npl=100,format=gbrpf32le,zscale=p=bt709,"
    "tonemap=tonemap=hable:desat=0,zscale=t=bt709:m=bt709:r=tv,format=yuv420p"
)
HDR_TRANSFERS = {"arib-std-b67", "smpte2084", "bt2020-10", "bt2020-12"}

# The ratios generation models actually offer, as seen on /videos/models.
KNOWN_ASPECT_RATIOS = {
    "21:9": 21 / 9, "16:9": 16 / 9, "3:2": 3 / 2, "4:3": 4 / 3, "1:1": 1.0,
    "3:4": 3 / 4, "2:3": 2 / 3, "9:16": 9 / 16, "9:21": 9 / 21,
}


class MediaError(RuntimeError):
    pass


@dataclass
class MediaInfo:
    path: str
    duration_s: float
    width: int
    height: int
    fps: float
    video_codec: str
    pix_fmt: str
    color_transfer: str
    has_audio: bool
    audio_codec: str | None = None
    audio_rate: int | None = None

    @property
    def aspect(self) -> float:
        return self.width / self.height if self.height else 0.0

    @property
    def is_hdr(self) -> bool:
        return self.color_transfer in HDR_TRANSFERS or "10le" in self.pix_fmt

    def as_dict(self) -> dict:
        out = asdict(self)
        out["aspect_ratio_label"] = nearest_aspect_ratio(self.width, self.height)[0]
        out["is_hdr"] = self.is_hdr
        return out


def nearest_aspect_ratio(width: int, height: int) -> tuple[str, float]:
    """Closest ratio from the menu models offer, plus the relative error.

    1912x1080 is 16:9 to within 0.4% and must not be treated as bespoke; 1440x1080
    is exactly 4:3. The error is returned so the caller can warn when a source is
    genuinely off-menu (2190x1080 is ~2:1 and lands 8% from 21:9).
    """
    if not height:
        raise MediaError("cannot compute aspect ratio of zero-height video")
    actual = width / height
    label, value = min(KNOWN_ASPECT_RATIOS.items(), key=lambda kv: abs(kv[1] - actual))
    return label, abs(value - actual) / actual


def _run(cmd: list[str], *, timeout: int = 1800) -> subprocess.CompletedProcess:
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    if proc.returncode != 0:
        tail = "\n".join((proc.stderr or "").strip().splitlines()[-12:])
        raise MediaError(f"ffmpeg failed ({proc.returncode}):\n{tail}")
    return proc


def probe(path: Path) -> MediaInfo:
    """Container and stream facts, via ffprobe when present, else the banner."""
    path = Path(path)
    if not path.exists():
        raise MediaError(f"no such video: {path}")
    if FFPROBE:
        return _probe_ffprobe(path)
    return _probe_banner(path)


def _probe_ffprobe(path: Path) -> MediaInfo:
    proc = _run([FFPROBE, "-v", "error", "-print_format", "json",
                 "-show_format", "-show_streams", str(path)], timeout=120)
    data = json.loads(proc.stdout)
    video = next((s for s in data["streams"] if s.get("codec_type") == "video"), None)
    audio = next((s for s in data["streams"] if s.get("codec_type") == "audio"), None)
    if video is None:
        raise MediaError(f"{path.name}: no video stream")
    num, _, den = (video.get("r_frame_rate") or "0/1").partition("/")
    fps = float(num) / float(den) if den and float(den) else 0.0
    return MediaInfo(
        path=str(path),
        duration_s=float(data["format"].get("duration") or video.get("duration") or 0.0),
        width=int(video["width"]), height=int(video["height"]), fps=round(fps, 3),
        video_codec=video.get("codec_name", "?"),
        pix_fmt=video.get("pix_fmt", "?"),
        color_transfer=video.get("color_transfer", ""),
        has_audio=audio is not None,
        audio_codec=(audio or {}).get("codec_name"),
        audio_rate=int((audio or {}).get("sample_rate") or 0) or None,
    )


_DUR = re.compile(r"Duration:\s*(\d+):(\d\d):(\d\d(?:\.\d+)?)")
_VIDEO = re.compile(r"Stream #\d+:\d+.*?: Video: ([A-Za-z0-9_]+).*")
_AUDIO = re.compile(r"Stream #\d+:\d+.*?: Audio: ([A-Za-z0-9_]+).*?(\d+) Hz")
_SIZE = re.compile(r"\b(\d{2,5})x(\d{2,5})\b")
_FPS = re.compile(r"([\d.]+)\s*fps")
_PIXFMT = re.compile(r"Video: \w+[^,]*,\s*([a-z0-9()]+)")


def _probe_banner(path: Path) -> MediaInfo:
    """Parse `ffmpeg -i`'s stderr. Exits non-zero by design, so no _run here."""
    err = subprocess.run([FFMPEG, "-hide_banner", "-i", str(path)],
                         capture_output=True, text=True).stderr
    dur = _DUR.search(err)
    duration = (int(dur.group(1)) * 3600 + int(dur.group(2)) * 60 + float(dur.group(3))
                ) if dur else 0.0
    vline = next((l for l in err.splitlines() if " Video: " in l), "")
    aline = next((l for l in err.splitlines() if " Audio: " in l), "")
    if not vline:
        raise MediaError(f"{path.name}: no video stream in ffmpeg banner")
    size = _SIZE.search(vline)
    fps = _FPS.search(vline)
    codec = _VIDEO.search(vline)
    pix = _PIXFMT.search(vline)
    transfer = ""
    for token in ("arib-std-b67", "smpte2084", "bt2020-10", "bt709"):
        if token in vline:
            transfer = token
            break
    arate = _AUDIO.search(aline) if aline else None
    return MediaInfo(
        path=str(path), duration_s=round(duration, 3),
        width=int(size.group(1)) if size else 0,
        height=int(size.group(2)) if size else 0,
        fps=round(float(fps.group(1)), 3) if fps else 0.0,
        video_codec=codec.group(1) if codec else "?",
        pix_fmt=(pix.group(1).split("(")[0] if pix else "?"),
        color_transfer=transfer,
        has_audio=bool(aline),
        audio_codec=_VIDEO.search(aline).group(1) if aline and _VIDEO.search(aline) else None,
        audio_rate=int(arate.group(2)) if arate else None,
    )


def video_filter(info: MediaInfo, extra: str = "") -> str:
    """Colour-correct filter chain for this source, plus any caller-supplied stage."""
    chain = TONEMAP_CHAIN if info.is_hdr else "format=yuv420p"
    return f"{chain},{extra}" if extra else chain


def stream_copy_ok(info: MediaInfo) -> bool:
    """Can prefix/suffix be copied rather than re-encoded?

    Only when the source is already what the output must be: 8-bit SDR H.264.
    An HDR HEVC source cannot be copied into an SDR H.264 output alongside a
    generated SDR segment without a visible colour and codec discontinuity.
    """
    return (not info.is_hdr and info.video_codec == "h264"
            and info.pix_fmt.startswith("yuv420p"))


def extract_frame(video: Path, timestamp: float, out: Path,
                  info: MediaInfo | None = None, width: int | None = None) -> Path:
    """One tone-mapped still, for use as a first/last frame reference."""
    info = info or probe(video)
    out.parent.mkdir(parents=True, exist_ok=True)
    _run([FFMPEG, "-hide_banner", "-loglevel", "error", "-y",
          "-ss", f"{max(0.0, timestamp):.3f}", "-i", str(video),
          "-frames:v", "1",
          "-vf", video_filter(info, f"scale={width}:-2" if width else ""),
          "-q:v", "2", str(out)], timeout=300)
    return out


def extract_clip(video: Path, start: float, end: float, out: Path,
                 info: MediaInfo | None = None, *, width: int | None = None,
                 fps: float | None = None, with_audio: bool = True,
                 crf: int = 18) -> Path:
    """Cut [start, end) and normalise it to SDR H.264 yuv420p.

    Used both for the segment handed to a video-to-video model and for the
    prefix/suffix pieces of the splice, so that every piece entering `concat`
    already shares one codec, pixel format and colour space.
    """
    info = info or probe(video)
    out.parent.mkdir(parents=True, exist_ok=True)
    extra = []
    if width:
        extra.append(f"scale={width}:-2")
    if fps:
        extra.append(f"fps={fps}")
    cmd = [FFMPEG, "-hide_banner", "-loglevel", "error", "-y",
           "-ss", f"{max(0.0, start):.3f}", "-i", str(video),
           "-t", f"{max(0.01, end - start):.3f}",
           "-vf", video_filter(info, ",".join(extra)),
           "-c:v", "libx264", "-crf", str(crf), "-preset", "medium",
           "-pix_fmt", "yuv420p", "-movflags", "+faststart"]
    if with_audio and info.has_audio:
        cmd += ["-c:a", "aac", "-b:a", "128k", "-ar", "48000", "-ac", "2"]
    else:
        cmd += ["-an"]
    cmd.append(str(out))
    _run(cmd)
    return out


def build_analysis_proxy(video: Path, out: Path, info: MediaInfo | None = None,
                         *, width: int = 640, fps: float = 6.0,
                         start: float | None = None, end: float | None = None) -> Path:
    """A small SDR copy for Stage 1.

    The sources run to 272 MB; a chat-completions request carrying that as base64
    is neither affordable nor accepted. 640px at 6 fps keeps hand position and
    tool identity legible while landing in single-digit megabytes.
    """
    info = info or probe(video)
    out.parent.mkdir(parents=True, exist_ok=True)
    cmd = [FFMPEG, "-hide_banner", "-loglevel", "error", "-y"]
    if start is not None:
        cmd += ["-ss", f"{start:.3f}"]
    cmd += ["-i", str(video)]
    if end is not None:
        cmd += ["-t", f"{max(0.1, end - (start or 0.0)):.3f}"]
    cmd += ["-vf", video_filter(info, f"scale={width}:-2,fps={fps}"),
            "-c:v", "libx264", "-crf", "30", "-preset", "veryfast",
            "-pix_fmt", "yuv420p", "-an", "-movflags", "+faststart", str(out)]
    _run(cmd)
    return out


def fit_duration(clip: Path, target_s: float, out: Path, *, fps: float,
                 tolerance: float = 0.04) -> Path:
    """Make `clip` exactly `target_s` long, so the splice restores total runtime.

    Too long is trimmed. Too short is extended by holding the final frame — a
    still hold at the end of a completed action reads as the technician pausing
    on finished work, which is what the surrounding footage shows anyway, and it
    keeps the erroneous artifact on screen for the grader.
    """
    info = probe(clip)
    delta = info.duration_s - target_s
    if abs(delta) <= tolerance:
        shutil.copyfile(clip, out)
        return out
    out.parent.mkdir(parents=True, exist_ok=True)
    if delta > 0:
        _run([FFMPEG, "-hide_banner", "-loglevel", "error", "-y", "-i", str(clip),
              "-t", f"{target_s:.3f}", "-c:v", "libx264", "-crf", "18",
              "-preset", "medium", "-pix_fmt", "yuv420p", "-an", str(out)])
        return out
    _run([FFMPEG, "-hide_banner", "-loglevel", "error", "-y", "-i", str(clip),
          "-vf", f"tpad=stop_mode=clone:stop_duration={-delta:.3f},fps={fps}",
          "-t", f"{target_s:.3f}", "-c:v", "libx264", "-crf", "18",
          "-preset", "medium", "-pix_fmt", "yuv420p", "-an", str(out)])
    return out


def mux_audio(video: Path, audio_source: Path, start: float, duration: float,
              out: Path) -> Path:
    """Lay the original ambient audio for this window over a silent segment.

    The generated picture has no sound, and a silent gap in the middle of a
    workshop recording is itself a discontinuity. Reusing the source audio for
    the same time window keeps the track continuous across the splice.
    """
    out.parent.mkdir(parents=True, exist_ok=True)
    _run([FFMPEG, "-hide_banner", "-loglevel", "error", "-y",
          "-i", str(video), "-ss", f"{start:.3f}", "-t", f"{duration:.3f}",
          "-i", str(audio_source),
          "-map", "0:v:0", "-map", "1:a:0?", "-shortest",
          "-c:v", "copy", "-c:a", "aac", "-b:a", "128k", "-ar", "48000", "-ac", "2",
          str(out)])
    return out


def concat(parts: list[Path], out: Path) -> Path:
    """Join normalised parts. Demuxer concat, so nothing is re-encoded again."""
    parts = [p for p in parts if p is not None and Path(p).exists()]
    if not parts:
        raise MediaError("nothing to concatenate")
    out.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False) as handle:
        for part in parts:
            handle.write(f"file '{Path(part).resolve()}'\n")
        listing = handle.name
    try:
        _run([FFMPEG, "-hide_banner", "-loglevel", "error", "-y", "-f", "concat",
              "-safe", "0", "-i", listing, "-c", "copy",
              "-movflags", "+faststart", str(out)])
    finally:
        Path(listing).unlink(missing_ok=True)
    return out


def is_playable(path: Path, *, min_duration: float = 0.5) -> tuple[bool, str]:
    """Decode the whole file and reject blank output.

    A model can return a file that demuxes but decodes to black, and a manifest
    row pointing at black frames is worse than a missing row. `blackdetect` over
    a full decode catches both that and truncated files.
    """
    if not Path(path).exists():
        return False, "file missing"
    proc = subprocess.run(
        [FFMPEG, "-hide_banner", "-v", "info", "-i", str(path),
         "-vf", "blackdetect=d=0.5:pix_th=0.10", "-f", "null", "-"],
        capture_output=True, text=True)
    if proc.returncode != 0:
        return False, "does not decode cleanly"
    info = probe(Path(path))
    if info.duration_s < min_duration:
        return False, f"too short ({info.duration_s:.2f}s)"
    black = sum(float(m) for m in re.findall(r"black_duration:(\d+\.?\d*)", proc.stderr))
    if info.duration_s and black / info.duration_s > 0.30:
        return False, f"{black:.1f}s of {info.duration_s:.1f}s is black"
    return True, "ok"


def splice(source: Path, generated: Path, start: float, end: float, out: Path,
           info: MediaInfo | None = None, *, work_dir: Path) -> dict:
    """Rebuild the source with [start, end) replaced by `generated`.

    Returns a record of what was actually done, which the metadata file keeps so
    a reviewer can see whether a given output was stream-copied or re-encoded.
    """
    info = info or probe(source)
    work_dir.mkdir(parents=True, exist_ok=True)
    fps = info.fps or 30.0
    target = end - start

    sized = fit_duration(generated, target, work_dir / "segment_fitted.mp4", fps=fps)
    voiced = (mux_audio(sized, source, start, target, work_dir / "segment_audio.mp4")
              if info.has_audio else sized)

    parts: list[Path] = []
    if start > 0.05:
        parts.append(extract_clip(source, 0.0, start, work_dir / "prefix.mp4", info))
    parts.append(voiced)
    if end < info.duration_s - 0.05:
        parts.append(extract_clip(source, end, info.duration_s, work_dir / "suffix.mp4", info))

    # Re-normalise every part through one encoder pass so concat's stream copy is
    # safe: fit_duration and mux_audio can leave the middle part with a different
    # audio layout from the ends.
    normalised = []
    for index, part in enumerate(parts):
        dest = work_dir / f"norm_{index:02d}.mp4"
        pinfo = probe(part)
        cmd = [FFMPEG, "-hide_banner", "-loglevel", "error", "-y", "-i", str(part),
               "-vf", f"scale={info.width}:{info.height},fps={fps},format=yuv420p",
               "-c:v", "libx264", "-crf", "18", "-preset", "medium",
               "-pix_fmt", "yuv420p", "-video_track_timescale", "30000"]
        if info.has_audio:
            if pinfo.has_audio:
                cmd += ["-c:a", "aac", "-b:a", "128k", "-ar", "48000", "-ac", "2"]
            else:
                cmd += ["-f", "lavfi", "-i", "anullsrc=r=48000:cl=stereo",
                        "-shortest", "-c:a", "aac", "-b:a", "128k"]
        else:
            cmd += ["-an"]
        cmd.append(str(dest))
        _run(cmd)
        normalised.append(dest)

    concat(normalised, out)
    final = probe(out)
    return {
        "edit_window": {"start": round(start, 3), "end": round(end, 3)},
        "source_duration_s": info.duration_s,
        "output_duration_s": final.duration_s,
        "duration_preserved": abs(final.duration_s - info.duration_s) < 0.5,
        "parts": len(normalised),
        "stream_copied": False,
        "tone_mapped": info.is_hdr,
        "output": final.as_dict(),
    }
