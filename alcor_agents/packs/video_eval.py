#!/usr/bin/env python3
"""Grades each subtask's criteria against its video sequence, once per model.

The photo eval answers one criterion about one still and abstains on more than
half of them — 1,045 of 1,952 saved verdicts are `unsure`, nearly all of them a
model saying the frame does not show the thing being asked about. That is not
usually the grader being timid. A still is one instant of a process, and much of
what a criterion asks about is a state that held during the work rather than at
the moment filming stopped.

This grades the span instead: every sampled frame of a subtask, in order, each
labelled with its timestamp.

ONE CALL PER SUBTASK PER MODEL — not one per criterion. Beyond the obvious
saving of not re-uploading a 23-frame sequence once per point, the sheet is a set
of conditions about the same article, and a grader that reads them together can
use one to place another. Splitting them into independent calls throws that away.

The points come from the latest photo run rather than from build/criteria/, so
the two runs grade the same points in the same order and a clip verdict can be
set against a photo verdict for the same line. A video run keyed differently
would produce a screen that looks like a comparison and is not one.

    python3 packs/video_eval.py --task AM.II.A.S6            # one task
    python3 packs/video_eval.py --all                        # every task with a run
    python3 packs/video_eval.py --task AM.II.A.S6 --estimate # price it, call nothing
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
import urllib.request
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "inspector"))

import vlm  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent))
import vertex_transport  # noqa: E402

BUILD = ROOT / "build"
FRAMES = BUILD / "frames"
PHOTO_EVAL = BUILD / "photo_eval"
VIDEO_EVAL = BUILD / "video_eval"

# Same four, in the same column order the portal grid uses. Kept as a literal
# rather than read from vlm.MODELS, which also carries the local candidates.
MODEL_IDS = [
    "anthropic/claude-opus-5",
    "google/gemini-3.1-pro-preview",
    "google/gemini-3.6-flash",
    "openai/gpt-5.6-sol",
]

# Must match SAMPLE_FPS in scripts/build_portal_data.py. The screen shows the
# frames it says it grades on, so these two rates are one number in two places —
# if they drift, the portal draws a sequence that was never sent.
SAMPLE_FPS = 0.5

SCHEMA_VERSION = 1


# ── frames ─────────────────────────────────────────────────────────────────

def frame_seconds(name: str) -> float | None:
    match = re.match(r"t(\d+)_(\d+)", Path(name).stem)
    return int(match.group(1)) + int(match.group(2)) / 100.0 if match else None


def clip_frames(code: str, clip: str) -> list[Path]:
    directory = FRAMES / code / clip
    if not directory.is_dir():
        return []
    return sorted(p for p in directory.glob("*.jpg") if frame_seconds(p.name) is not None)


def sample_picks(code: str, clip: str) -> list[Path]:
    """A clip's frames at SAMPLE_FPS — mirrors build_portal_data.sample_picks."""
    allf = clip_frames(code, clip)
    if not allf:
        return []
    times = [frame_seconds(p.name) for p in allf]
    step, out, used = 1 / SAMPLE_FPS, [], set()
    tick, end = 0.0, times[-1]
    while tick <= end + 1e-9:
        i = min(range(len(times)), key=lambda k: abs(times[k] - tick))
        if i not in used:
            used.add(i)
            out.append(allf[i])
        tick += step
    return out


def span_frames(code: str, clip: str, t0: float | None, t1: float | None) -> list[Path]:
    """The sampled frames inside a subtask's span, bounds included."""
    picks = sample_picks(code, clip)
    if t0 is None or t1 is None:
        return picks
    return [p for p in picks if t0 - 1e-9 <= (frame_seconds(p.name) or 0) <= t1 + 1e-9]


def frames_for(model: str, frames: list[Path]) -> list[Path]:
    """The span's frames, thinned to what one call to this model may carry.

    Even spacing, first and last kept: the span's endpoints are where a subtask
    is entered and left, and a thinning that slid off either would grade a
    different span than the screen states. The cap comes from the model's
    registry entry — see the note on `max_frames` in vlm.py for why exceeding
    it is not a longer call but no call at all.
    """
    cap = (vlm.MODELS_BY_ID.get(model) or {}).get("max_frames")
    if not cap or len(frames) <= cap:
        return frames
    idx = {round(i * (len(frames) - 1) / (cap - 1)) for i in range(cap)}
    return [frames[i] for i in sorted(idx)]


