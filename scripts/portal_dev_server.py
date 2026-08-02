#!/usr/bin/env python3
"""Serve the AssemblyMan portal with a token endpoint, for local testing.

`python3 -m http.server` is enough for the portal's static screens, but joining a real room
needs a token, and minting one in the browser would put the API secret in page source. This
serves `web/` and adds a single `POST /api/token` that signs server-side.

    ./scripts/portal_dev_server.py            # http://localhost:8777
    ./scripts/portal_dev_server.py --port 9000

Credentials come from livekit.txt at the repo root (gitignored). Development only — it binds
loopback by default and has no authentication, so anyone who can reach it can mint a token.
The deployed equivalent is api/token.js, which reads the same values from environment
variables.
"""

import argparse
import base64
import functools
import hashlib
import hmac
import http.server
import json
import pathlib
import secrets
import socketserver
import sys
import time

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
WEB_ROOT = REPO_ROOT / "web"
CREDENTIALS_FILE = REPO_ROOT / "livekit.txt"

# Matches the app's RoomCode alphabet.
ROOM_CHARS = set("ABCDEFGHJKMNPQRSTUVWXYZ23456789")
ROOM_CODE_LENGTH = 6


def read_credentials():
    if not CREDENTIALS_FILE.exists():
        sys.exit(f"No credentials at {CREDENTIALS_FILE}.")
    values = {}
    for line in CREDENTIALS_FILE.read_text().splitlines():
        line = line.strip()
        if "=" in line and not line.startswith("#"):
            key, _, value = line.partition("=")
            values[key.strip()] = value.strip()
    missing = [k for k in ("LIVEKIT_URL", "LIVEKIT_API_KEY", "LIVEKIT_API_SECRET") if k not in values]
    if missing:
        sys.exit(f"{CREDENTIALS_FILE} is missing: {', '.join(missing)}")
    return values


def b64url(raw):
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def mint(api_key, api_secret, room, identity, ttl=6 * 3600):
    now = int(time.time())
    claims = {
        "iss": api_key,
        "sub": identity,
        "nbf": now - 30,
        "exp": now + ttl,
        "name": "Portal viewer",
        "video": {
            "roomJoin": True,
            "room": room,
            # Portal participants are watch-only. The manual Meet helper grants microphone
            # access separately for interactive testing.
            "canPublish": False,
            "canSubscribe": True,
            "canPublishData": True,
        },
    }
    encode = lambda obj: b64url(json.dumps(obj, sort_keys=True, separators=(",", ":")).encode())
    signing_input = f'{encode({"alg": "HS256", "typ": "JWT"})}.{encode(claims)}'
    signature = hmac.new(api_secret.encode(), signing_input.encode(), hashlib.sha256).digest()
    return f"{signing_input}.{b64url(signature)}"


class Handler(http.server.SimpleHTTPRequestHandler):
    credentials = {}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(WEB_ROOT), **kwargs)

    def _json(self, status, payload):
        body = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        # Development diagnostics: the portal reports transport failures here so they land in
        # the terminal rather than only in a browser console nobody is watching.
        if self.path.rstrip("/") == "/api/log":
            try:
                length = int(self.headers.get("Content-Length", 0))
                entry = json.loads(self.rfile.read(length) or b"{}")
            except (ValueError, TypeError):
                entry = {}
            print(f"  [portal] {entry.get('level', 'info')}: {entry.get('message', '')}", flush=True)
            self._json(200, {"ok": True})
            return

        if self.path.rstrip("/") != "/api/token":
            self._json(404, {"error": "not found"})
            return

        try:
            length = int(self.headers.get("Content-Length", 0))
            request = json.loads(self.rfile.read(length) or b"{}")
        except (ValueError, TypeError):
            self._json(400, {"error": "malformed JSON"})
            return

        room = "".join(c for c in str(request.get("room", "")).upper() if c in ROOM_CHARS)
        if len(room) != ROOM_CODE_LENGTH:
            self._json(400, {"error": "room code must contain exactly 6 valid characters"})
            return

        # LiveKit identities are unique per room. Never let the caller impersonate
        # `phone-<room>` and evict the operator.
        identity = f"portal-{secrets.token_hex(8)}"
        token = mint(
            self.credentials["LIVEKIT_API_KEY"],
            self.credentials["LIVEKIT_API_SECRET"],
            room,
            identity,
        )
        print(f"  token issued: room={room} identity={identity}", flush=True)
        self._json(200, {"serverUrl": self.credentials["LIVEKIT_URL"], "token": token, "room": room})

    def end_headers(self):
        # The portal is edited constantly during a session; never serve it from cache.
        self.send_header("Cache-Control", "no-store")
        super().end_headers()

    def log_message(self, fmt, *args):
        if "/api/token" not in (args[0] if args else ""):
            return  # Static hits are noise; token requests are logged above.
        super().log_message(fmt, *args)


def main():
    parser = argparse.ArgumentParser(description="Serve the portal with a LiveKit token endpoint.")
    parser.add_argument("--port", type=int, default=8777)
    parser.add_argument(
        "--host",
        default="127.0.0.1",
        help="bind address; use 0.0.0.0 to reach it from another device on the LAN",
    )
    args = parser.parse_args()

    Handler.credentials = read_credentials()

    socketserver.TCPServer.allow_reuse_address = True
    with socketserver.TCPServer((args.host, args.port), Handler) as server:
        print(f"Portal:  http://{args.host}:{args.port}")
        print(f"LiveKit: {Handler.credentials['LIVEKIT_URL']}")
        print("Enter the room code from the app's live session screen.\n")
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            print("\nstopped")


if __name__ == "__main__":
    main()
