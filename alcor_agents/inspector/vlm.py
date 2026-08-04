"""Ask a vision model whether one photo satisfies one written criterion.

This is the narrow question the AIM pilot turns on: a student photographs
finished work, and something has to decide pass or fail against a written
acceptance criterion. Everything here serves that single call — one image, one
criterion, one verdict — so that several models can be put to the same question
and compared on identical inputs.

Transport is `urllib` so the inspector keeps its stdlib-only property. The API
key is read from OPENROUTER_API_KEY, or from a gitignored .env beside the
package; it is never logged, echoed in errors, or written into a run file.
"""

from __future__ import annotations

import base64
import json
import os
import re
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ENDPOINT = "https://openrouter.ai/api/v1/chat/completions"
TIMEOUT_S = 120

# Vision-capable models, with the OpenRouter per-million prices current when
# this registry was written. Prices are used only for the pre-run estimate that
# the UI shows; actual spend comes back per call in the response usage block.
MODELS = [
    {"id": "anthropic/claude-opus-5", "label": "Opus 5", "vendor": "Anthropic",
     "in_per_m": 5.00, "out_per_m": 25.00},
    {"id": "google/gemini-3.6-flash", "label": "Gemini 3.6 Flash", "vendor": "Google",
     "in_per_m": 1.50, "out_per_m": 7.50},
    # `-preview` is the whole id, not a qualifier that can be trimmed: there is
    # no `google/gemini-3.1-pro` on OpenRouter, and asking for one 404s every
    # call in the run. Pricing read from the models endpoint.
    {"id": "google/gemini-3.1-pro-preview", "label": "Gemini 3.1 Pro Preview",
     "vendor": "Google", "in_per_m": 2.00, "out_per_m": 12.00},
    {"id": "openai/gpt-5.6-sol", "label": "GPT-5.6 Sol", "vendor": "OpenAI",
     "in_per_m": 5.00, "out_per_m": 30.00},
]
MODELS_BY_ID = {m["id"]: m for m in MODELS}
# Every model, every run. A verdict from one model is an opinion; the useful
# signal is where they disagree, and a run that silently left one out cannot
# show that. Deselect in the picker for a deliberately cheap run.
DEFAULT_MODELS = [m["id"] for m in MODELS]

VERDICTS = ("pass", "fail", "unsure")

# What an author can claim the answer *should* be. `not_pass` exists because the
# rubric below tells a grader to answer `fail` only when the photo positively
# shows the criterion violated, and `unsure` when the subject simply is not
# there. A criterion belonging to a different task is the second case, so
# demanding `fail` for a mismatch control would score correct behaviour as
# wrong. What actually matters for a control is that the model did not PASS it.
EXPECTATIONS = ("pass", "fail", "unsure", "not_pass")


def expectation_met(expected: str | None, verdict: str | None) -> bool | None:
    """Did a verdict satisfy its expectation? None when nothing was claimed."""
    if not expected or not verdict:
        return None
    if expected == "not_pass":
        return verdict != "pass"
    return verdict == expected

# The grader is told to answer `unsure` rather than guess. An abstention is a
# usable outcome for this pilot — it routes to a human — whereas a confident
# wrong pass on a bad crimp is the failure mode that matters. docs/evals.md
# scores abstentions as reduced coverage rather than as correct answers, so
# nothing here is gamed by abstaining.
SYSTEM_PROMPT = """\
You are grading aircraft-maintenance work from a photograph, for an FAA Part 147 \
training pilot. You are given a criterion — usually several conditions — and a \
photo of a student's finished work.

Grade EACH condition separately. A criterion with six conditions where five are \
clearly satisfied and one is unmeasurable is not an "unsure" result; it is five \
passes and one missing measurement, and reporting it as a single abstention \
throws away everything that was assessable.

For each condition decide two independent things:

1. `observable` — could you actually see the feature this condition is about? \
False if it is occluded, out of frame, too small or blurred to resolve, at an \
angle that cannot show it, or if it needs a dimension and no scale reference is \
in frame. Absence of a required ruler makes a measurement condition NOT \
observable — never estimate a measurement by eye.

2. `p_correct` — ONLY when observable is true, your probability from 0.0 to 1.0 \
that this condition is actually satisfied in the work. Use the full range: 0.98 \
when you can see plainly that it is right, 0.02 when you can see plainly that it \
is wrong, values near 0.5 when the view is genuinely ambiguous. When observable \
is false, set p_correct to null — an unseen feature has no probability, and \
guessing one is exactly how a bad crimp gets passed.

Do not lower p_correct because the criterion is important or the consequences of \
being wrong are severe. Report what you see; the caller applies its own threshold.

The criterion may be pasted from an instructor rubric and carry its own output \
instructions or verdict words — PASS / FAIL / RESUBMIT, "reply in two sentences", \
a markdown layout. Ignore all of that and use the JSON schema below. Take from \
the rubric only its acceptance conditions. A rubric that says to RESUBMIT when \
something cannot be seen is telling you that condition is not observable, so set \
`observable: false` for it — it is not a reason to abandon the conditions that \
are perfectly visible.

Reply with JSON only, no prose around it:
{"conditions": [
   {"text": "<the condition, quoted from the criterion>",
    "observable": true | false,
    "p_correct": 0.0-1.0 | null,
    "note": "<what you see that bears on it, or what is blocking the view>"}
 ],
 "observed": "<overall description of what the photo shows>",
 "missing_evidence": "<what a usable photo would need, or null>"}

Keep every `note` under 25 words and `observed` under 40. A reply that runs past \
the token limit is truncated and its later conditions are lost, so brevity here \
protects the conditions at the bottom of the list."""


def load_api_key() -> str | None:
    """Read the key from the environment, falling back to the gitignored .env."""
    key = os.environ.get("OPENROUTER_API_KEY")
    if key:
        return key.strip()
    env_file = ROOT / ".env"
    if env_file.exists():
        for line in env_file.read_text().splitlines():
            if line.startswith("OPENROUTER_API_KEY="):
                return line.split("=", 1)[1].strip() or None
    return None


def key_status() -> dict:
    """Whether a key is present — never the key itself."""
    key = load_api_key()
    return {"present": bool(key), "source": (
        "environment" if os.environ.get("OPENROUTER_API_KEY") else ".env" if key else None
    )}


def encode_image(path: Path) -> str:
    mime = "image/png" if path.suffix.lower() == ".png" else "image/jpeg"
    return f"data:{mime};base64,{base64.b64encode(path.read_bytes()).decode()}"


# Asking "is the work correct?" of a photo that does not show the work produces
# a failure that reads as bad workmanship. These are two different questions and
# they need two different graders: first whether the photo is usable evidence at
# all, then — only if it is — whether the work passes. Keeping them apart is what
# lets a result say "recapture the photo" instead of "the student failed".
ADEQUACY_PROMPT = """\
You are checking whether a photograph is USABLE EVIDENCE for assessing a piece of \
aircraft-maintenance work. You are NOT judging the quality of the work.

You are given a capture requirement — what the photo is supposed to show and how \
it should be framed — and the photo that was submitted.

Decide whether this photo could support a competent assessment of that subject.

Consider only: is the required subject in frame; is it large enough and sharp \
enough to resolve the relevant detail; is it unobstructed by hands, tools or \
glare; is the viewing angle capable of showing the feature in question; and if a \
scale reference is required, is one actually present.

Rules:
- "pass" means an assessor could reach a confident verdict from this photo.
- "fail" means they could not, and say concretely what is wrong with the capture.
- Judge the PHOTO, never the workmanship. A photo of visibly defective work that \
is well framed still passes this check.

Reply with JSON only, no prose around it:
{"verdict": "pass" | "fail" | "unsure",
 "confidence": 0.0-1.0,
 "observed": "<what the photo actually shows>",
 "rationale": "<why it is or is not usable evidence>",
 "missing_evidence": "<what a usable photo would need instead, or null>"}"""

