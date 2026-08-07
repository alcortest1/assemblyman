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

Credentials come from the environment, same four variables the LiveKit agent uses:

    GOOGLE_APPLICATION_CREDENTIALS  service account key file
    GOOGLE_CLOUD_PROJECT            project id
    GOOGLE_CLOUD_LOCATION           defaults to `global`
"""

from __future__ import annotations

import base64
import os
import re
import threading

from google import genai
from google.genai import types

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


def available() -> bool:
    return bool(os.getenv("GOOGLE_CLOUD_PROJECT")
                and os.getenv("GOOGLE_APPLICATION_CREDENTIALS"))


def supports(model_id: str) -> bool:
    return model_id in SUPPORTED


def client() -> genai.Client:
    """One client for the process — see the note in agent/grading.py.

    `Client.models` holds no strong reference back, so a client built inline can
    be collected and its pool closed before the request goes out.
    """
    global _CLIENT
    if _CLIENT is None:
        with _CLIENT_LOCK:
            if _CLIENT is None:  # re-checked under the lock
                _CLIENT = genai.Client(
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
