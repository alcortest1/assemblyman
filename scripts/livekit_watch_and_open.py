#!/usr/bin/env python3
"""Wait for an AssemblyMan session to go live, then open it in LiveKit Meet.

The app mints a fresh room code every session, so this polls the LiveKit server instead of
asking you to read the code off the phone. It waits for a room whose operator is actually
connected, mints a viewer token for it, and opens Meet.

    ./scripts/livekit_watch_and_open.py            # wait up to 5 minutes
    ./scripts/livekit_watch_and_open.py --timeout 900

Credentials come from livekit.txt at the repo root (gitignored). Development convenience —
it signs with the project API secret, so keep it on your workstation.
"""

import argparse
import base64
import hashlib
import hmac
import json
import pathlib
import random
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
CREDENTIALS_FILE = REPO_ROOT / "livekit.txt"


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


def mint(api_key, api_secret, claims_extra, identity, ttl=6 * 3600):
    now = int(time.time())
    claims = {"iss": api_key, "sub": identity, "nbf": now - 30, "exp": now + ttl, **claims_extra}
    encode = lambda o: b64url(json.dumps(o, sort_keys=True, separators=(",", ":")).encode())
    signing_input = f'{encode({"alg": "HS256", "typ": "JWT"})}.{encode(claims)}'
    signature = hmac.new(api_secret.encode(), signing_input.encode(), hashlib.sha256).digest()
    return f"{signing_input}.{b64url(signature)}"


def list_rooms(host, api_key, api_secret):
    token = mint(api_key, api_secret, {"video": {"roomList": True}}, "cli-watch", ttl=900)
    request = urllib.request.Request(
        f"https://{host}/twirp/livekit.RoomService/ListRooms",
        data=b"{}",
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
    )
    try:
        return json.load(urllib.request.urlopen(request, timeout=15)).get("rooms", [])
    except (urllib.error.URLError, TimeoutError) as error:
        # A blip in polling should not end the watch.
        print(f"  (poll failed: {error})", flush=True)
        return []


def room_value(room, snake_case, camel_case, default=0):
    """Read RoomService JSON from either its protobuf or legacy JSON spelling."""
    return room.get(snake_case, room.get(camel_case, default))


def pick(rooms):
    """The live session: someone publishing beats someone merely connected beats newest."""
    for predicate in (
        lambda r: int(room_value(r, "num_publishers", "numPublishers")) > 0,
        lambda r: int(room_value(r, "num_participants", "numParticipants")) > 0,
    ):
        matches = [r for r in rooms if predicate(r)]
        if matches:
            return max(
                matches,
                key=lambda r: int(room_value(r, "creation_time", "creationTime")),
            ), True
    if rooms:
        return max(
            rooms,
            key=lambda r: int(room_value(r, "creation_time", "creationTime")),
        ), False
    return None, False


def main():
    parser = argparse.ArgumentParser(description="Open the live AssemblyMan session in LiveKit Meet.")
    parser.add_argument("--timeout", type=int, default=300, help="seconds to wait (default 300)")
    parser.add_argument("--interval", type=float, default=3, help="seconds between polls")
    parser.add_argument("--print-only", action="store_true", help="print the URL, do not open it")
    parser.add_argument(
        "--any",
        action="store_true",
        help="open as soon as a room exists, without waiting for the operator to be in it. "
        "Useful when diagnosing a relay that connects and drops — joining as a viewer also "
        "keeps the room alive so you can watch the operator arrive.",
    )
    parser.add_argument(
        "--newer-than",
        type=int,
        default=0,
        help="ignore rooms created before this unix time, so a stale room is not reopened",
    )
    args = parser.parse_args()

    credentials = read_credentials()
    url = credentials["LIVEKIT_URL"]
    host = url.replace("wss://", "").replace("ws://", "")
    api_key, api_secret = credentials["LIVEKIT_API_KEY"], credentials["LIVEKIT_API_SECRET"]

    print(f"Watching {host} — start a session on the phone.", flush=True)
    deadline = time.time() + args.timeout
    announced = set()

    while time.time() < deadline:
        rooms = list_rooms(host, api_key, api_secret)
        if args.newer_than:
            rooms = [
                r for r in rooms
                if int(room_value(r, "creation_time", "creationTime")) >= args.newer_than
            ]
        room, is_occupied = pick(rooms)

        if room is not None:
            name = room["name"]
            if name not in announced:
                announced.add(name)
                print(
                    f"  saw {name}: participants="
                    f"{room_value(room, 'num_participants', 'numParticipants')} "
                    f"publishers={room_value(room, 'num_publishers', 'numPublishers')}",
                    flush=True,
                )
            # Normally wait for the operator to actually be in the room — opening earlier lands
            # the viewer in an empty room and reads as a failure. `--any` overrides that.
            if is_occupied or args.any:
                token = mint(
                    api_key,
                    api_secret,
                    {
                        "video": {
                            "roomJoin": True,
                            "room": name,
                            "canPublish": True,
                            "canSubscribe": True,
                            "canPublishData": True,
                        }
                    },
                    f"viewer-{random.randint(1000, 9999)}",
                )
                meet = "https://meet.livekit.io/custom?" + urllib.parse.urlencode(
                    {"liveKitUrl": url, "token": token}
                )
                display = f"{name[:3]}-{name[3:]}" if len(name) == 6 else name
                publishers = room_value(room, "num_publishers", "numPublishers")
                print(f"\nLIVE: {display}  publishers={publishers}")
                print(meet)
                if not args.print_only:
                    subprocess.run(["open", meet], check=False)
                    print("\nOpened in your browser.")
                return 0

        time.sleep(args.interval)

    print(f"\nTimed out after {args.timeout}s — no session went live.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