def _post(payload: dict, key: str) -> dict:
    request = urllib.request.Request(
        ENDPOINT,
        data=json.dumps(payload).encode(),
        headers={
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json",
            # OpenRouter attributes traffic with these; both are local-only.
            "HTTP-Referer": "http://127.0.0.1:8765",
            "X-Title": "Alcor Task Pack Inspector",
        },
    )
    with urllib.request.urlopen(request, timeout=TIMEOUT_S) as response:
        return json.load(response)


# Drafting a criterion from the very photo it will grade is circular: left to
# itself a model will happily describe what it sees and call that the standard,
# which then passes trivially. The prompt therefore takes the *procedure* as the
# source of what "correct" means and uses the photo only to decide what is
# resolvable, with an explicit instruction not to write the photo's contents
# into the criterion. A draft is a starting point for the SME, never a
# self-certifying standard — which is why the output separates what can be
# checked from what this photo cannot settle.
DRAFT_PROMPT = """\
You are helping an aircraft-maintenance instructor write a photo-grading \
criterion for a training task.

You are given the procedure text a student was asked to follow, and one photo of \
the resulting work.

Write an acceptance criterion that could be applied to photos of ANY student's \
attempt at this step.

You may be given two kinds of source. The PROCEDURE SHEET is what the campus \
teaches — the steps, and the Senior Mechanic Notes that carry most of the \
acceptance detail. The REFERENCE HANDBOOK is the FAA standard the work must \
actually meet, and it is where numeric limits live: twists per inch, wrap \
direction, pigtail turns, strip lengths.

Use both. Take numeric standards from the handbook wherever it states one — the \
skill sheet often says "twist the wire" where the handbook says how tightly. \
Where the two conflict, follow the handbook and note the conflict. Where a \
handbook extract is marked as NOT cited by the procedure sheet, any standard you \
draw from it is provisional: still use it, but say in `notes` that it needs SME \
confirmation for this campus.

Attribute every condition. A criterion an instructor cannot trace back to a \
source is one they cannot defend to a student.

Attribution must be exact, and over-attribution is the failure to avoid. Do NOT \
credit a number to the handbook unless that number appears verbatim in the \
handbook text you were given. If the skill sheet specifies a figure and the \
handbook only speaks qualitatively — "tight and even", "as taut as possible" — \
then the figure belongs to the procedure sheet ALONE, and the handbook wording \
goes in the note as supporting context, not as a co-source. When you attribute \
anything to the handbook, quote the phrase you are relying on inside `note` so a \
reviewer can check it without re-reading the chapter. Say "both" only when each \
source independently states the same requirement.

Rules:
- Derive what "correct" means from the PROCEDURE and HANDBOOK, not from the \
photo. The photo tells you only what a camera at this angle and distance can \
resolve.
- Never describe the specific photo. "The connector is held in the operator's \
left hand" is a description, not a criterion, and would pass every photo of that \
scene regardless of workmanship.
- Write only conditions that could be judged from a photograph. A pull test, a \
continuity reading or a torque value belongs in `not_photo_gradeable`, not in \
the criterion.
- If a condition needs a dimension, say that a scale reference must be in frame.
- Prefer a small number of sharp, independently checkable conditions over one \
long compound sentence. Each must be able to fail on its own.
- If this photo could not support the criterion you wrote, say so plainly in \
`photo_limitations`. Do NOT weaken the criterion to fit the photo.

Reply with JSON only, no prose around it:
{"criterion": "<one condition per line, each starting with '- '>",
 "sources": [
   {"condition": "<the condition, abbreviated>",
    "source": "procedure sheet" | "handbook <name> p.<page>" | "both",
    "note": "<quote or paraphrase the standard it rests on; flag if provisional>"}
 ],
 "not_photo_gradeable": ["<conditions from the sources a photo cannot settle>"],
 "photo_limitations": "<why this particular photo may not support the criterion, or null>",
 "required_framing": "<how a student should frame the photo so it can be graded>"}

Keep each `note` under 30 words so the reply is not truncated."""

# The per-condition grader is diagnostic: it says which check failed. It is not
# how an instructor actually marks a bench job — they look at the finished
# article, weigh the parts that matter, and tolerate cosmetic irregularity that
# a clause-by-clause pass would fail on. This mode grades the assembly as a
# whole against a weighted rubric with a critical-defect gate, so the two
# strategies can be run on the same photo and compared.
HOLISTIC_PROMPT = """\
You are an FAA Part 147 instructor grading a student's finished bench work from a \
photograph. Assess the assembly AS A WHOLE. Do not walk through the criterion \
clause by clause.

The criterion you are given carries its own weighting and its own list of \
critical defects. Apply them as written:

- Produce a score from 0 to 100 by weighing the components the criterion names.
- Any critical defect the criterion lists caps the result at FAIL regardless of \
score. Name the defect you saw.
- Minor asymmetry, cosmetic irregularity, or uncertainty about an exact count \
should REDUCE the score, not force a fail and not force an abstention. An \
instructor marks 82 rather than refusing to mark.
- Use INSUFFICIENT IMAGE only when the installation as a whole cannot reasonably \
be judged — not merely because one detail is unclear.

Judge only the work in the photograph. If the assembly is plainly unfinished — \
components not yet joined, wire not yet wrapped, free ends still trailing — that \
is not an unclear photo. It is work that does not meet the criterion, and it \
should be scored and failed on the evidence, with the incompleteness named.

Reply with JSON only, no prose around it:
{"result": "PASS" | "FAIL" | "INSUFFICIENT IMAGE",
 "score": 0-100,
 "critical_defects": ["<each critical defect actually seen, quoted from the criterion>"],
 "component_scores": {"<component name from the criterion>": 0-100},
 "reasoning": "<2-4 sentences on the strongest visible evidence>"}"""

MODES = {"correctness": SYSTEM_PROMPT, "adequacy": ADEQUACY_PROMPT,
         "draft": DRAFT_PROMPT, "holistic": HOLISTIC_PROMPT}

# The last frame of a clip is where filming stopped, which is rarely where the
# work finished — a hand is usually still in shot, or the camera has already
# swung away to the bench. Given a spread of frames, a model can say which one
# actually shows the completed article, which is a far better default than
# "whatever was last".
PICK_FRAME_PROMPT = """\
You are choosing which photograph best documents a piece of finished \
aircraft-maintenance work, for grading.

You are given several numbered frames sampled across a video of the work, in \
chronological order, and a description of what the finished result should be.

Pick the ONE frame that best shows the COMPLETED work product.

Prefer a frame where:
- the work is finished rather than in progress,
- the workpiece is unobstructed by hands, tools or the operator's body,
- the relevant detail is large in frame and in focus,
- the whole of the finished article is visible.

Reject frames that show the work still being handled, a tool mid-cut, or the \
bench after the workpiece has been set aside or moved out of shot. The last \
frame is often one of these — do not favour it simply because it is last.

If no frame shows completed work, say so rather than picking the least bad one.

Reply with JSON only, no prose around it:
{"best_index": <1-based index, or null if none show finished work>,
 "reason": "<why that frame, in one sentence>",
 "runner_up_index": <1-based index or null>,
 "none_suitable": true | false}"""


