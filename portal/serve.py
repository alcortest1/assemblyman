#!/usr/bin/env python3
"""Serve the portal, and make Video assessment's Run button real.

Standard library only, and everything it touches lives under `portal/`. The page
still owns the grading — it builds the prompt, parses the reply, draws the grid.
What a static server cannot give it is a route to the Gemini arms, because that
route needs an API key and a key in a web page is published, not used. So this
wrapper holds the key server-side and does exactly three things:

    GET  /api/health      — is a key configured?
    GET  /videos/<ACS>/<clip>.mp4
                          — the source clip out of alcor_agents/data/videos/,
                            with byte-range support so the player can seek
    POST /api/video/run   — one arm, one call: attach the named frames, forward
                            to the Gemini API, return the raw reply text
    POST /api/video/save  — persist the grid the page built, so the run
                            survives a reload
    POST /api/video/focus — persist a clip's area-of-focus track (the keyframed
                            crop box the Videos tab edits)

The key comes from GEMINI_API_KEY in the environment or `portal/.env` (one line:
GEMINI_API_KEY=...). Runs land in `data/video_runs/<ACS>.json`, keyed by the
subtask's sheet slug — a separate file from the built extract, so re-running
`build_portal_data.py` does not erase them.

    python3 serve.py            # http://localhost:8080
    python3 serve.py --port 9000
"""

from __future__ import annotations

import base64
import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

PORTAL = Path(__file__).resolve().parent
DATA = PORTAL / "data"
RUNS = DATA / "video_runs"
FOCUS = DATA / "focus_tracks"
# Source clips stay in the working tree — the extract only carries their frames.
VIDEOS = PORTAL.parent / "alcor_agents" / "data" / "videos"

GEMINI_API = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
OPENROUTER_API = "https://openrouter.ai/api/v1/chat/completions"

# The four hosted arms, in the photo grid's column order. Which are callable
# depends on which key is configured: an OpenRouter key routes all four, a
# Gemini key alone routes the two Gemini arms through the Developer API.
HOSTED_ARMS = [
    ("anthropic/claude-opus-5", "Opus 5"),
    ("google/gemini-3.1-pro-preview", "Gemini 3.1 Pro"),
    ("google/gemini-3.6-flash", "Gem 3.6 Flash"),
    ("openai/gpt-5.6-sol", "GPT-5.6 Sol"),
]

# Everything that lands in a filesystem path or a URL is shape-checked first.
SAFE_CODE = re.compile(r"^[A-Za-z0-9.]{1,32}$")
SAFE_CLIP = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
SAFE_FRAME = re.compile(r"^t\d{6}_\d{2}\.jpg$")
SAFE_SHEET = re.compile(r"^[a-z0-9_-]{1,80}$")
VIDEO_URL = re.compile(r"^/videos/([A-Za-z0-9.]{1,32})/([A-Za-z0-9_-]{1,64})\.mp4$")


def read_key(name: str) -> str | None:
    """A key, from the environment or portal/.env — never from the repo."""
    if os.environ.get(name):
        return os.environ[name]
    env = PORTAL / ".env"
    if env.exists():
        for line in env.read_text().splitlines():
            line = line.strip()
            if line.startswith(name + "="):
                return line.split("=", 1)[1].strip().strip("\"'") or None
    return None


def gemini_key() -> str | None:
    return read_key("GEMINI_API_KEY") or read_key("GOOGLE_API_KEY")


def openrouter_key() -> str | None:
    return read_key("OPENROUTER_API_KEY")