# ── the points to grade ────────────────────────────────────────────────────

def latest_photo_run(code: str) -> dict | None:
    """The newest photo run that actually graded something.

    Newest-by-name is not enough. `run_1786055907_AM.II.A.S6.json` is 1,060 rows
    of `http_402` — a run started after the OpenRouter balance ran out, which
    wrote a full-sized file containing no verdicts at all. Taking it would grade
    the sequence against criteria whose photo column is empty, producing a
    comparison screen with nothing on one side and no error anywhere to say why.
    A run with no verdicts is not a run.
    """
    directory = PHOTO_EVAL / code
    runs = sorted(directory.glob(f"run_*_{code}.json")) if directory.is_dir() else []
    for path in reversed(runs):
        try:
            run = json.loads(path.read_text())
        except (OSError, json.JSONDecodeError):
            continue
        if any(r.get("verdict") for r in run.get("results", [])):
            return run
        print(f"    skipping {path.name} — no verdicts in it")
    return None


def subtasks_from(run: dict) -> "OrderedDict[str, dict]":
    """Group a photo run's original points by subtask, in grid order.

    Controls are skipped. A perturbed sheet is a probe of the grader's agreement
    on a still; running it again on video would double the spend to answer a
    question about the photo run, not this one.

    So are the two other probe forms, on the same reasoning and for the same reason
    the portal drops them: `step:dl.s1` is one step of a section this run already
    grades whole, and `section:...#vmsghdgf5` is that section's criterion reworded
    for a match test. Grading them here would put AM.I.D.S1 at thirty sequences
    over seven clips — most of them a slice of a clip already being graded end to
    end — where the runs that came out right graded one span per clip. It also has
    to agree with `build_portal_data.py`: the screen shows one row per section, and
    a verdict against a row the screen does not draw is a verdict nobody can read.
    """
    groups: OrderedDict[str, dict] = OrderedDict()
    for result in run.get("results", []):
        if result.get("is_control") or result.get("polarity") not in (None, "original"):
            continue
        gid = result.get("rolls_up_to") or result.get("target_id")
        if gid.startswith("step:") or "#" in gid:
            continue
        tid = result.get("target_id")
        group = groups.setdefault(gid, {
            "gid": gid,
            "label": result.get("parent_label") or result.get("label") or gid,
            "video": result.get("video"),
            "frame": result.get("frame"),
            "points": OrderedDict(),
        })
        # One entry per target, not per (target, model): `results` is flat, so
        # the same point appears once for each model that graded it.
        if tid not in group["points"]:
            group["points"][tid] = {
                "target_id": tid,
                "criterion": result.get("criterion") or result.get("label") or tid,
                "frame": result.get("frame"),
            }
        group["video"] = group["video"] or result.get("video")
    return groups


def step_count(label: str) -> int:
    """The "(3 steps)" a run writes into `parent_label`, or 0.

    This is how the runner tells a compiled subtask from one that exists only in
    the run, without reading the pack — and that distinction decides the span.
    """
    match = re.search(r"\((\d+)\s+steps?\)", label or "")
    return int(match.group(1)) if match else 0


def span_for(group: dict, groups: "OrderedDict[str, dict]") -> tuple[float | None, float | None]:
    """A subtask's interval on its clip.

    Mirrors `spanFor` in portal/app.js exactly, and the exactness is the point:
    the screen labels the sequence it displays with a frame count, and if the
    runner grades a different span than the screen draws, the tab shows a verdict
    beside evidence that did not produce it.

    A subtask with compiled steps takes the WHOLE clip — the clip was shot for
    that subtask, so bounding it against a neighbour would cut away most of the
    work. Only run-only targets, several of which share one clip, get the
    interval from the previous target's graded frame to their own.
    """
    clip = group.get("video")
    if not clip:
        return None, None
    own = frame_seconds(group.get("frame") or "") if group.get("frame") else None
    if step_count(group.get("label", "")) or own is None:
        return None, None  # whole clip

    earlier = [
        frame_seconds(g["frame"])
        for g in groups.values()
        if g is not group and g.get("video") == clip and g.get("frame")
        and not step_count(g.get("label", ""))
        and (frame_seconds(g["frame"]) or 0) < own
    ]
    earlier = [t for t in earlier if t is not None]
    return (max(earlier) if earlier else 0.0), own


