"""Serve one clip over a short-lived HTTPS URL, for video-to-video generation.

This module exists because of one hard constraint: OpenRouter accepts video
references only as HTTPS URLs. Base64 `data:` URIs are refused outright —
"Only HTTPS URLs are allowed" — so the tier-1 video-to-video path (`runway/aleph-2`)
cannot work from local files, no matter how small they are. Image references do
accept `data:` URIs, which is why the frame-guided tiers need nothing from here.

**This publishes confidential AIM footage to the public internet.** For the life
of the tunnel the clip is fetchable by anyone holding the URL, without
authentication. Three things narrow that exposure, and none of them eliminate it:

* only the edit-window segment is served, never the full recording;
* the path carries 32 random hex characters, so the URL is not guessable;
* the tunnel is torn down as soon as the job stops needing it.

The provider still fetches and processes the footage, and may retain it under its
own terms. That is inherent to sending video to a hosted model and is documented
in the README rather than hidden here. `ALLOW_VIDEO_REFERENCE` is off by default
so this never runs unless someone turns it on deliberately.
"""

from __future__ import annotations

import contextlib
import functools
import http.server
import os
import re
import secrets
import shutil
import socket
import socketserver
import ssl
import subprocess
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

from .config import ROOT

# Checked before PATH so a vendored copy can be used without touching the system.
VENDORED = [ROOT / "build" / "bin" / "cloudflared", ROOT / ".venv" / "bin" / "cloudflared"]
_TUNNEL_RE = re.compile(rb"https://[a-z0-9-]+\.trycloudflare\.com")


class HostingError(RuntimeError):
    pass


def find_cloudflared() -> str | None:
    for candidate in VENDORED:
        if candidate.exists() and os.access(candidate, os.X_OK):
            return str(candidate)
    return shutil.which("cloudflared")


def install_hint() -> str:
    return (
        "cloudflared is required for video-to-video generation but was not found.\n"
        "  Homebrew:  brew install cloudflared\n"
        "  Direct:    curl -L -o build/bin/cloudflared \\\n"
        "               https://github.com/cloudflare/cloudflared/releases/latest/download/"
        "cloudflared-darwin-arm64 && chmod +x build/bin/cloudflared\n"
        "Or run with --no-video-reference to use frame-guided generation, which "
        "needs no public URL."
    )


class _QuietHandler(http.server.SimpleHTTPRequestHandler):
    def log_message(self, fmt, *args):  # noqa: A003 - silence per-request logging
        pass