def arms() -> list[dict]:
    """The arms this server can route right now, with the route each takes."""
    if openrouter_key():
        return [{"id": mid, "label": label, "route": "openrouter"}
                for mid, label in HOSTED_ARMS]
    if gemini_key():
        return [{"id": mid, "label": label, "route": "gemini"}
                for mid, label in HOSTED_ARMS if mid.startswith("google/")]
    return []


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(PORTAL), **kwargs)

    def log_message(self, fmt, *args):  # static chatter off, API and clip lines on
        first = str(args[0]) if args else ""
        if "/api/" in first or "/videos/" in first:
            super().log_message(fmt, *args)

    def send_json(self, payload, status: int = 200) -> None:
        body = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):  # noqa: N802
        path = urllib.parse.unquote(self.path.split("?")[0])
        if path == "/api/health":
            return self.send_json({"server": True, "arms": arms(),
                                   "videos": VIDEOS.is_dir()})
        clip = VIDEO_URL.match(path)
        if clip:
            return self.send_video(clip.group(1), clip.group(2))
        return super().do_GET()

    def send_video(self, code: str, clip: str) -> None:
        """One source clip, honouring Range — seeking needs 206s, not one long 200."""
        path = VIDEOS / code / f"{clip}.mp4"
        if not path.is_file():
            return self.send_error(404, "No such clip")
        size = path.stat().st_size
        start, end = 0, size - 1
        rng = self.headers.get("Range")
        if rng:
            m = re.match(r"^bytes=(\d*)-(\d*)$", rng.strip())
            if not m or not (m.group(1) or m.group(2)):
                rng = None  # a shape we do not speak — answer with the whole file
            elif m.group(1):
                start = int(m.group(1))
                if m.group(2):
                    end = min(int(m.group(2)), size - 1)
            else:  # bytes=-N — the final N bytes, how players find the moov atom
                start = max(0, size - int(m.group(2)))
            if rng and (start > end or start >= size):
                self.send_response(416)
                self.send_header("Content-Range", f"bytes */{size}")
                self.end_headers()
                return
        self.send_response(206 if rng else 200)
        self.send_header("Content-Type", "video/mp4")
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Content-Length", str(end - start + 1))
        if rng:
            self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        self.end_headers()
        try:
            with path.open("rb") as f:
                f.seek(start)
                remaining = end - start + 1
                while remaining > 0:
                    chunk = f.read(min(65536, remaining))
                    if not chunk:
                        break
                    self.wfile.write(chunk)
                    remaining -= len(chunk)
        except (BrokenPipeError, ConnectionResetError):
            pass  # players abandon ranges mid-flight as a matter of course

    def do_POST(self):  # noqa: N802
        route = self.path.split("?")[0]
        try:
            length = int(self.headers.get("Content-Length") or 0)
            body = json.loads(self.rfile.read(length) or b"{}")
        except Exception:
            return self.send_error(400, "Malformed JSON body")
        if not isinstance(body, dict):
            return self.send_error(400, "Body must be a JSON object")
        if route == "/api/video/run":
            return self.video_run(body)
        if route == "/api/video/save":
            return self.video_save(body)
        if route == "/api/video/focus":
            return self.video_focus(body)
        return self.send_error(404)

    def video_run(self, body: dict) -> None:
        model = str(body.get("model") or "")
        arm = next((a for a in arms() if a["id"] == model), None)
        if not arm:
            return self.send_json({"error": "no_route", "message":
                "No key routes this arm — set OPENROUTER_API_KEY (all four) or "
                "GEMINI_API_KEY (the Gemini two) in portal/.env and restart serve.py."},
                status=409)

        code = str(body.get("task_code") or "")
        clip = str(body.get("clip") or "")
        frames = body.get("frames") or []
        system = str(body.get("system") or "")
        user_text = str(body.get("user_text") or "")
        if not (SAFE_CODE.match(code) and SAFE_CLIP.match(clip)):
            return self.send_error(400, "task_code and clip are required")
        if not system or not user_text:
            return self.send_error(400, "system and user_text are required")
        if not (isinstance(frames, list) and 0 < len(frames) <= 120
                and all(isinstance(f, str) and SAFE_FRAME.match(f) for f in frames)):
            return self.send_error(400, "frames must be 1..120 tNNNNNN_NN.jpg names")

        # The full frame where the tree has one, the thumb where it does not —
        # the same preference the screen draws with.
        images = []
        for name in frames:
            path = DATA / "frames" / code / clip / name
            if not path.is_file():
                path = DATA / "thumbs" / code / clip / name
            if not path.is_file():
                return self.send_json({"error": "missing_frame", "message": name}, status=400)
            images.append(base64.b64encode(path.read_bytes()).decode())

        # Same call, two dialects. The payload shape differs; the prompt, the
        # frames and their order do not — that is what keeps a Gemini-routed and
        # an OpenRouter-routed verdict comparable.
        if arm["route"] == "gemini":
            payload = {
                "system_instruction": {"parts": [{"text": system}]},
                "contents": [{"role": "user", "parts":
                    [{"text": user_text}] + [{"inline_data": {
                        "mime_type": "image/jpeg", "data": b64}} for b64 in images]}],
                "generationConfig": {"temperature": 0, "maxOutputTokens": 4000},
            }
            url = GEMINI_API.format(model=model.split("/", 1)[1])
            # The key rides a header, never the URL — URLs land in logs.
            headers = {"Content-Type": "application/json", "x-goog-api-key": gemini_key()}
        else:
            content = [{"type": "text", "text": user_text}] + [
                {"type": "image_url", "image_url": {
                    "url": "data:image/jpeg;base64," + b64}} for b64 in images]
            payload = {
                "model": model, "max_tokens": 4000, "temperature": 0,
                "messages": [{"role": "system", "content": system},
                             {"role": "user", "content": content}],
            }
            url = OPENROUTER_API
            headers = {"Content-Type": "application/json",
                       "Authorization": f"Bearer {openrouter_key()}",
                       "HTTP-Referer": "http://127.0.0.1:8080",
                       "X-Title": "AIM Inspector portal"}

        request = urllib.request.Request(url, data=json.dumps(payload).encode(),
                                         headers=headers)
        try:
            with urllib.request.urlopen(request, timeout=300) as response:
                data = json.load(response)
        except urllib.error.HTTPError as exc:
            detail = ""
            try:
                detail = (json.load(exc).get("error") or {}).get("message", "")
            except Exception:
                pass
            return self.send_json({"error": f"http_{exc.code}",
                                   "message": detail or str(exc.reason)}, status=502)
        except Exception as exc:  # noqa: BLE001 — the reason goes to the screen
            return self.send_json({"error": "request_failed", "message": str(exc)[:300]},
                                  status=502)

        if arm["route"] == "gemini":
            candidate = (data.get("candidates") or [{}])[0]
            text = "".join(p.get("text", "")
                           for p in ((candidate.get("content") or {}).get("parts") or []))
        else:
            if isinstance(data, dict) and data.get("error"):
                return self.send_json({"error": "api_error",
                    "message": str(data["error"].get("message", ""))[:300]}, status=502)
            text = (((data.get("choices") or [{}])[0].get("message") or {})
                    .get("content") or "")
        return self.send_json({"text": text})

    def video_save(self, body: dict) -> None:
        code = str(body.get("task_code") or "")
        sheet = str(body.get("sheet") or "")
        # Two stores, same shape: a live photo grid and a live video grid are
        # both page-built runs, differing only in the evidence they grade.
        kind = str(body.get("kind") or "video")
        grid = body.get("grid")
        if kind not in ("video", "photo"):
            return self.send_error(400, "kind must be video or photo")
        if not (SAFE_CODE.match(code) and SAFE_SHEET.match(sheet) and isinstance(grid, dict)):
            return self.send_error(400, "task_code, sheet and grid are required")
        directory = DATA / f"{kind}_runs"
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / f"{code}.json"
        try:
            store = json.loads(path.read_text()) if path.exists() else {}
        except (OSError, json.JSONDecodeError):
            store = {}
        store[sheet] = grid
        path.write_text(json.dumps(store, indent=1))
        return self.send_json({"saved": True, "path": f"data/{kind}_runs/{code}.json"})

    def video_focus(self, body: dict) -> None:
        """Persist a clip's area-of-focus track: keyframes of a crop box.

        Keys are {t, cx, cy, w} — seconds, then centre and width as fractions of
        the frame. Height is width: the box shares the clip's aspect, so one
        number is the whole zoom. Lands in data/focus_tracks/<ACS>.json keyed by
        clip, which the page reads back as plain static data.
        """
        code = str(body.get("task_code") or "")
        clip = str(body.get("clip") or "")
        track = body.get("track")
        if not (SAFE_CODE.match(code) and SAFE_CLIP.match(clip) and isinstance(track, dict)):
            return self.send_error(400, "task_code, clip and track are required")
        keys = track.get("keys")

        def num(v) -> bool:
            return isinstance(v, (int, float)) and not isinstance(v, bool)

        if not (isinstance(keys, list) and len(keys) <= 200
                and all(isinstance(k, dict)
                        and all(num(k.get(f)) for f in ("t", "cx", "cy", "w"))
                        for k in keys)):
            return self.send_error(400, "track.keys must be <=200 {t,cx,cy,w} entries")
        FOCUS.mkdir(parents=True, exist_ok=True)
        path = FOCUS / f"{code}.json"
        try:
            store = json.loads(path.read_text()) if path.exists() else {}
        except (OSError, json.JSONDecodeError):
            store = {}
        store[clip] = {"keys": [
            {f: round(float(k[f]), 4) for f in ("t", "cx", "cy", "w")}
            for k in sorted(keys, key=lambda k: k["t"])
        ]}
        path.write_text(json.dumps(store, indent=1))
        return self.send_json({"saved": True, "path": f"data/focus_tracks/{code}.json"})


def main() -> int:
    port = int(sys.argv[sys.argv.index("--port") + 1]) if "--port" in sys.argv else 8080
    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    key = "key configured" if gemini_key() else "NO KEY — Run stays inert " \
        "(add GEMINI_API_KEY=... to portal/.env)"
    print(f"portal → http://localhost:{port}  · {key}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
