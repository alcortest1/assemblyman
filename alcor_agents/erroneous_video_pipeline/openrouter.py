"""OpenRouter transport: chat completions, and the asynchronous video job API.

The video API's shape here was established by probing the live service rather
than from documentation, and the details that matter are not guessable:

* `POST /videos` returns **202** with `{id, polling_url, status}`. There is **no
  cancel endpoint** — `DELETE /videos/{id}` and `POST /videos/{id}/cancel` both
  404 — so a submitted job always runs to completion and always bills. That is
  why `config.Budget` charges before submission and why `--confirm` exists.
* Assets go in `input_references`, each `{"type": "video_url", "video_url":
  {"url": ...}}` or `{"type": "image_url", "image_url": {"url": ...}}`. The
  nested object is required; a bare string is rejected by the schema.
* **Video references must be HTTPS.** `data:` URIs are refused outright with
  "Only HTTPS URLs are allowed", which is the whole reason `hosting.py` exists.
  Image references do accept `data:` URIs, so the frame-guided tiers need no host.
* Finished content is at `GET /videos/{id}/content?index=N`, authenticated;
  `unsigned_urls` on the job are the same paths and are not public links.

Transport is `urllib` to match `inspector/vlm.py` — this venv has neither
`httpx` nor `requests`, and adding a dependency the machine cannot install would
make the pipeline unrunnable here.
"""

from __future__ import annotations

import json
import random
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path

from .config import Settings, redact

# Terminal states, from the live API plus the provider-neutral spellings the
# docs use. Anything unrecognised is treated as still running until the poll
# deadline, which fails safe: a job we cannot classify is not reported as good.
DONE_OK = {"completed", "succeeded", "success"}
DONE_BAD = {"failed", "cancelled", "canceled", "expired", "error"}
PENDING = {"pending", "queued", "in_progress", "processing", "running", "generating"}

RETRY_STATUS = {408, 409, 425, 429, 500, 502, 503, 504}
HEADERS = {"HTTP-Referer": "http://127.0.0.1:8765",
           "X-Title": "Alcor Erroneous Video Pipeline"}


class OpenRouterError(RuntimeError):
    def __init__(self, message: str, status: int | None = None, retryable: bool = False):
        super().__init__(redact(message))
        self.status = status
        self.retryable = retryable


@dataclass
class VideoJob:
    id: str
    status: str
    raw: dict

    @property
    def done(self) -> bool:
        return self.status in DONE_OK or self.status in DONE_BAD

    @property
    def ok(self) -> bool:
        return self.status in DONE_OK

    @property
    def cost(self) -> float | None:
        usage = self.raw.get("usage") or {}
        value = usage.get("cost")
        return float(value) if value is not None else None

    @property
    def content_indices(self) -> list[int]:
        return list(range(len(self.raw.get("unsigned_urls") or []) or 1))


class Client:
    def __init__(self, api_key: str, settings: Settings | None = None):
        if not api_key:
            raise OpenRouterError("no OPENROUTER_API_KEY in the environment or .env")
        self._key = api_key
        self.settings = settings or Settings.from_env()
        self.base = self.settings.base_url.rstrip("/")

    # ------------------------------------------------------------- transport

    def _request(self, method: str, path: str, payload: dict | None = None,
                 *, timeout: int = 180, raw_bytes: bool = False):
        url = path if path.startswith("http") else f"{self.base}{path}"
        headers = dict(HEADERS)
        headers["Authorization"] = f"Bearer {self._key}"
        data = None
        if payload is not None:
            data = json.dumps(payload).encode()
            headers["Content-Type"] = "application/json"
        request = urllib.request.Request(url, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                body = response.read()
                return body if raw_bytes else json.loads(body or b"{}")
        except urllib.error.HTTPError as exc:
            body = exc.read().decode(errors="replace")[:2000]
            raise OpenRouterError(f"HTTP {exc.code} on {method} {path}: {body}",
                                  status=exc.code, retryable=exc.code in RETRY_STATUS)
        except urllib.error.URLError as exc:
            raise OpenRouterError(f"network error on {method} {path}: {exc.reason}",
                                  retryable=True)

    def _retrying(self, method: str, path: str, payload: dict | None = None, **kw):
        """Exponential backoff with jitter, on transport and 5xx/429 only.

        A 400 is a bad request and repeating it just burns time; only the
        statuses in RETRY_STATUS come back here.
        """
        attempts = max(1, self.settings.max_retries)
        last: Exception | None = None
        for attempt in range(attempts):
            try:
                return self._request(method, path, payload, **kw)
            except OpenRouterError as exc:
                last = exc
                if not exc.retryable or attempt == attempts - 1:
                    raise
                delay = min(60.0, 2.0 ** attempt) + random.uniform(0, 0.75)
                time.sleep(delay)
        raise last  # pragma: no cover - loop always returns or raises

    # --------------------------------------------------------- capabilities

    def video_models(self) -> list[dict]:
        """Live capability list from GET /videos/models."""
        body = self._retrying("GET", "/videos/models", timeout=60)
        return body.get("data", body if isinstance(body, list) else [])

    def credits(self) -> dict:
        return (self._retrying("GET", "/key", timeout=30) or {}).get("data", {})

    # ------------------------------------------------------------- stage one

    def chat(self, model: str, messages: list[dict], *, max_tokens: int = 4000,
             temperature: float = 0.2, response_json: bool = True) -> dict:
        payload: dict = {"model": model, "messages": messages,
                         "max_tokens": max_tokens, "temperature": temperature}
        if response_json:
            payload["response_format"] = {"type": "json_object"}
        return self._retrying("POST", "/chat/completions", payload, timeout=600)

    # ------------------------------------------------------------- stage two

    def submit_video(self, payload: dict) -> VideoJob:
        body = self._retrying("POST", "/videos", payload, timeout=300)
        job_id = body.get("id")
        if not job_id:
            raise OpenRouterError(f"submission returned no job id: {body}")
        return VideoJob(id=job_id, status=body.get("status", "pending"), raw=body)

    def get_video(self, job_id: str) -> VideoJob:
        body = self._retrying("GET", f"/videos/{job_id}", timeout=60)
        return VideoJob(id=body.get("id", job_id),
                        status=(body.get("status") or "unknown").lower(), raw=body)

    def poll_video(self, job_id: str, *, timeout_s: int = 1800, interval_s: float = 10.0,
                   on_tick=None) -> VideoJob:
        """Poll to a terminal state or raise on deadline.

        A timeout here does not mean the job stopped — there is no cancel — so
        the caller records the id and `resume` can pick it up later.
        """
        deadline = time.monotonic() + timeout_s
        job = self.get_video(job_id)
        while not job.done:
            if time.monotonic() > deadline:
                raise OpenRouterError(
                    f"job {job_id} still {job.status} after {timeout_s}s; "
                    f"it keeps running — re-attach with `resume`")
            if on_tick:
                on_tick(job)
            time.sleep(interval_s)
            job = self.get_video(job_id)
        return job

    def download_video(self, job_id: str, dest: Path, index: int = 0) -> Path:
        """Fetch finished content, written atomically so a partial file is never used."""
        data = self._request("GET", f"/videos/{job_id}/content?index={index}",
                             timeout=1800, raw_bytes=True)
        if not data or len(data) < 1024:
            raise OpenRouterError(f"job {job_id} content was empty ({len(data or b'')} bytes)")
        dest.parent.mkdir(parents=True, exist_ok=True)
        temp = dest.with_suffix(dest.suffix + ".part")
        temp.write_bytes(data)
        temp.replace(dest)
        return dest
