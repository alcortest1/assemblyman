"""Speaks Vertex AI in OpenRouter's shape, so vlm.py needs no changes.

The OpenRouter account is spent — `/credits` reports usage past the balance and
every call comes back 402 — but the Gemini arms of a run do not have to go
through it. The pilot's Google Cloud project serves `gemini-3.1-pro-preview` and
`gemini-3.6-flash` directly, on a service account.

`vlm._complete` already takes a `post=` callable, so the whole adaptation is a
function with the same signature as `vlm._post` that returns the same envelope.
Nothing in the grading path knows the difference, which is what keeps a Vertex
run and an OpenRouter run comparable: same prompt, same parsing, same schema.

Only the Gemini arms can move. Opus 5 and GPT-5.6 Sol have no Vertex counterpart
and stay ungraded — reported as ungraded, never as an abstention.

Credentials come from the environment, same four variables the LiveKit agent uses,
falling back to the gitignored `.env` beside this package:

    GOOGLE_APPLICATION_CREDENTIALS  service account key file
    GOOGLE_CLOUD_PROJECT            project id
    GOOGLE_CLOUD_LOCATION           defaults to `global`
"""

from __future__ import annotations

import base64
import os
import re
import threading
from pathlib import Path

from google import genai
from google.genai import types

ROOT = Path(__file__).resolve().parent.parent

# The variables that decide whether Vertex is reachable, and how it authenticates.
# GEMINI_API_KEY is the second door to the same models: the Gemini Developer API
# takes an AI Studio key and no service account, which matters on the day the
# service account stops existing while the work does not.
ENV_KEYS = (
    "GOOGLE_APPLICATION_CREDENTIALS",
    "GOOGLE_CLOUD_PROJECT",
    "GOOGLE_CLOUD_LOCATION",
    "GOOGLE_GENAI_USE_VERTEXAI",
    "GEMINI_API_KEY",
    "GOOGLE_API_KEY",
)

# OpenRouter prefixes vendor to model; Vertex does not.
VENDOR_PREFIX = re.compile(r"^(google|anthropic|openai)/")

# What Vertex can serve of the pilot's four arms.
SUPPORTED = {
    "google/gemini-3.1-pro-preview": "gemini-3.1-pro-preview",
    "google/gemini-3.6-flash": "gemini-3.6-flash",
    "google/gemini-3.5-flash": "gemini-3.5-flash",
    "google/gemini-2.5-pro": "gemini-2.5-pro",
}

_CLIENT: genai.Client | None = None
# The runner grades with a thread pool, and `client()` is a check-then-set. Two
# workers arriving together both build a client; the loser is dropped, collected,
# and closes the connection pool the winner is still using — which surfaces as
# "Cannot send a request, as the client has been closed" on a call that never
# left the process. It cost 3 of 80 calls on the first run, silently, because a
# failed call is just an ungraded point.
_CLIENT_LOCK = threading.Lock()


def load_env() -> None:
    """Put the Vertex variables in the environment, from `.env` if not already there.

    `vlm.load_api_key()` reads the OpenRouter key out of the gitignored `.env`,
    so a shell that exported nothing can still reach OpenRouter. The Google
    variables had no such fallback, and the effect was not a clear failure: in
    exactly the setup the README describes — key file on disk, its path recorded
    in `.env`, nothing exported — `available()` came back false, every arm of a
    run routed to an OpenRouter account that is spent, and the run wrote a
    full-sized file of 402s containing no verdicts at all.

    Environment wins. `setdefault` never overrides a value a caller deliberately
    exported, so pointing a run at a different project stays a matter of
    exporting one.

    These have to become real environment variables rather than values we hold:
    `google.auth` reads GOOGLE_APPLICATION_CREDENTIALS out of the environment
    itself when it resolves default credentials.
    """
    env_file = ROOT / ".env"
    if not env_file.exists():
        return
    for line in env_file.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        name, value = line.split("=", 1)
        if name.strip() in ENV_KEYS:
            os.environ.setdefault(name.strip(), value.strip().strip('"').strip("'"))


def api_key() -> str | None:
    """The Gemini Developer API key, if one is configured."""
    load_env()
    return os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY") or None


def available() -> bool:
    """Whether the Google route can be reached — asked before every route decision.

    Loads `.env` first: this predicate is the gate the whole transport passes
    through, so it is the one place that can answer for a shell that exported
    nothing without every caller having to remember to. An API key or a service
    account both count — they are two doors to the same models.
    """
    load_env()
    return bool(api_key()
                or (os.getenv("GOOGLE_CLOUD_PROJECT")
                    and os.getenv("GOOGLE_APPLICATION_CREDENTIALS")))


def supports(model_id: str) -> bool:
    return model_id in SUPPORTED