# ── cost ───────────────────────────────────────────────────────────────────

# Rough, and deliberately so: an image's token cost depends on the provider's
# tiling. This is for the go/no-go before spending, not for accounting — the
# real figures come back per call and are what the run file records.
TOKENS_PER_FRAME = 800


def estimate(frames: int, points: int, model_id: str) -> float:
    meta = next((m for m in vlm.MODELS if m["id"] == model_id), None)
    if not meta:
        return 0.0
    prompt = frames * TOKENS_PER_FRAME + points * 40 + 700
    completion = points * 60 + 80
    return (prompt * meta["in_per_m"] + completion * meta["out_per_m"]) / 1e6


# ── routes ─────────────────────────────────────────────────────────────────

OPENROUTER_CREDITS = "https://openrouter.ai/api/v1/credits"


def openrouter_balance() -> float | None:
    """Credit left on the OpenRouter account, or None if it could not be asked.

    A key outlives the credit that made it useful. The account is spent —
    `/credits` reports usage past the balance and every call returns 402 — while
    `OPENROUTER_API_KEY` sits in `.env` exactly as it did before. So the
    pre-flight has to ask what is left rather than whether a key exists, or it
    green-lights arms with no route: the run then spends on the arms that work
    and writes ungraded points for the ones that never had a chance, with
    nothing on the run distinguishing the two.

    None means the question could not be put, which is not the same as an answer
    of nothing. The caller proceeds on the key alone and says that it did.
    """
    key = vlm.load_api_key()
    if not key:
        return 0.0
    request = urllib.request.Request(
        OPENROUTER_CREDITS, headers={"Authorization": f"Bearer {key}"})
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            data = (json.load(response) or {}).get("data") or {}
    except Exception:
        return None
    granted, used = data.get("total_credits"), data.get("total_usage")
    if granted is None or used is None:
        return None
    return round(granted - used, 4)


def local_up(model: str) -> bool:
    """Whether this arm's `llama-server` is answering on its own port.

    The third route fails a third way, and not over a credential: an on-device
    arm needs no key and no project, only a server holding that checkpoint.
    Asked here rather than per call for the same reason as the other two — a
    port with nothing behind it fails every call in that column with a
    connection error that reads like a model fault.

    Asked of `/health`, which is the one endpoint that distinguishes serving from
    listening: llama-server binds its port as it starts and answers `/v1/models`
    while the weights are still loading, so that route calls a server ready some
    minutes before it is.
    """
    endpoint = (vlm.MODELS_BY_ID.get(model) or {}).get("endpoint")
    if not endpoint:
        return False
    try:
        with urllib.request.urlopen(endpoint.split("/v1/")[0] + "/health", timeout=5) as r:
            return (json.load(r) or {}).get("status") == "ok"
    except Exception:
        return False


def resolve_routes(models: list[str]) -> tuple[dict[str, str], str]:
    """The route each arm takes, decided once here and carried to every call.

    One decision, in one place, because the alternative is what this runner did:
    the pre-flight asked `reachable()` and printed a plan, and `one()` re-asked
    `available()` per call and followed a different one. Those two disagree in
    exactly the case that matters — a key file whose service account has been
    deleted is configured but cannot mint a token — so the run announced
    `openrouter` for the Gemini arms and then sent every one of them to Vertex,
    where they failed identically. A plan that is not what runs is worse than no
    plan: the saved file then records a route the call never took.

    A local arm answers to neither question. Routing it by `supports()` alone put
    it on the OpenRouter branch, where a spent balance aborted a run that was
    never going to touch OpenRouter — and, had it started, stamped its rows
    `openrouter` in the run file.
    """
    vertex, why = vertex_transport.reachable()
    routes = {}
    for model in models:
        if vlm.is_local(model):
            routes[model] = "local"
        elif vertex and vertex_transport.supports(model):
            routes[model] = "vertex"
        else:
            routes[model] = "openrouter"
    return routes, why


# ── the run ────────────────────────────────────────────────────────────────