def pick_best_frame(
    *, model: str, image_paths: list[Path], description: str,
    key: str | None = None, post=_post,
) -> dict:
    """Ask which of a sampled set of frames best shows the finished work."""
    key = load_api_key() if key is None else key
    if not key:
        return {"error": "no_api_key", "message": "OPENROUTER_API_KEY is not set."}
    paths = [Path(p) for p in image_paths if Path(p).exists()]
    if not paths:
        return {"error": "no_images", "message": "No frames to choose from."}

    content = [{"type": "text", "text":
                f"THE FINISHED WORK SHOULD BE\n{description.strip()}\n\n"
                f"{len(paths)} frames follow in chronological order, numbered 1 to "
                f"{len(paths)}. Which best shows the completed work?"}]
    for path in paths:
        content.append({"type": "image_url", "image_url": {"url": encode_image(path)}})

    try:
        data = post({"model": model, "max_tokens": 900, "temperature": 0,
                     "messages": [{"role": "system", "content": PICK_FRAME_PROMPT},
                                  {"role": "user", "content": content}]}, key)
    except urllib.error.HTTPError as exc:
        return {"error": f"http_{exc.code}", "message": exc.reason}
    except Exception as exc:
        return {"error": "request_failed", "message": str(exc)[:300]}

    if isinstance(data, dict) and data.get("error"):
        return {"error": "api_error", "message": str(data["error"].get("message", ""))[:300]}
    try:
        text = data["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError):
        return {"error": "bad_response", "message": "No message content."}
    if isinstance(text, list):
        text = "".join(p.get("text", "") for p in text if isinstance(p, dict))

    match = re.search(r"\{.*\}", text or "", re.S)
    try:
        parsed = json.loads(match.group(0)) if match else {}
    except Exception:
        parsed = {}
    # The choice is the first field written, so a reply cut off mid-JSON still
    # carries it. Discarding the whole answer over a missing closing brace threw
    # away a usable pick.
    if not isinstance(parsed.get("best_index"), int):
        salvage = re.search(r'"best_index"\s*:\s*(\d+)', text or "")
        if salvage:
            parsed = {**parsed, "best_index": int(salvage.group(1)),
                      "reason": parsed.get("reason") or "(reply truncated)"}
    index = parsed.get("best_index")
    if not isinstance(index, int) or not 1 <= index <= len(paths):
        # "No frame shows finished work" is a real finding, not a failure to
        # answer — on this footage it is often the true one, because filming
        # stops while the tool is still on the workpiece. Reporting it as an
        # error would hide exactly the thing worth knowing.
        if parsed.get("none_suitable") or "best_index" in (text or ""):
            return {"error": None, "frame": None, "none_suitable": True,
                    "reason": parsed.get("reason") or "No sampled frame shows completed work.",
                    "runner_up": None, "cost_usd": None}
        return {"error": "no_choice", "message": "Model did not pick a usable frame.",
                "raw_text": (text or "")[:400]}

    usage = data.get("usage") or {}
    meta = MODELS_BY_ID.get(model, {})
    cost = usage.get("cost")
    if cost is None and meta:
        cost = ((usage.get("prompt_tokens") or 0) * meta["in_per_m"]
                + (usage.get("completion_tokens") or 0) * meta["out_per_m"]) / 1e6
    return {
        "error": None,
        "frame": paths[index - 1].name,
        "reason": parsed.get("reason"),
        "none_suitable": bool(parsed.get("none_suitable")),
        "runner_up": (paths[parsed["runner_up_index"] - 1].name
                      if isinstance(parsed.get("runner_up_index"), int)
                      and 1 <= parsed["runner_up_index"] <= len(paths) else None),
        "cost_usd": round(cost, 6) if cost is not None else None,
    }

# A weighted score maps onto the harness's pass/fail/unsure vocabulary so a
# holistic run sits in the same grid, and is scored by the same expectations, as
# a per-condition one.
RESULT_TO_VERDICT = {"PASS": "pass", "FAIL": "fail", "INSUFFICIENT IMAGE": "unsure"}


WEIGHT_RE = re.compile(r"^[\s•\-*]*(.+?)\s*[:\-]\s*(\d{1,3})\s*%\s*$", re.MULTILINE)


def parse_weights(criterion: str) -> dict[str, float]:
    """Pull "component: 40%" lines out of a rubric.

    The weights are stated in the rubric, so the overall score is arithmetic and
    should not be left to the model. In practice models are a few points out —
    components computing to 14.75 get reported as 18 — which is harmless until
    someone sets a pass mark at 75 and the difference decides a student's
    result.
    """
    weights = {}
    for name, value in WEIGHT_RE.findall(criterion or ""):
        name = name.strip()
        # A rubric line like "PASS requires 75 or higher" is not a component.
        if name and not name.lower().startswith(("pass", "fail", "score", "result")):
            weights[name] = float(value)
    return weights


def weighted_score(components: dict, weights: dict[str, float]) -> float | None:
    """Recompute the total from the rubric's own weights, where they line up."""
    if not components or not weights:
        return None

    def match(name: str) -> float | None:
        # Models paraphrase component names, so fall back to the longest
        # overlapping word run rather than demanding an exact key.
        if name in components:
            return components[name]
        low = name.lower()
        for key, value in components.items():
            k = str(key).lower()
            if k == low or k in low or low in k:
                return value
        return None

    total, used = 0.0, 0.0
    for name, weight in weights.items():
        value = match(name)
        if value is None:
            continue
        try:
            total += float(value) * weight
            used += weight
        except (TypeError, ValueError):
            continue
    if used == 0:
        return None
    # Renormalise so a partially matched rubric is not silently scored low.
    return round(total / used, 1)


def pass_mark_from(criterion: str) -> float | None:
    """Read "PASS requires 75 or higher" out of the rubric."""
    match = re.search(r"pass\s+requires\s+(\d{1,3})", criterion or "", re.IGNORECASE)
    return float(match.group(1)) if match else None


def parse_holistic(text: str) -> dict | None:
    """Read a weighted holistic reply, if the model produced one."""
    raw = (text or "").strip()
    candidates = re.findall(r"```(?:json)?\s*(.*?)```", raw, re.S) + [raw]
    start = raw.find("{")
    if start != -1:
        depth = 0
        for i, ch in enumerate(raw[start:], start):
            depth += (ch == "{") - (ch == "}")
            if depth == 0:
                candidates.append(raw[start : i + 1])
                break
    for candidate in candidates:
        try:
            data = json.loads(candidate.strip())
        except Exception:
            continue
        if not isinstance(data, dict) or "result" not in data:
            continue
        result = str(data.get("result", "")).strip().upper()
        if result not in RESULT_TO_VERDICT:
            continue
        try:
            score = max(0.0, min(100.0, float(data.get("score", 0))))
        except (TypeError, ValueError):
            score = 0.0
        defects = data.get("critical_defects")
        defects = [str(d) for d in defects] if isinstance(defects, list) else []
        components = data.get("component_scores")
        return {
            "result": result,
            "verdict": RESULT_TO_VERDICT[result],
            "score": score,
            # A score is a mark out of 100, not a probability, but the grid shows
            # a confidence — so surface it on the same 0-1 scale rather than
            # leaving the column empty and implying no signal.
            "confidence": round(score / 100, 2),
            "critical_defects": defects,
            "component_scores": components if isinstance(components, dict) else {},
            "rationale": data.get("reasoning"),
            "parse": "holistic",
        }
    return None


def build_draft_prompt(procedure: str, title: str | None = None,
                       with_photo: bool = True) -> str:
    parts = []
    if title:
        parts.append(f"TASK\n{title.strip()}")
    parts.append(f"PROCEDURE THE STUDENT FOLLOWED\n{procedure.strip()}")
    if with_photo:
        parts.append(
            "Write a photo-gradeable acceptance criterion for this work. Judge from the "
            "procedure what correct means; use the photo only to gauge what a camera can "
            "resolve here."
        )
    else:
        # Without a photo there is nothing to reason about resolvability from, so
        # say so rather than letting the model invent a frame it never saw.
        parts.append(
            "Write a photo-gradeable acceptance criterion for this work, from the "
            "procedure and handbook alone. NO photograph is attached, so leave "
            "`photo_limitations` null rather than speculating about one, and put the "
            "capture requirements a grader would need into `required_framing`."
        )
    return "\n\n".join(parts)


def json_candidates(raw: str) -> list[str]:
    """Every substring of a reply that might be the JSON object it promised.

    Models wrap JSON in prose or fences often enough that a bare json.loads
    would throw away otherwise good answers, so try the fenced blocks, the whole
    reply, and the first balanced brace run, in that order.
    """
    raw = (raw or "").strip()
    candidates = re.findall(r"```(?:json)?\s*(.*?)```", raw, re.S) + [raw]
    start = raw.find("{")
    if start != -1:
        depth = 0
        for i, ch in enumerate(raw[start:], start):
            depth += (ch == "{") - (ch == "}")
            if depth == 0:
                candidates.append(raw[start : i + 1])
                break
    return candidates


def parse_json_object(text: str) -> dict | None:
    """First candidate in `text` that parses as a JSON object, or None."""
    for candidate in json_candidates(text):
        try:
            data = json.loads(candidate.strip())
        except Exception:
            continue
        if isinstance(data, dict):
            return data
    return None


def _complete(
    *, model: str, system: str, user_text: str, image_paths: list[Path] | None = None,
    max_tokens: int = 4000, key: str | None = None, post=_post,
) -> dict:
    """One JSON-returning call, with cost accounting and no parsing opinions.

    Shared by criterion drafting and pack compilation. `image_paths` is
    optional: nine of the eleven pilot tasks are compiled from the procedure
    sheet and handbook alone, with no photograph in existence to send.
    """
    key = load_api_key() if key is None else key
    if not key:
        return {"error": "no_api_key", "message": "OPENROUTER_API_KEY is not set."}

    content: list[dict] = [{"type": "text", "text": user_text}]
    for path in image_paths or []:
        if not Path(path).exists():
            return {"error": "missing_frame", "message": f"Frame not found: {Path(path).name}"}
        content.append({"type": "image_url", "image_url": {"url": encode_image(Path(path))}})

    payload = {
        "model": model, "max_tokens": max_tokens, "temperature": 0,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": content},
        ],
    }
    started = time.monotonic()
    try:
        data = post(payload, key)
    except urllib.error.HTTPError as exc:
        detail = ""
        try:
            detail = json.load(exc).get("error", {}).get("message", "")
        except Exception:
            pass
        return {"error": f"http_{exc.code}", "message": detail or exc.reason}
    except Exception as exc:
        return {"error": "request_failed", "message": str(exc)[:300]}

    latency = round(time.monotonic() - started, 2)
    if isinstance(data, dict) and data.get("error"):
        return {"error": "api_error", "message": str(data["error"].get("message", ""))[:300]}
    try:
        text = data["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError):
        return {"error": "bad_response", "message": "No message content in reply."}
    if isinstance(text, list):  # some providers return content parts
        text = "".join(part.get("text", "") for part in text if isinstance(part, dict))

    usage = data.get("usage") or {}
    meta = MODELS_BY_ID.get(model, {})
    cost = usage.get("cost")
    if cost is None and meta:
        cost = ((usage.get("prompt_tokens") or 0) * meta["in_per_m"]
                + (usage.get("completion_tokens") or 0) * meta["out_per_m"]) / 1e6
    return {
        "error": None, "model": model, "text": text, "latency_s": latency,
        "prompt_tokens": usage.get("prompt_tokens") or 0,
        "completion_tokens": usage.get("completion_tokens") or 0,
        "cost_usd": round(cost, 6) if cost is not None else None,
    }


def parse_draft(text: str) -> dict:
    """Read a drafted criterion, reusing the tolerant JSON extraction above."""
    raw = (text or "").strip()
    candidates = json_candidates(raw)
    for candidate in candidates:
        try:
            data = json.loads(candidate.strip())
        except Exception:
            continue
        if isinstance(data, dict) and data.get("criterion"):
            not_gradeable = data.get("not_photo_gradeable") or []
            sources = data.get("sources") or []
            return {
                "criterion": str(data["criterion"]).strip(),
                "sources": [x for x in sources if isinstance(x, dict)],
                "not_photo_gradeable": [str(x) for x in not_gradeable
                                        if isinstance(not_gradeable, list)],
                "photo_limitations": data.get("photo_limitations"),
                "required_framing": data.get("required_framing"),
                "parse": "json",
            }
    # A criterion list is long enough to hit the token ceiling mid-JSON, and the
    # useful part — the criterion itself — is written first. Salvaging it beats
    # discarding a nearly complete answer, but the result is flagged `truncated`
    # so it is never mistaken for a clean parse.
    match = re.search(r'"criterion"\s*:\s*"((?:[^"\\]|\\.)*)', raw)
    if match:
        try:
            salvaged = json.loads(f'"{match.group(1)}"')
        except Exception:
            salvaged = match.group(1).replace("\\n", "\n")
        if salvaged.strip():
            return {"criterion": salvaged.strip(), "sources": [], "not_photo_gradeable": [],
                    "photo_limitations": "Reply was cut off before it finished; "
                                         "later fields are missing.",
                    "required_framing": None, "parse": "truncated", "raw_text": raw[:1200]}

    return {"criterion": None, "sources": [], "not_photo_gradeable": [],
            "photo_limitations": None, "required_framing": None,
            "parse": "unparsed", "raw_text": raw[:1200]}


def draft_criterion(
    *, model: str, image_path: Path | None = None, procedure: str,
    title: str | None = None, key: str | None = None, max_tokens: int = 4000, post=_post,
) -> dict:
    """Propose a photo-gradeable criterion from a procedure, optionally with a photo.

    The photo is optional because most pilot tasks have no photograph of the
    work to point at. Where one exists it is sent only so the model can judge
    what a camera at that distance resolves; the standard itself always comes
    from the procedure and handbook text.
    """
    if not (procedure or "").strip():
        return {"error": "no_procedure",
                "message": "No procedure text for this target to derive a criterion from."}

    result = _complete(
        model=model, system=DRAFT_PROMPT,
        user_text=build_draft_prompt(procedure, title, with_photo=bool(image_path)),
        image_paths=[image_path] if image_path else None,
        max_tokens=max_tokens, key=key, post=post,
    )
    if result.get("error"):
        return result
    return {"error": None, "model": model, "latency_s": result["latency_s"],
            "cost_usd": result["cost_usd"], **parse_draft(result["text"])}


# --------------------------------------------------------- pack compilation
#
# Nine of the eleven pilot tasks have a procedure sheet but no compiled pack, so
# the inspector's Atoms and Photo-assessment tabs are empty for them. These two
# prompts compile the missing structure: what the finished work must satisfy
# (`checks`), how it characteristically goes wrong (`error_modes`), and the
# photo criterion those imply. Ids, ordering and provenance are assigned by
# compile_pack.py — the model is asked only for judgement, never for bookkeeping
# it would have to keep consistent across a hundred independent calls.

PACK_STEP_PROMPT = """\
You are compiling an assessment pack for one step of an FAA Part 147 aircraft \
maintenance training task, from the campus procedure sheet and the governing FAA \
handbook.

For the ONE step named to you, produce the acceptance criteria and the failure \
modes an instructor would grade against.

A CHECK is a condition the finished work must satisfy — not a restatement of the \
action. "Cut the wire to length" is the action; "the cut is square and no strands \
are splayed or nicked" is the check. If a step genuinely has no inspectable \
outcome, return fewer checks rather than padding with restatements.

Mark each check with what evidence could settle it, and be strict, because this \
field decides whether a photo grader is asked an answerable question:

  photo        visible in a still of the finished work
  video        only evidenced by watching the action performed
  measurement  needs a dimension, force, torque or meter reading
  document     established from paperwork or a specification

A pull test, a continuity reading, a torque value and a tautness judgement are \
NEVER `photo`. Marking one `photo` is the failure that matters here: it lets a \
grader pass work it cannot actually see.

An ERROR MODE is a specific way this step goes wrong, with a severity:

  critical  defeats the purpose of the step, or is unsafe — especially when the \
finished work still looks plausible to a novice
  major     the work must be redone, and the defect is obvious on inspection
  minor     workmanship or cosmetic

Attribution must be exact, and over-attribution is the failure to avoid. Do NOT \
credit a number to the handbook unless that number appears verbatim in the \
handbook text you were given. If the procedure sheet specifies a figure and the \
handbook only speaks qualitatively — "tight and even", "as taut as possible" — \
the figure belongs to the procedure sheet ALONE. When you attribute anything to \
the handbook, quote the phrase you rely on in `note`. Say "both" only when each \
source independently states the same requirement.

Where a handbook extract is marked NOT cited by the procedure sheet, any standard \
drawn from it is provisional: use it, but set `assumed: true` on that check and \
say in `note` that it needs subject-matter confirmation.

If the two sources conflict — different wrap counts, different tolerances — do \
not silently pick a side. Record it in `conflicts`, following the procedure sheet \
as operative since that is what the student was taught.

Reply with JSON only, no prose around it:
{"checks": [
   {"statement": "<the condition, one sentence>",
    "observable": "photo" | "video" | "measurement" | "document",
    "source": "procedure sheet" | "handbook <name> p.<page>" | "both",
    "note": "<the standard it rests on; quote the handbook phrase if cited>",
    "assumed": true | false}
 ],
 "error_modes": [
   {"statement": "<how it goes wrong>", "severity": "critical" | "major" | "minor"}
 ],
 "criterion": "<photo-gradeable conditions, one per line, each starting with '- '>",
 "not_photo_gradeable": ["<conditions from the sources a photo cannot settle>"],
 "required_framing": "<how a student should frame the photo of this step, or null>",
 "conflicts": ["<procedure sheet says X, handbook says Y>"]}

Aim for 2-4 checks and 1-3 error modes. Keep each `note` under 30 words and each \
statement under 25, so the reply is not truncated."""

PACK_TASK_PROMPT = """\
You are compiling the task-level section of an assessment pack for an FAA Part \
147 aircraft maintenance training task.

You are given the campus procedure sheet, the governing FAA handbook extract, and \
the full list of steps with the checks already compiled for each.

Produce three things.

1. `evidence_required` — the photographs a student must submit. These are \
SEPARATE photos, each with its own subject and framing; do not describe one photo \
that shows everything. Include a non-photo item where an acceptance criterion is \
tactile or instrumented (a pull test, a continuity reading), with `medium` set \
accordingly and `assumed: true`, so it is visible that a still cannot settle it.

2. `criterion` — the whole-task acceptance criterion: what a complete, correct \
submission demonstrates, as independently checkable conditions.

3. `rationale` — two or three sentences on why this task does or does not suit \
photo assessment, naming what is readable from a still and what is not. You are \
told the campus's own fit rating; explain it, do not re-rate it.

Same attribution discipline as the step level: a number is credited to the \
handbook only if it appears verbatim in the text you were given, and a handbook \
attribution must quote the phrase it rests on. Anything drawn from a handbook \
extract marked NOT cited by the procedure sheet is provisional.

Reply with JSON only, no prose around it:
{"rationale": "<why photo assessment fits this task, or does not>",
 "evidence_required": [
   {"description": "<what this photo must show>",
    "medium": "photo" | "measurement" | "video" | "document",
    "framing": "<angle, distance, scale reference, lighting>",
    "assumed": true | false}
 ],
 "criterion": "<one condition per line, each starting with '- '>",
 "sources": [{"condition": "<abbreviated>", "source": "...", "note": "..."}],
 "not_photo_gradeable": ["<acceptance criteria a photo cannot settle>"],
 "open_questions": ["<what a subject-matter expert must settle before this pack is used>"]}

Aim for 3-5 evidence items. Keep each `note` under 30 words."""


def draft_pack_step(*, model: str, sources: str, step_text: str,
                    key: str | None = None, max_tokens: int = 4000, post=_post) -> dict:
    """Compile checks, error modes and a photo criterion for one procedure step."""
    user = f"{sources}\n\nSTEP TO COMPILE\n{step_text.strip()}\n\n" \
           "Compile the checks, error modes and photo criterion for THIS step only."
    result = _complete(model=model, system=PACK_STEP_PROMPT, user_text=user,
                       max_tokens=max_tokens, key=key, post=post)
    if result.get("error"):
        return result
    parsed = parse_json_object(result["text"])
    if parsed is None:
        return {"error": "unparsed", "message": "Reply was not JSON.",
                "raw_text": result["text"][:1200], "cost_usd": result["cost_usd"]}
    return {"error": None, "cost_usd": result["cost_usd"],
            "latency_s": result["latency_s"], **parsed}


def draft_pack_task(*, model: str, sources: str, summary: str,
                    key: str | None = None, max_tokens: int = 4000, post=_post) -> dict:
    """Compile evidence requirements and the task-level criterion."""
    user = f"{sources}\n\nCOMPILED STEPS\n{summary.strip()}\n\n" \
           "Compile the task-level evidence, criterion and rationale."
    result = _complete(model=model, system=PACK_TASK_PROMPT, user_text=user,
                       max_tokens=max_tokens, key=key, post=post)
    if result.get("error"):
        return result
    parsed = parse_json_object(result["text"])
    if parsed is None:
        return {"error": "unparsed", "message": "Reply was not JSON.",
                "raw_text": result["text"][:1200], "cost_usd": result["cost_usd"]}
    return {"error": None, "cost_usd": result["cost_usd"],
            "latency_s": result["latency_s"], **parsed}


# ------------------------------------------------------ subtask rubric drafting
#
# `packs/compile_pack.py` compiles one criterion per *step*, which is the right
# grain for an instructor reviewing a pack but the wrong grain for a grader: a
# student photographs a finished subtask ("the bend"), not each of the three
# steps that produced it. This prompt drafts the subtask-level rubric that
# `packs/generate_criteria.py` renders — the conditions a still of the completed
# subtask must satisfy, and the defects that condemn it outright.
#
# The rules below are not stylistic. Each one closes a way a rubric lets a
# grader answer a question the photograph cannot settle, which is the failure
# that produces a confident wrong verdict rather than an honest missing one.

RUBRIC_PROMPT = """\
You are writing a grading rubric for a vision-language model. The model will be \
shown ONE photograph of a completed subtask from an FAA Part 147 aircraft \
maintenance training task, and must judge it against your rubric.

Write the rubric from the campus procedure sheet and the FAA handbook extract \
supplied. Introduce nothing they do not support.

STAY INSIDE THIS SUBTASK
Grade the state the work is in when THIS subtask is finished, and no further. \
Work belonging to a later subtask has not happened yet, so its absence is the \
correct state, not a defect — if this subtask marks out an area and the next one \
cuts it, "no material has been removed" condemns exactly correct work. The scope \
note tells you what comes before and after.

GRADE THE RESULT, NOT THE WORK
The photograph shows finished work. Every criterion must describe evidence \
visible in the completed article. "The tube is cut to the marked length" is \
gradeable; "the student aligned the cutting wheel with the mark" is not, because \
the action is over. Never refer to the student, the technician, or what was done \
during the step.

EACH CRITERION
  * is independently gradeable PASS or FAIL on its own
  * states ONE requirement — do not join unrelated requirements with "and"
  * uses direct, concrete language naming what is seen
  * names the reference when verification needs one in frame: a gauge, ruler, \
scale, template, drawing, or a marking on the work. Write "measured against a \
rule in frame", not "measures 6 inches".
  * never requires a hidden property. Torque, pressure integrity, internal \
condition, material type, alloy, wall thickness and exact dimensions are not \
visible. Do not ask for them unless the supplied text says a marking, colour \
band or printed code carries them and that marking would be in the photograph.
  * ignores cosmetics unless appearance bears on function, integrity, fit, \
safety, or whether the work can be inspected.

EACH CRITICAL DEFECT
  * is a serious mistake that is AFFIRMATIVELY VISIBLE — something present in \
the frame, not something missing from the evidence
  * never fires from absence of confirmation. "The flare angle cannot be \
confirmed" is not a defect. "The flare is visibly split at the rim" is.
  * names the article it is looking at when the defect is something missing. \
"No sleeve is visible" condemns a badly framed photograph of correct work; "The \
tube end is bare between the B-nut and the flare, with no sleeve on it" \
condemns the work. Missing hardware is a real defect — write it so it fires on \
what is in the frame, not on what is out of it.
  * is a condition of the WORK, never a property of the photograph. A missing \
ruler, gauge or template in the frame is a framing problem, and the criterion \
that needs it already fails on its own — it is not a critical defect.
  * covers damage, wrong configuration, deformation, absent required hardware, \
unsafe routing, visible leakage, or another functionally unacceptable condition \
the sources support

NUMBERS AND ATTRIBUTION
Use a number only if it appears verbatim in the procedure or handbook text you \
were given. Source priority is: task-specific controlling data, then the \
procedure sheet, then the FAA handbook or advisory circular. Follow the \
higher-priority source where they differ, and record the disagreement in \
`source_notes` — never resolve a conflict silently. List a manual page in \
`manual_sources` only if a criterion you wrote actually rests on text from that \
page.

NEVER
Never write "INSUFFICIENT IMAGE". Never assign a percentage weight or a point \
value to a criterion. Never claim a photograph establishes airworthiness, \
internal condition, pressure integrity, torque, material type, or an exact \
dimension.

Reply with JSON only, no prose around it:
{"subtask_description": "<noun phrase for the finished work, e.g. 'flared tube \
end with its sleeve and B-nut' — completes the sentence 'Assess the completed \
___ visible in the image.'>",
 "criteria": ["<one visible requirement, under 20 words>"],
 "critical_defects": ["<one affirmative visible defect, under 15 words>"],
 "procedure_sources": ["<procedure document and section>"],
 "manual_sources": ["<handbook, chapter and page, only where relied on>"],
 "source_notes": ["<conflict between sources, or a limitation of what the \
photograph can settle>"]}

Give 4 to 6 criteria and 3 to 5 critical defects. Stay inside the word limits: \
the rendered rubric has a hard 300-word ceiling and overrunning it fails \
validation."""


def draft_subtask_rubric(*, model: str, sources: str, subtask: str,
                         adjust: str | None = None, key: str | None = None,
                         max_tokens: int = 3000, post=_post) -> dict:
    """Draft the criteria and critical defects for one procedure subtask.

    `adjust` carries a length correction back into a second attempt. The rendered
    rubric has to land between 100 and 300 words, and that is a property of the
    assembled Markdown rather than of anything the model can count, so the caller
    measures it and asks again rather than guessing at the prompt.
    """
    user = f"{sources}\n\nSUBTASK TO GRADE\n{subtask.strip()}\n\n" \
           "Write the rubric for THIS subtask only."
    if adjust:
        user += f"\n\nREVISION REQUIRED\n{adjust.strip()}"
    result = _complete(model=model, system=RUBRIC_PROMPT, user_text=user,
                       max_tokens=max_tokens, key=key, post=post)
    if result.get("error"):
        return result
    parsed = parse_json_object(result["text"])
    if parsed is None:
        return {"error": "unparsed", "message": "Reply was not JSON.",
                "raw_text": result["text"][:1200], "cost_usd": result["cost_usd"]}
    return {"error": None, "cost_usd": result["cost_usd"],
            "latency_s": result["latency_s"], **parsed}


# --------------------------------------------------------- step/atom rubrics
#
# One step, one frame, a handful of words. The compiled atoms already say what
# the step must satisfy (`checks`) and how it goes wrong (`error_modes`); this
# turns the photo-observable ones into something a grader can answer while
# looking at the single frame where that step ends. It is deliberately much
# smaller than the subtask rubric: a step is a slice of work, the frame is a
# guess at where it finished, and a long rubric would imply more certainty about
# both than either deserves.

STEP_PROMPT = """\
You are writing a very short grading rubric for a vision-language model. It will \
see ONE frame — the moment this single step of an aircraft maintenance procedure \
finishes — and judge it against your rubric.

You are given the step, the acceptance checks already compiled for it, and the \
error modes already compiled for it. Each check is marked with the evidence that \
settles it. USE ONLY the checks marked `photo`. A check marked `measurement`, \
`document` or `video` cannot be answered from a frame, and turning one into a \
grading point invites a grader to pass work it cannot see.

BE SHORT. One to three grading points, and the whole rubric — points and \
critical mistakes together — under 100 words. This is a slice of work caught \
mid-procedure, not a finished article; there is not much a single frame can \
honestly settle, and padding it out manufactures confidence.

EACH GRADING POINT
  * is independently gradeable PASS or FAIL from the frame alone
  * states ONE thing that is visible, in plain concrete words
  * describes the state the work is in when this step ends — not the action, \
and not work belonging to a later step
  * never asks for a torque, a pressure, an exact dimension, an alloy, or an \
internal condition

EACH CRITICAL MISTAKE
  * is a serious error that is affirmatively VISIBLE in the frame
  * never fires because something is out of shot or cannot be made out — that \
is a framing problem, not a defect
  * is drawn from the compiled error modes, preferring those marked `critical`

If the frame is mid-action — a tool in the way, hands over the work, the article \
still in a fixture — say so in `frame_limits`. That is the normal case for a \
step caught in progress, and naming it is more useful than pretending the view \
is clean.

Reply with JSON only, no prose around it:
{"grading_points": ["<one visible requirement, under 18 words>"],
 "critical_mistakes": ["<one affirmative visible error, under 14 words>"],
 "frame_limits": "<what this frame probably cannot show, one short sentence, \
or null>"}

One to three grading points, one to three critical mistakes. Under 100 words \
in total."""


def draft_step_rubric(*, model: str, sources: str, step: str, adjust: str | None = None,
                      key: str | None = None, max_tokens: int = 1200, post=_post) -> dict:
    """Draft the 1-3 grading points and critical mistakes for one procedure step."""
    user = f"{sources}\n\nSTEP TO GRADE\n{step.strip()}\n\n" \
           "Write the short frame rubric for THIS step only."
    if adjust:
        user += f"\n\nREVISION REQUIRED\n{adjust.strip()}"
    result = _complete(model=model, system=STEP_PROMPT, user_text=user,
                       max_tokens=max_tokens, key=key, post=post)
    if result.get("error"):
        return result
    parsed = parse_json_object(result["text"])
    if parsed is None:
        return {"error": "unparsed", "message": "Reply was not JSON.",
                "raw_text": result["text"][:1200], "cost_usd": result["cost_usd"]}
    return {"error": None, "cost_usd": result["cost_usd"],
            "latency_s": result["latency_s"], **parsed}


def build_adequacy_prompt(description: str, framing: str | None = None) -> str:
    parts = [f"THE PHOTO IS REQUIRED TO SHOW\n{description.strip()}"]
    if framing and framing.strip():
        parts.append(f"REQUIRED FRAMING\n{framing.strip()}")
    parts.append("Is the attached photo usable evidence for that subject?")
    return "\n\n".join(parts)


def build_user_prompt(criterion: str, context: str | None = None) -> str:
    parts = [f"CRITERION\n{criterion.strip()}"]
    if context and context.strip():
        parts.append(f"TASK CONTEXT (background only — do not grade against this)\n{context.strip()}")
    parts.append("Grade the attached photo against the CRITERION.")
    return "\n\n".join(parts)


DEFAULT_PASS_THRESHOLD = 0.95
DEFAULT_FAIL_THRESHOLD = 0.20


def apply_thresholds(
    conditions: list[dict],
    pass_at: float = DEFAULT_PASS_THRESHOLD,
    fail_at: float = DEFAULT_FAIL_THRESHOLD,
) -> dict:
    """Turn per-condition probabilities into one verdict at a chosen threshold.

    Two rules carry the safety weight:

    - An unobservable condition can never pass, at any threshold. Raising the bar
      makes passing harder, never easier, and no amount of model confidence
      substitutes for a feature that is not in the photo.
    - One failed condition fails the criterion. A crimp with a clean indent and
      an exposed conductor is not "mostly correct"; it is rejected.

    Thresholds live here rather than in the prompt so they can be retuned against
    a saved run without spending another call.
    """
    graded, blocked = [], []
    for condition in conditions:
        probability = condition.get("p_correct")
        if not condition.get("observable") or probability is None:
            blocked.append(condition)
            condition["verdict"] = "unsure"
        elif probability >= pass_at:
            condition["verdict"] = "pass"
            graded.append(condition)
        elif probability <= fail_at:
            condition["verdict"] = "fail"
            graded.append(condition)
        else:
            condition["verdict"] = "unsure"
            blocked.append(condition)

    failed = [c for c in graded if c["verdict"] == "fail"]
    passed = [c for c in graded if c["verdict"] == "pass"]
    if failed:
        verdict = "fail"
    elif blocked:
        verdict = "unsure"
    elif passed:
        verdict = "pass"
    else:
        verdict = "unsure"

    probabilities = [c["p_correct"] for c in conditions
                     if c.get("observable") and c.get("p_correct") is not None]
    return {
        "verdict": verdict,
        # Confidence in the *verdict*: for a pass, the weakest link, since the
        # criterion is only as satisfied as its least certain condition.
        "confidence": (min(probabilities) if verdict == "pass" and probabilities
                       else max(probabilities) if verdict == "fail" and probabilities
                       else 0.0),
        "conditions_total": len(conditions),
        "conditions_passed": len(passed),
        "conditions_failed": len(failed),
        "conditions_blocked": len(blocked),
        "thresholds": {"pass": pass_at, "fail": fail_at},
    }


def parse_verdict(text: str) -> dict:
    """Pull the verdict object out of a model reply.

    Models wrap JSON in prose or fences often enough that a bare json.loads
    would throw away otherwise good answers, so fall back to the first balanced
    object in the text, then to a keyword scan. A reply we cannot read at all
    becomes `unsure` with a parse note rather than a silent pass.
    """
    raw = (text or "").strip()
    if not raw:
        return {"verdict": "unsure", "confidence": 0.0, "rationale": "Empty reply.",
                "observed": None, "missing_evidence": None, "parse": "empty"}

    candidates = []
    fenced = re.findall(r"```(?:json)?\s*(.*?)```", raw, re.S)
    candidates.extend(fenced)
    candidates.append(raw)
    start = raw.find("{")
    if start != -1:
        depth = 0
        for i, ch in enumerate(raw[start:], start):
            depth += (ch == "{") - (ch == "}")
            if depth == 0:
                candidates.append(raw[start : i + 1])
                break

    for candidate in candidates:
        try:
            data = json.loads(candidate.strip())
        except Exception:
            continue
        if not isinstance(data, dict):
            continue

        # Per-condition form: the caller applies thresholds to these.
        if isinstance(data.get("conditions"), list) and data["conditions"]:
            conditions = []
            for item in data["conditions"]:
                if not isinstance(item, dict):
                    continue
                probability = item.get("p_correct")
                try:
                    probability = (None if probability is None
                                   else min(1.0, max(0.0, float(probability))))
                except (TypeError, ValueError):
                    probability = None
                conditions.append({
                    "text": str(item.get("text") or "").strip(),
                    "observable": bool(item.get("observable")),
                    "p_correct": probability,
                    "note": item.get("note"),
                })
            if conditions:
                return {
                    "conditions": conditions,
                    "observed": data.get("observed"),
                    "missing_evidence": data.get("missing_evidence"),
                    "parse": "conditions",
                }

        verdict = str(data.get("verdict", "")).strip().lower()
        if verdict not in VERDICTS:
            continue
        try:
            confidence = min(1.0, max(0.0, float(data.get("confidence", 0.0))))
        except (TypeError, ValueError):
            confidence = 0.0
        return {
            "verdict": verdict,
            "confidence": confidence,
            "observed": data.get("observed"),
            "rationale": data.get("rationale"),
            "missing_evidence": data.get("missing_evidence"),
            "parse": "json",
        }

    # A truncated conditions array still contains complete condition objects
    # before the cut. Discarding them would throw away most of a graded
    # criterion because the reply ran a few tokens long, so salvage the ones
    # that closed cleanly and mark the result as partial.
    if '"conditions"' in raw:
        salvaged = []
        for match in re.finditer(r"\{[^{}]*\}", raw[raw.index('"conditions"'):]):
            try:
                item = json.loads(match.group(0))
            except Exception:
                continue
            if isinstance(item, dict) and item.get("text") is not None:
                probability = item.get("p_correct")
                try:
                    probability = (None if probability is None
                                   else min(1.0, max(0.0, float(probability))))
                except (TypeError, ValueError):
                    probability = None
                salvaged.append({
                    "text": str(item.get("text")).strip(),
                    "observable": bool(item.get("observable")),
                    "p_correct": probability,
                    "note": item.get("note"),
                })
        if salvaged:
            return {"conditions": salvaged, "observed": None,
                    "missing_evidence": "Reply was cut off; later conditions may be missing.",
                    "parse": "conditions_truncated"}

    lowered = raw.lower()
    for verdict in VERDICTS:
        if re.search(rf'"?verdict"?\s*[:=]\s*"?{verdict}\b', lowered):
            return {"verdict": verdict, "confidence": 0.0, "observed": None,
                    "rationale": raw[:600], "missing_evidence": None, "parse": "keyword"}

    return {"verdict": "unsure", "confidence": 0.0, "observed": None,
            "rationale": raw[:600], "missing_evidence": None, "parse": "unparsed"}


def grade(
    *,
    model: str,
    image_path: Path | None = None,
    criterion: str,
    context: str | None = None,
    key: str | None = None,
    max_tokens: int = 3000,
    post=_post,
    mode: str = "correctness",
    framing: str | None = None,
    image_paths: list[Path] | None = None,
    pass_at: float = DEFAULT_PASS_THRESHOLD,
    fail_at: float = DEFAULT_FAIL_THRESHOLD,
) -> dict:
    """Grade a photo — or a set of photos — against one criterion.

    `image_paths` carries a whole submission: task-level assessment asks whether
    the finished work is right, and the pack requires several photos to show
    that, so forcing it through a single frame guarantees a failure that says
    nothing about the work. `image_path` remains for the single-photo case.

    `mode` selects the grader: "correctness" judges the work, "adequacy" judges
    whether the photo is usable evidence at all.

    `post` is injectable so the tests exercise parsing and cost accounting
    without touching the network.
    """
    paths = [Path(p) for p in (image_paths or ([image_path] if image_path else []))]
    # `None` means "use the ambient key"; an explicit "" means "no key", which
    # must not silently fall back to the one in the environment.
    key = load_api_key() if key is None else key
    if not key:
        return {"model": model, "error": "no_api_key",
                "message": "OPENROUTER_API_KEY is not set.", "verdict": None}
    if not paths:
        return {"model": model, "error": "no_image",
                "message": "No frame supplied.", "verdict": None}
    missing = [p for p in paths if not p.exists()]
    if missing:
        return {"model": model, "error": "missing_frame",
                "message": f"Frame not found: {missing[0].name}", "verdict": None}

    if mode == "adequacy":
        text = build_adequacy_prompt(criterion, framing)
    elif mode == "holistic":
        text = (f"CRITERION AND SCORING RUBRIC\n{criterion.strip()}\n\n"
                "Grade the attached photo of the finished work against this rubric.")
    else:
        text = build_user_prompt(criterion, context)
    # With several photos the model must be told they are one submission, or it
    # grades whichever it happened to look at last.
    if len(paths) > 1:
        text += (
            f"\n\n{len(paths)} photos are attached, numbered in order. They are ONE "
            "submission covering this work between them. A criterion is met if any "
            "photo shows it met; judge the set, not each photo separately."
        )

    content = [{"type": "text", "text": text}]
    for path in paths:
        content.append({"type": "image_url", "image_url": {"url": encode_image(path)}})

    payload = {
        "model": model,
        "max_tokens": max_tokens,
        "temperature": 0,
        "messages": [
            {"role": "system", "content": MODES.get(mode, SYSTEM_PROMPT)},
            {"role": "user", "content": content},
        ],
    }

    started = time.monotonic()
    try:
        data = post(payload, key)
    except urllib.error.HTTPError as exc:
        detail = ""
        try:
            detail = json.load(exc).get("error", {}).get("message", "")
        except Exception:
            pass
        return {"model": model, "error": f"http_{exc.code}", "message": detail or exc.reason,
                "verdict": None, "latency_s": round(time.monotonic() - started, 2)}
    except Exception as exc:
        return {"model": model, "error": "request_failed", "message": str(exc)[:300],
                "verdict": None, "latency_s": round(time.monotonic() - started, 2)}

    latency = round(time.monotonic() - started, 2)
    if isinstance(data, dict) and data.get("error"):
        return {"model": model, "error": "api_error",
                "message": str(data["error"].get("message", ""))[:300],
                "verdict": None, "latency_s": latency}

    try:
        text = data["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError):
        return {"model": model, "error": "bad_response",
                "message": "No message content in reply.", "verdict": None, "latency_s": latency}
    if isinstance(text, list):  # some providers return content parts
        text = "".join(part.get("text", "") for part in text if isinstance(part, dict))

    parsed = parse_holistic(text) if mode == "holistic" else None
    if parsed is not None:
        weights = parse_weights(criterion)
        computed = weighted_score(parsed.get("component_scores") or {}, weights)
        if computed is not None:
            parsed["model_score"] = parsed["score"]
            parsed["score"] = computed
            parsed["confidence"] = round(computed / 100, 2)
            parsed["weights"] = weights
            # The pass mark applies to the recomputed total, but a critical
            # defect caps at FAIL whatever the arithmetic says. Record the mark
            # either way, and say when a defect overrode it — "FAIL at 82/100"
            # is only intelligible if the reason is visible.
            mark = pass_mark_from(criterion)
            parsed["pass_mark"] = mark
            if parsed.get("critical_defects"):
                parsed["result"] = "FAIL"
                parsed["verdict"] = "fail"
                parsed["failed_on"] = "critical_defect"
            elif mark is not None:
                parsed["result"] = "PASS" if computed >= mark else "FAIL"
                parsed["verdict"] = RESULT_TO_VERDICT[parsed["result"]]
                parsed["failed_on"] = None if computed >= mark else "below_pass_mark"
    if parsed is None:
        parsed = parse_verdict(text)
    # Per-condition replies carry no verdict of their own; the threshold turns
    # them into one. Single-verdict replies (adequacy, or a model that ignored
    # the schema) already have one and are left alone.
    if parsed.get("parse") in ("conditions", "conditions_truncated"):
        parsed = {**parsed, **apply_thresholds(parsed["conditions"], pass_at, fail_at)}

    usage = data.get("usage") or {}
    meta = MODELS_BY_ID.get(model, {})
    prompt_tokens = usage.get("prompt_tokens") or 0
    completion_tokens = usage.get("completion_tokens") or 0
    cost = usage.get("cost")
    if cost is None and meta:
        cost = (prompt_tokens * meta["in_per_m"] + completion_tokens * meta["out_per_m"]) / 1e6

    return {
        "model": model,
        "error": None,
        "mode": mode,
        "image_count": len(paths),
        "latency_s": latency,
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "cost_usd": round(cost, 6) if cost is not None else None,
        "raw_text": text,
        **parsed,
    }


def grade_many(jobs: list[dict], key: str | None = None, workers: int = 4, post=_post,
               pass_at: float = DEFAULT_PASS_THRESHOLD,
               fail_at: float = DEFAULT_FAIL_THRESHOLD) -> list[dict]:
    """Run a batch of grade() calls concurrently, preserving input order.

    Each job carries its own `cell` metadata (which target, which model, whether
    it is a mismatch control) which is echoed back so the caller can rebuild the
    grid without re-deriving anything.
    """
    key = load_api_key() if key is None else key

    def run(job: dict) -> dict:
        result = grade(
            model=job["model"],
            image_path=Path(job["image_path"]) if job.get("image_path") else None,
            image_paths=[Path(p) for p in job["image_paths"]] if job.get("image_paths") else None,
            criterion=job["criterion"],
            context=job.get("context"),
            mode=job.get("mode", "correctness"),
            pass_at=pass_at, fail_at=fail_at,
            framing=job.get("framing"),
            key=key,
            post=post,
        )
        return {**job.get("cell", {}), **result}

    if not jobs:
        return []
    with ThreadPoolExecutor(max_workers=max(1, min(workers, len(jobs)))) as pool:
        return list(pool.map(run, jobs))


def estimate_cost(model_ids: list[str], calls_per_model: int) -> dict:
    """Rough pre-run estimate. A 960px frame lands near 1.5k image tokens."""
    IMAGE_TOKENS, TEXT_IN, TEXT_OUT = 1500, 400, 250
    total = 0.0
    per_model = {}
    for model_id in model_ids:
        meta = MODELS_BY_ID.get(model_id)
        if not meta:
            continue
        cost = calls_per_model * (
            (IMAGE_TOKENS + TEXT_IN) * meta["in_per_m"] + TEXT_OUT * meta["out_per_m"]
        ) / 1e6
        per_model[model_id] = round(cost, 4)
        total += cost
    return {"total_usd": round(total, 4), "per_model": per_model,
            "calls": calls_per_model * len(per_model)}