def reachable() -> tuple[bool, str]:
    """Whether the service account can actually mint a token, and why not.

    `available()` asks whether credentials are configured; this asks whether they
    still work, which is a different question and the one a pre-flight needs. A
    key file outlives the account it was cut for: delete the service account and
    the JSON on disk is unchanged — same project, same client_email, same private
    key — while every token request comes back `invalid_grant: account not found`.

    Left unchecked that surfaces sixty identical call failures with the run
    already underway, which is what this pre-flight exists to prevent. The token
    is minted once here rather than per call; google-auth caches it, so the run
    that follows reuses this one.
    """
    if not available():
        return False, ("GEMINI_API_KEY / GOOGLE_API_KEY not set, and neither are "
                       "GOOGLE_CLOUD_PROJECT / GOOGLE_APPLICATION_CREDENTIALS")
    # The API key is the cheaper door and needs no token dance; one models.get
    # answers whether it is live. Preferred when both are configured, matching
    # the route client() will actually take.
    if api_key():
        try:
            client().models.get(model=next(iter(SUPPORTED.values())))
        except Exception as exc:  # noqa: BLE001 — the reason is for a human, not a branch
            return False, f"GEMINI_API_KEY did not answer: {str(exc)[:200]}"
        return True, ""
    path = os.environ["GOOGLE_APPLICATION_CREDENTIALS"]
    if not Path(path).exists():
        return False, f"key file not found: {path}"
    try:
        import google.auth.transport.requests as transport
        from google.oauth2 import service_account

        creds = service_account.Credentials.from_service_account_file(
            path, scopes=["https://www.googleapis.com/auth/cloud-platform"])
        creds.refresh(transport.Request())
    except Exception as exc:  # noqa: BLE001 — the reason is for a human, not a branch
        return False, str(exc)
    return True, ""


def client() -> genai.Client:
    """One client for the process — see the note in agent/grading.py.

    `Client.models` holds no strong reference back, so a client built inline can
    be collected and its pool closed before the request goes out.
    """
    global _CLIENT
    if _CLIENT is None:
        with _CLIENT_LOCK:
            if _CLIENT is None:  # re-checked under the lock
                key = api_key()
                # Same client class, same models, two doors: the Developer API
                # on a key when one is configured, Vertex on the service
                # account otherwise. The choice matches reachable()'s.
                _CLIENT = genai.Client(api_key=key) if key else genai.Client(
                    vertexai=True,
                    project=os.environ["GOOGLE_CLOUD_PROJECT"],
                    location=os.getenv("GOOGLE_CLOUD_LOCATION", "global"),
                )
    return _CLIENT


def _parts(content) -> list[types.Part]:
    """Turn OpenRouter's content list into Vertex parts, in the order given.

    Order is load-bearing here: the sequence prompt tells the model the frames
    are chronological and names their timestamps, so a reordering would make
    every cited moment wrong.
    """
    if isinstance(content, str):
        return [types.Part.from_text(text=content)]

    parts: list[types.Part] = []
    for item in content or []:
        kind = item.get("type")
        if kind == "text":
            parts.append(types.Part.from_text(text=item.get("text", "")))
        elif kind == "image_url":
            url = (item.get("image_url") or {}).get("url", "")
            match = re.match(r"data:([^;]+);base64,(.*)$", url, re.S)
            if not match:
                continue  # only data URIs are ever built by vlm.encode_image
            parts.append(types.Part.from_bytes(
                data=base64.b64decode(match.group(2)), mime_type=match.group(1)
            ))
    return parts


def post(payload: dict, key: str | None = None) -> dict:
    """Drop-in for `vlm._post`, returning the same envelope.

    `key` is accepted and ignored: Vertex authenticates with the service account,
    not the OpenRouter key, and the caller passes one positionally.
    """
    model = payload.get("model", "")
    target = SUPPORTED.get(model) or VENDOR_PREFIX.sub("", model)

    system, user_content = None, None
    for message in payload.get("messages", []):
        if message.get("role") == "system":
            system = message.get("content")
        elif message.get("role") == "user":
            user_content = message.get("content")

    response = client().models.generate_content(
        model=target,
        contents=[types.Content(role="user", parts=_parts(user_content))],
        config=types.GenerateContentConfig(
            system_instruction=system,
            temperature=payload.get("temperature", 0),
            max_output_tokens=payload.get("max_tokens", 4000),
            response_mime_type="application/json",
        ),
    )

    usage = getattr(response, "usage_metadata", None)
    prompt_tokens = getattr(usage, "prompt_token_count", 0) or 0
    completion_tokens = getattr(usage, "candidates_token_count", 0) or 0

    # `response.text` is None when the model returned no text part at all — a
    # safety block, or a reply that spent its whole budget thinking. Returning
    # an empty string would be parsed as an unparseable reply and silently
    # scored; an explicit error is what the caller can act on.
    text = response.text
    if text is None:
        reason = getattr(response, "prompt_feedback", None)
        return {"error": {"message": f"no text in reply ({reason or 'no candidates'})"}}

    return {
        "choices": [{"message": {"content": text}}],
        "usage": {
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            # Left to the caller's price table. Vertex bills the project rather
            # than reporting a per-call figure the way OpenRouter does.
            "cost": None,
        },
    }