def grade_task(code: str, *, models: list[str], dry_run: bool = False,
               workers: int = 4, routes: dict[str, str] | None = None) -> dict | None:
    run = latest_photo_run(code)
    if not run:
        print(f"  {code}: no photo run — nothing to grade against")
        return None

    groups = subtasks_from(run)
    if not groups:
        print(f"  {code}: photo run has no original points")
        return None

    jobs, skipped = [], []
    for group in groups.values():
        points = list(group["points"].values())
        t0, t1 = span_for(group, groups)
        frames = span_frames(code, group["video"], t0, t1) if group.get("video") else []
        if not frames or not points:
            skipped.append((group["gid"], "no frames" if not frames else "no points"))
            continue
        jobs.append({"group": group, "points": points, "frames": frames,
                     "t0": t0, "t1": t1})

    if not jobs:
        print(f"  {code}: no subtask has both frames and points")
        return None

    calls = len(jobs) * len(models)
    cost = sum(estimate(len(j["frames"]), len(j["points"]), m) for j in jobs for m in models)
    total_points = sum(len(j["points"]) for j in jobs)
    print(f"  {code}: {len(jobs)} subtasks · {total_points} points · "
          f"{calls} calls · ~${cost:.2f}")
    for gid, why in skipped:
        print(f"    skipped {gid} — {why}")
    if dry_run:
        return None

    # Route per model, not per run: the Gemini arms can go to Vertex while the
    # others cannot, and a run that mixes routes is still one run — same prompt,
    # same parsing, same schema — so the columns stay comparable. Resolved once
    # for the whole run, never re-derived per call.
    if routes is None:
        routes, _ = resolve_routes(models)

    results = []

    def one(job: dict, model: str) -> dict:
        group = job["group"]
        started = time.monotonic()
        route = routes[model]
        sent = frames_for(model, job["frames"])
        out = vlm.grade_sequence(
            model=model,
            frame_paths=sent,
            criteria=[p["criterion"] for p in job["points"]],
            subject=group["label"],
            key="vertex" if route == "vertex" else None,
            post=vertex_transport.post if route == "vertex" else vlm._post,
        )
        row = {
            "gid": group["gid"], "label": group["label"], "model": model,
            "video": group["video"], "t0": job["t0"], "t1": job["t1"],
            # What was SENT, which the cap can make thinner than the span. The
            # drop is recorded on the row because a verdict must be readable
            # against the evidence that produced it, not the evidence the
            # screen happens to draw.
            "frames": [p.name for p in sent],
            "frame_count": len(sent),
            "span_frames": len(job["frames"]),
            "dropped": len(job["frames"]) - len(sent),
            "route": route,
            "latency_s": out.get("latency_s") or round(time.monotonic() - started, 2),
            "cost_usd": out.get("cost_usd"),
            "prompt_tokens": out.get("prompt_tokens"),
            "completion_tokens": out.get("completion_tokens"),
            "observed": out.get("observed"),
            "error": out.get("error"),
            "message": out.get("message"),
            "raw_text": out.get("raw_text"),
            "points": [],
        }
        graded = {g["index"]: g for g in (out.get("criteria") or [])}
        for i, point in enumerate(job["points"], 1):
            g = graded.get(i) or {}
            row["points"].append({
                "target_id": point["target_id"],
                "index": i,
                # None, not "unsure": a call that errored asked nobody anything,
                # and an ungraded point must never read as a model abstaining.
                "verdict": g.get("verdict"),
                "at": g.get("at"),
                "note": g.get("note"),
            })
        return row

    pairs = [(j, m) for j in jobs for m in models]
    # A hosted arm is rate-limited; an on-device one is a single process holding a
    # single checkpoint on this machine's GPU. Sending it four sequences at once
    # wins no throughput — they queue on the same device either way — and costs
    # four slots' worth of KV cache, which for a video span is what takes the
    # server out. So a run touching a local arm goes one call at a time.
    if any(routes[m] == "local" for m in models):
        workers = 1
    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        for row in pool.map(lambda pair: one(*pair), pairs):
            results.append(row)
            mark = "!" if row.get("error") else "."
            print(mark, end="", flush=True)
    print()

    spent = sum(r["cost_usd"] or 0 for r in results)
    errors = [r for r in results if r.get("error")]
    verdicts = [p["verdict"] for r in results for p in r["points"]]
    tally = {v: verdicts.count(v) for v in ("pass", "fail", "unsure")}
    tally["ungraded"] = sum(1 for v in verdicts if v not in ("pass", "fail", "unsure"))

    out = {
        "schema_version": SCHEMA_VERSION,
        "run_id": f"vrun_{int(time.time())}_{code}",
        "task_code": code,
        "generator": "packs/video_eval.py",
        "models": models,
        "sample_fps": SAMPLE_FPS,
        "system_prompt": vlm.SEQUENCE_PROMPT,
        "photo_run_id": run.get("run_id"),
        "results": results,
        "summary": {
            "subtasks": len(jobs), "points": total_points, "calls": len(results),
            "errors": len(errors), "cost_usd": round(spent, 4), "tally": tally,
        },
    }

    directory = VIDEO_EVAL / code
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{out['run_id']}.json"
    path.write_text(json.dumps(out, indent=1))
    print(f"  → {path.relative_to(ROOT)}  "
          f"pass {tally['pass']} · fail {tally['fail']} · unsure {tally['unsure']}"
          + (f" · ungraded {tally['ungraded']}" if tally["ungraded"] else "")
          + (f" · {len(errors)} call errors" if errors else "")
          + f" · ${spent:.2f}")
    return out


