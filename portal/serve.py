#!/usr/bin/env python3
"""Serve the portal, and make Video assessment's Run button real.

Standard library only, and everything it touches lives under `portal/`. The page
still owns the grading — it builds the prompt, parses the reply, draws the grid.
What a static server cannot give it is a route to the Gemini arms, because that
route needs an API key and a key in a web page is published, not used. So this
wrapper holds the key server-side and does exactly three things:

    GET  /api/health      — is a key configured?
    POST /api/video/run   — one arm, one call: attach the named frames, forward
                            to the Gemini API, return the raw reply text
    POST /api/video/save  — persist the grid the page built, so the run
                            survives a reload

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
import urllib.request
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

PORTAL = Path(__file__).resolve().parent
DATA = PORTAL / "data"
RUNS = DATA / "video_runs"

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

    def log_message(self, fmt, *args):  # static chatter off, API lines on
        if args and "/api/" in str(args[0]):
            super().log_message(fmt, *args)

    def send_json(self, payload, status: int = 200) -> None:
        body = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):  # noqa: N802
        if self.path.split("?")[0] == "/api/health":
            return self.send_json({"server": True, "arms": arms()})
        return super().do_GET()

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
