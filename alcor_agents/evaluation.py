#!/usr/bin/env python3
"""Photo-assessment evaluation for one task, across several models.

Three inputs: a task code, the criteria to grade against, and the models to run.

    # compiled criteria, default models
    python evaluation.py AM.I.D.S1

    # your own rubric, three vendors side by side
    python evaluation.py AM.I.D.S1 --criteria-file rubric.txt --models claude,gemini,sol

    # one subtask, graded as a whole article against an inline rubric
    python evaluation.py AM.I.D.S1 --target section:flare-the-tube --mode holistic \
        --criteria "Assess the completed flare as one finished assembly. ..."

    # grade a photo you took rather than a video frame
    python evaluation.py AM.I.D.S1 --target section:flare-the-tube --photo flare.jpg

Without --criteria the compiled criterion for each target is used, which is
drafted from the AIM procedure sheet and the governing FAA handbook together.

Photos come from the reference clips. Because filming usually stops with the
tool still on the workpiece, the best frame is chosen by asking a model which
one actually shows finished work — pass --last-frame to skip that and take the
final frame instead, or --photo to supply the real artifact.

Exit status is 1 if any model returned a fail, so this can gate a check.
"""

from __future__ import annotations

import argparse
import json
import sys
import textwrap
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "inspector"))

from inspector import server, vlm  # noqa: E402

# Vendor shorthand, so a run reads as `--models claude,gemini,sol`.
MODELS = {
    "claude": "anthropic/claude-opus-5",
    "opus": "anthropic/claude-opus-5",
    "gemini": "google/gemini-3.6-flash",
    "flash": "google/gemini-3.6-flash",
    "pro": "google/gemini-3.1-pro-preview",
    "gpt": "openai/gpt-5.6-sol",
    "sol": "openai/gpt-5.6-sol",
}
DEFAULT_MODELS = "claude,gemini,sol"
MARK = {"pass": "PASS", "fail": "FAIL", "unsure": "----"}


def resolve_models(spec: str) -> list[str]:
    if spec.strip().lower() == "all":
        return list(dict.fromkeys(MODELS.values()))
    out = []
    for name in spec.split(","):
        name = name.strip()
        if not name:
            continue
        model = MODELS.get(name.lower(), name)
        if model not in vlm.MODELS_BY_ID:
            raise SystemExit(
                f"Unknown model {name!r}.\n"
                f"  short names: {', '.join(sorted(MODELS))}, all\n"
                f"  full ids:    {', '.join(vlm.MODELS_BY_ID)}")
        if model not in out:
            out.append(model)
    return out


def read_criteria(args: argparse.Namespace) -> str | None:
    if args.criteria_file:
        if not args.criteria_file.is_file():
            raise SystemExit(f"No such criteria file: {args.criteria_file}")
        return args.criteria_file.read_text().strip()
    return args.criteria.strip() if args.criteria else None


def attach_photo(acs: str, target_id: str, photo: Path) -> None:
    """Pin an operator-supplied photo to a target, as the portal's upload does."""
    destination = server.PHOTO_DIR / acs / "uploads" / photo.name
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(photo.read_bytes())
    store = server.read_criteria_store(acs)
    entry = store.get(target_id) or {"criterion": None, "variants": []}
    entry["upload"] = photo.name
    store[target_id] = entry
    server.write_criteria_store(acs, store)