def tasks_with_runs() -> list[str]:
    if not PHOTO_EVAL.is_dir():
        return []
    return sorted(p.name for p in PHOTO_EVAL.iterdir()
                  if p.is_dir() and any(p.glob("run_*.json")))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--task", action="append", help="ACS code; repeatable")
    parser.add_argument("--all", action="store_true", help="every task with a photo run")
    parser.add_argument("--models", help="comma-separated model ids (default: all four)")
    parser.add_argument("--estimate", action="store_true",
                        help="print the cost and call nothing")
    parser.add_argument("--workers", type=int, default=4)
    args = parser.parse_args()

    codes = args.task or (tasks_with_runs() if args.all else None)
    if not codes:
        parser.error("pass --task <ACS> or --all")

    models = [m.strip() for m in args.models.split(",")] if args.models else MODEL_IDS
    unknown = [m for m in models if m not in {x["id"] for x in vlm.MODELS}]
    if unknown:
        parser.error(f"unknown model(s): {', '.join(unknown)}")

    # Each arm needs a route it can actually reach. Checked up front rather than
    # per call, so a run does not spend on two models and then fail on the third.
    routes = None
    if not args.estimate:
        # Credentials being configured is not the same as their still working, and
        # the difference is 60 identical failures with the run already underway.
        # Each route is asked about the failure it actually has: Vertex about a
        # token, OpenRouter about a balance, a local arm about its server.
        routes, why = resolve_routes(models)
        if why and any(vertex_transport.supports(m) and not vlm.is_local(m)
                       for m in models):
            print(f"Vertex not reachable: {why}")

        down = [m for m, r in routes.items() if r == "local" and not local_up(m)]
        if down:
            print("No server for: " + ", ".join(down))
            for model in down:
                print(f"  {model} → {vlm.MODELS_BY_ID[model]['endpoint']} is not answering")
            print("  Start one per arm, each on its own port: "
                  "scripts/serve_local_vlm.sh {lfm-vl|lfm-vl-q4|lfm-vl-small}")
            return 1

        needs_or = [m for m, r in routes.items() if r == "openrouter"]
        balance = openrouter_balance() if needs_or else None
        if needs_or and balance is not None and balance <= 0:
            print("No route for: " + ", ".join(needs_or))
            print(f"  OpenRouter has ${balance:.2f} left — a key is not a route."
                  if vlm.load_api_key() else
                  "  OPENROUTER_API_KEY is not set (environment or alcor_agents/.env).")
            print("  Vertex needs GOOGLE_CLOUD_PROJECT and GOOGLE_APPLICATION_CREDENTIALS, "
                  "in the environment or alcor_agents/.env.")
            print("  Vertex serves: " + ", ".join(sorted(vertex_transport.SUPPORTED)))
            print("  The on-device arms need neither, only a server: "
                  + ", ".join(m["id"] for m in vlm.MODELS if m.get("local")))
            print("  Re-run with --models naming only the arms that have a route.")
            return 1
        if needs_or and balance is None:
            print("  could not read the OpenRouter balance — proceeding on the key alone")
        for model in models:
            print(f"  {model} → {routes[model]}")

    print(f"{'estimating' if args.estimate else 'grading'} "
          f"{len(codes)} task(s) over {len(models)} model(s)")
    for code in codes:
        grade_task(code, models=models, dry_run=args.estimate,
                   workers=args.workers, routes=routes)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