@contextlib.contextmanager
def serve_file(path: Path, *, startup_timeout: float = 45.0):
    """Yield a public HTTPS URL for `path`, and tear it down on exit.

    The file is copied into a directory that contains nothing else, so a bug in
    the static handler cannot expose anything but the clip being generated from.
    """
    path = Path(path).resolve()
    if not path.exists():
        raise HostingError(f"cannot serve missing file: {path}")
    binary = find_cloudflared()
    if not binary:
        raise HostingError(install_hint())

    token = secrets.token_hex(16)
    staging = Path(ROOT / "build" / "tunnel" / token)
    staging.mkdir(parents=True, exist_ok=True)
    served = staging / f"{token}{path.suffix}"
    shutil.copyfile(path, served)

    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]

    handler = functools.partial(_QuietHandler, directory=str(staging))
    httpd = socketserver.TCPServer(("127.0.0.1", port), handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()

    process = subprocess.Popen(
        [binary, "tunnel", "--url", f"http://127.0.0.1:{port}", "--no-autoupdate"],
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT)

    # cloudflared logs continuously for the life of the tunnel. Reading only
    # until the URL appears and then stopping fills the pipe buffer, at which
    # point cloudflared blocks on write and the tunnel silently stops forwarding.
    # A drain thread consumes output for as long as the process runs.
    found_url: list[str] = []
    log_tail: list[str] = []

    def _drain():
        for raw in iter(process.stdout.readline, b""):
            match = _TUNNEL_RE.search(raw)
            if match and not found_url:
                found_url.append(match.group(0).decode())
            log_tail.append(raw.decode(errors="replace").rstrip())
            del log_tail[:-40]

    reader = threading.Thread(target=_drain, daemon=True)
    reader.start()

    public: str | None = None
    try:
        deadline = time.monotonic() + startup_timeout
        while time.monotonic() < deadline and not found_url:
            if process.poll() is not None:
                raise HostingError("cloudflared exited before publishing a URL:\n  "
                                   + "\n  ".join(log_tail[-8:]))
            time.sleep(0.2)
        public = found_url[0] if found_url else None
        if not public:
            raise HostingError(f"cloudflared published no URL within {startup_timeout}s:\n  "
                               + "\n  ".join(log_tail[-8:]))

        url = f"{public}/{served.name}"
        _wait_reachable(url, deadline=time.monotonic() + 60)
        yield url
    finally:
        process.terminate()
        with contextlib.suppress(Exception):
            process.wait(timeout=10)
        httpd.shutdown()
        httpd.server_close()
        shutil.rmtree(staging, ignore_errors=True)


def _resolve_via_public_dns(hostname: str) -> str | None:
    """Resolve through a public resolver when the system one cannot.

    Some networks — including the one this was developed on — run a local
    resolver that refuses to answer for `*.trycloudflare.com`, while the name is
    perfectly resolvable on the public internet. That matters because the party
    that actually has to fetch the URL is OpenRouter's provider, not this
    machine, so a local DNS failure is not evidence that the tunnel is broken.
    Falling back here keeps the pre-flight meaningful instead of failing a job
    that would have worked.
    """
    dig = shutil.which("dig")
    if not dig:
        return None
    for resolver in ("@1.1.1.1", "@8.8.8.8"):
        try:
            out = subprocess.run([dig, "+short", "+time=3", resolver, hostname, "A"],
                                 capture_output=True, text=True, timeout=15).stdout
        except (subprocess.SubprocessError, OSError):
            continue
        for line in out.splitlines():
            line = line.strip()
            if re.fullmatch(r"\d+\.\d+\.\d+\.\d+", line):
                return line
    return None


def _probe_with_sni(hostname: str, address: str, path: str, *, timeout: float = 15.0) -> int:
    """Range-GET `path` from `address`, presenting `hostname` for SNI and Host.

    Needed because the connection has to go to an IP this machine's resolver
    could not supply, while Cloudflare's edge still requires the real hostname
    in both TLS SNI and the HTTP Host header to route the request.
    """
    context = ssl.create_default_context()
    with socket.create_connection((address, 443), timeout=timeout) as raw:
        with context.wrap_socket(raw, server_hostname=hostname) as tls:
            request = (f"GET {path} HTTP/1.1\r\nHost: {hostname}\r\n"
                       "Range: bytes=0-31\r\nConnection: close\r\n"
                       "User-Agent: alcor-erroneous-video-pipeline\r\n\r\n")
            tls.sendall(request.encode())
            head = b""
            while b"\r\n" not in head and len(head) < 4096:
                chunk = tls.recv(1024)
                if not chunk:
                    break
                head += chunk
    match = re.match(rb"HTTP/1\.[01] (\d{3})", head)
    return int(match.group(1)) if match else 0


def _wait_reachable(url: str, *, deadline: float) -> None:
    """Block until the tunnel actually serves the file.

    A tunnel prints its hostname before edge routing is ready, and submitting in
    that gap makes the provider fetch a 404 — which fails the job and still costs
    the submission. Confirming with a HEAD first is much cheaper than retrying.
    """
    last = "no attempt made"
    while time.monotonic() < deadline:
        try:
            # A ranged GET rather than HEAD: the edge and the provider both use
            # GET, and confirming with a different method can succeed while the
            # real fetch still fails. Range keeps it to a few bytes.
            request = urllib.request.Request(url, headers={"Range": "bytes=0-31"})
            with urllib.request.urlopen(request, timeout=15) as response:
                if response.status in (200, 206) and response.read(32):
                    return
                last = f"HTTP {response.status}"
        except urllib.error.HTTPError as exc:
            last = f"HTTP {exc.code}"
        except urllib.error.URLError as exc:
            last = f"URLError: {exc.reason}"
            if isinstance(getattr(exc, "reason", None), socket.gaierror):
                parts = urllib.parse.urlsplit(url)
                address = _resolve_public_cached(parts.hostname or "")
                if address:
                    try:
                        status = _probe_with_sni(parts.hostname, address, parts.path)
                        if status in (200, 206):
                            return
                        last = f"HTTP {status} via public DNS ({address})"
                    except Exception as inner:  # noqa: BLE001
                        last = f"public-DNS probe failed: {type(inner).__name__}: {inner}"
        except Exception as exc:  # noqa: BLE001 - edge is not up yet
            last = f"{type(exc).__name__}: {exc}"
        time.sleep(2.0)
    raise HostingError(f"tunnel URL never became reachable ({last})")


_RESOLVED: dict[str, str] = {}


def _resolve_public_cached(hostname: str) -> str | None:
    """Cache successes only.

    A freshly created quick tunnel takes a few seconds to appear in public DNS,
    so the first lookup often returns nothing. Memoising that miss — as an
    lru_cache would — pins the negative answer for the rest of the run and the
    retry loop can never recover.
    """
    if not hostname:
        return None
    if hostname not in _RESOLVED:
        address = _resolve_via_public_dns(hostname)
        if not address:
            return None
        _RESOLVED[hostname] = address
    return _RESOLVED[hostname]
