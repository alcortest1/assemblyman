#!/usr/bin/env python3
"""Mint a viewer token so you can watch an AssemblyMan session from a browser.

The iOS app shows a room code on the live session screen (e.g. ABC-DEF). Pass it here and
open the printed URL — it drops you straight into LiveKit Meet's custom tab, already filled in.

    ./scripts/livekit_viewer_token.py ABC-DEF

Credentials are read from livekit.txt at the repo root, which is gitignored. The token is
scoped to that one room and expires in six hours.

This is a development convenience. It signs with the project API secret, so it belongs on a
workstation, never in anything you ship.
"""

import argparse
import base64
import hashlib
import hmac
import json
import pathlib
import random
import sys
import time
import urllib.parse

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
CREDENTIALS_FILE = REPO_ROOT / "livekit.txt"

# Same alphabet the app generates from — ambiguous glyphs removed.
ROOM_ALPHABET = set("ABCDEFGHJKMNPQRSTUVWXYZ23456789")


def read_credentials():
    if not CREDENTIALS_FILE.exists():
        sys.exit(f"No credentials at {CREDENTIALS_FILE}. Expected LIVEKIT_URL/API_KEY/API_SECRET.")

    values = {}
    for line in CREDENTIALS_FILE.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        values[key.strip()] = value.strip()

    missing = [k for k in ("LIVEKIT_URL", "LIVEKIT_API_KEY", "LIVEKIT_API_SECRET") if k not in values]
    if missing:
        sys.exit(f"{CREDENTIALS_FILE} is missing: {', '.join(missing)}")
    return values


def normalise_room(code):
    """Match the app's RoomCode.roomName: uppercase, separators dropped."""
    room = "".join(c for c in code.upper() if c in ROOM_ALPHABET)
    if len(room) != 6:
        sys.exit(f"'{code}' is not a room code — expected exactly 6 characters like ABC-DEF.")
    return room


def b64url(raw):
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def mint(api_key, api_secret, room, identity, ttl_seconds):
    now = int(time.time())
    header = {"alg": "HS256", "typ": "JWT"}
    claims = {
        "iss": api_key,
        "sub": identity,
        "nbf": now - 30,
        "exp": now + ttl_seconds,
        "name": "Viewer",
        "video": {
            "roomJoin": True,
            "room": room,
            # A viewer watches and can speak back, but publishes no video of its own.
            "canPublish": True,
            "canSubscribe": True,
            "canPublishData": True,
        },
    }
    encode = lambda obj: b64url(json.dumps(obj, sort_keys=True, separators=(",", ":")).encode())
    signing_input = f"{encode(header)}.{encode(claims)}"
    signature = hmac.new(api_secret.encode(), signing_input.encode(), hashlib.sha256).digest()
    return f"{signing_input}.{b64url(signature)}"


def main():
    parser = argparse.ArgumentParser(description="Mint a LiveKit viewer token for a room code.")
    parser.add_argument("room_code", help="the code shown in the app, e.g. ABC-DEF")
    parser.add_argument("--ttl", type=int, default=6 * 3600, help="token lifetime in seconds")
    args = parser.parse_args()

    credentials = read_credentials()
    room = normalise_room(args.room_code)
    url = credentials["LIVEKIT_URL"]
    token = mint(
        credentials["LIVEKIT_API_KEY"],
        credentials["LIVEKIT_API_SECRET"],
        room,
        f"viewer-{random.randint(1000, 9999)}",
        args.ttl,
    )

    meet = "https://meet.livekit.io/custom?" + urllib.parse.urlencode(
        {"liveKitUrl": url, "token": token}
    )

    print(f"Room:   {room}")
    print(f"URL:    {url}")
    print(f"Token:  {token}")
    print()
    print("Open this to join — LiveKit Meet, pre-filled:")
    print(meet)


if __name__ == "__main__":
    main()