def choose_frame(acs: str, target: dict, model: str, sample: int,
                 grade_anyway: bool = False) -> dict:
    """Pick the frame that best shows finished work, rather than the last one."""
    clip = target.get("video")
    if not clip:
        return {"skipped": "no clip for this target"}
    names = server.frame_names(acs, clip, "detail")
    if not names:
        return {"skipped": f"no extracted frames for {clip}"}
    step = max(1, len(names) // max(2, sample))
    sampled = names[::step][:sample]
    result = vlm.pick_best_frame(
        model=model,
        image_paths=[server.FRAME_SETS["detail"] / acs / clip / n for n in sampled],
        description=target.get("criterion") or target.get("label") or "")
    if result.get("frame"):
        target["frame"] = result["frame"]
        target["frame_exists"] = True
    elif not result.get("error"):
        # No frame shows the completed article. Grading the last one anyway
        # returns a verdict about work that was still in progress, which reads
        # as bad workmanship rather than as missing evidence — so it is opt-in,
        # and the verdict is labelled wherever it is shown.
        if grade_anyway:
            names_last = server.frame_names(acs, clip, "detail")
            target["frame"] = names_last[-1] if names_last else None
            target["frame_exists"] = bool(target["frame"])
            target["work_in_progress"] = True
        else:
            target["frame_exists"] = False
    return result


def image_for(acs: str, target: dict) -> Path | None:
    if target.get("upload_path"):
        return Path(target["upload_path"])
    if target.get("video") and target.get("frame"):
        return server.FRAME_SETS["detail"] / acs / target["video"] / target["frame"]
    return None


def wrap(text, indent="      ", width=94) -> str:
    return textwrap.fill(str(text or ""), width,
                         initial_indent=indent, subsequent_indent=indent)


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("task_code", nargs="?", default="AM.I.D.S1")
    parser.add_argument("--criteria", help="criteria text applied to every target")
    parser.add_argument("--criteria-file", type=Path)
    parser.add_argument("--models", default=DEFAULT_MODELS,
                        help=f"comma-separated; {', '.join(sorted(MODELS))}, or 'all'")
    parser.add_argument("--target", action="append", default=[],
                        help="target id; repeatable. Default: every --kind target")
    parser.add_argument("--kind", default="section",
                        choices=["section", "step", "subtask", "evidence", "task", "all"])
    parser.add_argument("--mode", default="correctness",
                        choices=["correctness", "holistic"],
                        help="per-condition verdicts, or one 0-100 score for the whole article")
    parser.add_argument("--photo", type=Path, help="grade this image (needs one --target)")
    parser.add_argument("--last-frame", action="store_true",
                        help="use each clip's final frame instead of searching for finished work")
    parser.add_argument("--grade-anyway", action="store_true",
                        help="when no finished-work frame is found, grade the best available "
                             "frame regardless and flag the verdict as in-progress work")
    parser.add_argument("--frame-model", default="gemini")
    parser.add_argument("--sample", type=int, default=14)
    parser.add_argument("--pass-threshold", type=float, default=0.9)
    parser.add_argument("--limit", type=int)
    parser.add_argument("--dry-run", action="store_true",
                        help="show the plan and cost estimate; make no API calls")
    parser.add_argument("--json", type=Path)
    args = parser.parse_args()

    acs = args.task_code
    models = resolve_models(args.models)
    criteria = read_criteria(args)

    if args.photo:
        if len(args.target) != 1:
            raise SystemExit("--photo needs exactly one --target")
        if not args.photo.is_file():
            raise SystemExit(f"No such image: {args.photo}")
        attach_photo(acs, args.target[0], args.photo)

    pack, _, pack_error = server.load_pack(acs)
    if pack_error:
        print(f"warning: {acs} pack.yaml did not parse: {pack_error}", file=sys.stderr)
    every = server.photo_targets(acs, pack)
    if not every:
        raise SystemExit(f"{acs}: no targets. Is there a pack or a segmented clip?")

    if args.target:
        wanted = set(args.target)
        targets = [t for t in every if t["target_id"] in wanted]
        missing = wanted - {t["target_id"] for t in targets}
        if missing:
            available = ", ".join(sorted(t["target_id"] for t in every)[:12])
            raise SystemExit(f"Unknown target(s): {', '.join(sorted(missing))}\n"
                             f"Available include: {available}…")
    else:
        targets = [t for t in every if args.kind == "all" or t["kind"] == args.kind]
    if args.limit:
        targets = targets[: args.limit]
    if not targets:
        kinds = ", ".join(sorted({t["kind"] for t in every}))
        raise SystemExit(f"{acs}: no targets of kind {args.kind!r}. Available: {kinds}")

    if criteria:
        for target in targets:
            target["criterion"] = criteria

    title = (pack or {}).get("title") or acs
    print(f"{acs} — {title}")
    print(f"{len(targets)} target(s) · {len(models)} model(s) · mode={args.mode} · "
          f"criteria={'supplied' if criteria else 'compiled'}")
    print("models: " + ", ".join(m.split('/')[-1] for m in models))

    online = bool(vlm.load_api_key())
    if not online and not args.dry_run:
        raise SystemExit("OPENROUTER_API_KEY is not set (env or alcor_agents/.env)")

    frame_spend = 0.0
    if not args.last_frame and not args.photo and not args.dry_run:
        finder = resolve_models(args.frame_model)[0]
        print(f"\nChoosing frames that show finished work ({finder.split('/')[-1]}):")
        for target in targets:
            if target.get("upload_path"):
                print(f"  {target['target_id']:<40} operator-supplied photo")
                continue
            outcome = choose_frame(acs, target, finder, args.sample, args.grade_anyway)
            frame_spend += outcome.get("cost_usd") or 0
            if outcome.get("skipped"):
                note = outcome["skipped"]
            elif outcome.get("error"):
                note = f"error: {outcome['error']}"
            elif outcome.get("frame"):
                note = outcome["frame"]
            elif target.get("work_in_progress"):
                note = f"{target['frame']}  (IN PROGRESS — no finished-work frame)"
            else:
                note = "no finished-work frame in this clip"
            print(f"  {target['target_id']:<40} {note}")

    runnable = [t for t in targets
                if t.get("criterion") and t.get("frame_exists") and image_for(acs, t)]
    skipped = [t for t in targets if t not in runnable]
    if skipped:
        print(f"\nskipping {len(skipped)} target(s) with no usable photo:")
        for target in skipped:
            print(f"  {target['target_id']}")

    if not runnable:
        print("\nNothing to grade. Supply a photo with --photo, or pass --last-frame.")
        return 0

    estimate = vlm.estimate_cost(models, len(runnable))
    print(f"\n{len(runnable) * len(models)} call(s), estimated {estimate['total_usd']:.4f} USD")
    if args.dry_run:
        for target in runnable:
            print(f"  {target['target_id']:<40} {image_for(acs, target).name}")
        return 0

    jobs = [{"model": model,
             "image_path": str(image_for(acs, target)),
             "criterion": target["criterion"],
             "context": title,
             "mode": args.mode,
             "cell": {"target_id": target["target_id"], "label": target["label"],
                      "frame": target.get("frame"),
                      "work_in_progress": bool(target.get("work_in_progress"))}}
            for target in runnable for model in models]

    started = time.monotonic()
    results = vlm.grade_many(jobs, pass_at=args.pass_threshold)
    elapsed = time.monotonic() - started

    cells: dict[str, dict] = {}
    for result in results:
        cells.setdefault(result["target_id"], {})[result["model"]] = result

    failures = 0
    for target in runnable:
        row = cells.get(target["target_id"], {})
        print(f"\n== {target['label'][:88]}")
        flag = "  [IN PROGRESS — not a photo of finished work]" if target.get(
            "work_in_progress") else ""
        print(f"   {image_for(acs, target).name}{flag}")
        for model in models:
            result = row.get(model) or {}
            name = model.split("/")[-1]
            if result.get("error"):
                print(f"   {name:<22} ERROR {result['error']}: "
                      f"{str(result.get('message'))[:56]}")
                continue
            verdict = result.get("verdict")
            failures += verdict == "fail"
            if args.mode == "holistic":
                detail = f"score {result.get('score')}/100"
                if result.get("critical_defects"):
                    detail += f" · {len(result['critical_defects'])} critical defect(s)"
            else:
                detail = (f"{result.get('conditions_passed', 0)} pass / "
                          f"{result.get('conditions_failed', 0)} fail / "
                          f"{result.get('conditions_blocked', 0)} blocked")
            print(f"   {name:<22} {MARK.get(verdict, '?')}  {detail}")
            if result.get("rationale"):
                print(wrap(result["rationale"]))

        verdicts = {r.get("verdict") for r in row.values() if not r.get("error")}
        if len(verdicts) > 1:
            print("   models disagree: " + ", ".join(sorted(v for v in verdicts if v)))

    spent = sum(r.get("cost_usd") or 0 for r in results) + frame_spend
    errors = sum(1 for r in results if r.get("error"))
    print(f"\n{len(results)} calls · {errors} errors · {spent:.4f} USD · {elapsed:.0f}s")

    run = {"schema_version": 1, "run_id": f"eval_{int(time.time())}_{acs}",
           "task_code": acs, "models": models, "mode": args.mode,
           "criteria_supplied": bool(criteria), "results": results}
    destination = args.json or (server.PHOTO_DIR / acs / f"{run['run_id']}.json")
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(run, indent=2) + "\n")
    print(f"run written to {destination}")

    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
