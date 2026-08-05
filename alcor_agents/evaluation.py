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

A step is a slice of work rather than a finished article, so a step-level target
is graded on several frames of its own span — --step-frames sets how many, with
1 reproducing the single-frame behaviour. Subtask targets are unaffected: they
are graded against the completed subtask, and moments of it in progress are not
evidence about that.

Exit status is 1 if any model returned a fail, so this can gate a check.
"""

from __future__ import annotations

import argparse
import json
import sys
import textwrap
import time
from collections import Counter
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
    """Pick the frame that best shows finished work, rather than the last one.

    For a target that already carries frames of its own span — a step, or a
    reviewed interval — the best view is *added* to them rather than replacing
    them, because the sampled frames are what establish the state the work ends
    in and a single flattering frame should not be the only evidence of it.
    """
    if target.get("frames"):
        return server.add_best_view(acs, target, model) or {
            "skipped": "span offers no choice beyond the sampled frames"}
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


def images_for(acs: str, target: dict) -> list[Path]:
    """Every frame this target is graded on, in the order the model sees them.

    A step carries several frames of its own span; everything else carries one.
    Both come back as a list so the caller has one shape to handle rather than
    branching on the kind of target it happens to be holding.
    """
    if target.get("upload_path"):
        return [Path(target["upload_path"])]
    if not target.get("video"):
        return []
    frames = target.get("frames") or ([target["frame"]] if target.get("frame") else [])
    return [server.FRAME_SETS["detail"] / acs / target["video"] / f for f in frames]


def image_for(acs: str, target: dict) -> Path | None:
    """The single frame a target is identified by — the state its work ends in."""
    images = images_for(acs, target)
    return images[-1] if images else None


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
    parser.add_argument("--negative", action="store_true",
                        help="also grade match-test controls: for each criteria point, a "
                             "criterion the work should NOT satisfy. Reference frames show "
                             "work an instructor accepted, so a positive run cannot tell a "
                             "grader from a model that passes everything; this can")
    parser.add_argument("--negative-kinds", default="inversion,substitution,foreign",
                        help="which controls to generate (comma-separated)")
    parser.add_argument("--negative-model", default="gemini",
                        help="model that writes the controls; foreign controls need none")
    parser.add_argument("--frame-model", default="gemini")
    parser.add_argument("--step-frames", type=int, default=server.STEP_FRAMES,
                        help="frames of its own span each step-level target is graded "
                             f"on (default {server.STEP_FRAMES}); 1 grades the single "
                             "frame the step ends on")
    parser.add_argument("--sample", type=int, default=14)
    parser.add_argument("--pass-threshold", type=float,
                        default=vlm.DEFAULT_PASS_THRESHOLD,
                        help="probability a condition must reach to count as satisfied "
                             f"(default {vlm.DEFAULT_PASS_THRESHOLD}); an unobservable "
                             "condition never passes at any threshold")
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
    every = server.photo_targets(acs, pack, server.clamp_frames_per_step(args.step_frames))
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
            elif target.get("best_view"):
                # Added to the span's frames rather than replacing them, so say
                # what the grader will actually be shown.
                note = (f"{len(target['frames'])} frames, best view "
                        f"{target['best_view']}")
            elif outcome.get("frame"):
                note = outcome["frame"]
            elif target.get("frames"):
                note = f"{len(target['frames'])} frames, no better view found"
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

    images = sum(len(images_for(acs, t)) for t in runnable)
    # One call per criteria point, as the inspector's run does. A criterion sent
    # whole comes back as one verdict and `apply_thresholds` fails it on one
    # failed condition and abstains on one unobservable one, so a four-condition
    # step could only pass when all four cleared. Splitting here is what makes
    # this CLI agree with the tab; without it the two grade the same criterion
    # differently and their runs cannot be compared.
    #
    # Holistic mode is exempt: it grades the assembly as a whole against a
    # weighted rubric on purpose, and splitting it would defeat the mode.
    control_spend = 0.0
    if args.negative:
        kinds = tuple(k.strip() for k in args.negative_kinds.split(",") if k.strip())
        writer = resolve_models(args.negative_model)[0]
        print(f"\nWriting match-test controls ({writer.split('/')[-1]}), "
              f"kinds: {', '.join(kinds)}:")

    items = []
    for target in runnable:
        points = [] if args.mode == "holistic" else server.sheet_checks(target["criterion"])
        paths = [str(p) for p in images_for(acs, target)]
        base = {"paths": paths, "label": target["label"],
                "sequence": len(paths) > 1 and not target.get("upload_path"),
                "best_view": target.get("best_view"),
                "frame": target.get("frame"),
                "frames": [Path(p).name for p in paths],
                "polarity": "original",
                "work_in_progress": bool(target.get("work_in_progress"))}
        if points:
            for point in points:
                items.append({**base, "target_id": f"{target['target_id']}::{point['id']}",
                              "criterion": point["statement"],
                              "rolls_up_to": target["target_id"],
                              "check_id": point["id"], "expected": None,
                              "negative_kind": None, "negative_of": None})
        else:
            items.append({**base, "target_id": target["target_id"],
                          "criterion": target["criterion"],
                          "rolls_up_to": None, "check_id": None, "expected": None,
                          "negative_kind": None, "negative_of": None})

        if not args.negative:
            continue
        # The same subtask against a criterion that does not describe it, on the
        # very same frames. Drafted as a whole sheet and split by the same
        # parser, so its points are the same measurement as the criteria's and
        # the two pass rates can be subtracted — which is the only reason to
        # spend the calls. Graded as their own roll-up rather than folded into
        # the subtask: the subtask's verdict is about the work, and this one is
        # about the grader.
        drafted = server.negative_sheet(acs, target, target["criterion"], writer)
        control_spend += drafted.get("cost_usd") or 0
        if drafted.get("error"):
            print(f"  {target['target_id']:<34} none — {drafted['error']}")
            continue
        parent = f"{target['target_id']}{server.NEGATIVE_SUFFIX}"
        paired = {p["id"]: p for p in drafted["points"]}
        control_points = server.sheet_checks(drafted["criterion"])
        for point in control_points:
            items.append({**base,
                          "target_id": f"{parent}::{point['id']}",
                          "criterion": point["statement"],
                          "label": f"{target['label']} — negated · {point['id']}",
                          "rolls_up_to": parent, "check_id": point["id"],
                          "expected": "fail", "polarity": "negative",
                          "negative_kind": (paired.get(point["id"]) or {}).get("kind"),
                          "negative_of": (paired.get(point["id"]) or {}).get("of")})
        by_kind = Counter(p.get("kind") for p in drafted["points"])
        # Lines the drafter declined are named rather than absorbed: a negative
        # sheet shorter than the criterion it mirrors is a weaker control.
        left_out = len(drafted.get("skipped") or [])
        note = f"  ({left_out} not negated)" if left_out else ""
        print(f"  {target['target_id']:<34} {len(control_points)} point(s)  "
              f"{dict(by_kind) if control_points else 'none writable'}{note}")

    estimate = vlm.estimate_cost(models, len(items), images / len(runnable))
    print(f"\n{len(runnable)} target(s) → {len(items)} point(s) × {len(models)} model(s) "
          f"= {len(items) * len(models)} call(s), {images / len(runnable):.1f} image(s) each, "
          f"estimated {estimate['total_usd']:.4f} USD")
    if args.dry_run:
        for target in runnable:
            names = ", ".join(p.name for p in images_for(acs, target))
            points = sum(1 for i in items if (i["rolls_up_to"] or i["target_id"])
                         == target["target_id"])
            print(f"  {target['target_id']:<34} {points} point(s)  {names}")
        return 0

    jobs = [{"model": model,
             "image_paths": item["paths"],
             # Frames of one step at successive moments read differently from
             # photographs of separate subjects, and the grader is told which.
             "sequence": item["sequence"],
             "best_view": item["best_view"],
             "criterion": item["criterion"],
             "context": title,
             "mode": args.mode,
             "cell": {k: item[k] for k in
                      ("target_id", "label", "frame", "frames", "best_view",
                       "criterion", "rolls_up_to", "check_id", "work_in_progress",
                       "expected", "polarity", "negative_kind", "negative_of")}}
            for item in items for model in models]

    started = time.monotonic()
    results = vlm.grade_many(jobs, pass_at=args.pass_threshold)
    elapsed = time.monotonic() - started

    # Per point, then per target. A point knows its parent, so the roll-up does
    # not rest on picking `<target>::c1` apart.
    # Controls answer a different question from the rest of the run, so they are
    # reported apart from it rather than mixed into a step's verdict.
    controls = [r for r in results if r.get("expected")]
    graded = [r for r in results if not r.get("expected")]

    cells: dict[str, dict] = {}
    for result in graded:
        parent = result.get("rolls_up_to") or result["target_id"]
        cells.setdefault(parent, {}).setdefault(result["model"], []).append(result)

    def roll_up(points: list[dict]) -> str:
        """One fail fails the step; a point the photo could not settle is review.

        The same rule `handle_photo_run` applies, so the CLI and the tab cannot
        report different verdicts for the same replies.
        """
        verdicts = [p.get("verdict") for p in points if not p.get("error")]
        if not verdicts:
            return "error"
        if "fail" in verdicts:
            return "fail"
        return "review" if any(v != "pass" for v in verdicts) else "pass"

    failures = 0
    totals: dict[str, Counter] = {m: Counter() for m in models}
    for target in runnable:
        row = cells.get(target["target_id"], {})
        print(f"\n== {target['label'][:88]}")
        flag = "  [IN PROGRESS — not a photo of finished work]" if target.get(
            "work_in_progress") else ""
        shown = images_for(acs, target)
        names = shown[-1].name if len(shown) == 1 else (
            f"{len(shown)} frames: " + ", ".join(p.name for p in shown))
        print(f"   {names}{flag}")
        for model in models:
            points = sorted(row.get(model) or [], key=lambda r: r.get("check_id") or "")
            name = model.split("/")[-1]
            errored = [p for p in points if p.get("error")]
            if errored and len(errored) == len(points):
                print(f"   {name:<22} ERROR {errored[0]['error']}: "
                      f"{str(errored[0].get('message'))[:56]}")
                continue
            verdict = roll_up(points)
            failures += verdict == "fail"
            totals[model].update(p.get("verdict") for p in points if not p.get("error"))
            if args.mode == "holistic":
                first = points[0] if points else {}
                detail = f"score {first.get('score')}/100"
                if first.get("critical_defects"):
                    detail += f" · {len(first['critical_defects'])} critical defect(s)"
            else:
                counted = Counter(p.get("verdict") for p in points if not p.get("error"))
                detail = (f"{counted['pass']} pass / {counted['fail']} fail / "
                          f"{counted['unsure']} unsure  of {len(points)} point(s)")
            print(f"   {name:<22} {MARK.get(verdict, verdict.upper()[:4])}  {detail}")
            # Only where a point is worth reading: a failure names itself, which
            # is the whole reason for grading a point at a time.
            for point in points:
                if point.get("error") or point.get("verdict") == "pass":
                    continue
                mark = MARK.get(point.get("verdict"), "?")
                print(f"     {point.get('check_id') or '-':<4} {mark} "
                      f"{str(point.get('criterion'))[:76]}")
                if point.get("missing_evidence"):
                    print(wrap(point["missing_evidence"], indent="          "))

        verdicts = {roll_up(p) for p in row.values() if p}
        if len(verdicts) > 1:
            print("   models disagree: " + ", ".join(sorted(verdicts)))

    spent = sum(r.get("cost_usd") or 0 for r in results) + frame_spend + control_spend
    errors = sum(1 for r in results if r.get("error"))
    print(f"\n{'-' * 78}\nevery point, per model:")
    for model in models:
        counted = totals[model]
        n = sum(counted.values())
        if not n:
            continue
        print(f"   {model.split('/')[-1]:<22} {counted['pass']:>4} pass  "
              f"{counted['fail']:>4} fail  {counted['unsure']:>4} unsure   "
              f"of {n} ({counted['pass'] / n:.0%} pass)")

    negatives = [r for r in results if r.get("polarity") == "negative"]
    if negatives:
        # The question a positive-only run cannot answer. Every frame here is
        # work an instructor accepted, so the criteria's own pass rate is the
        # same number a model that passes everything would produce; the negated
        # sheets are the same photographs and the same points against criteria
        # the work does not satisfy, and the distance between the two rates is
        # the part of the result that is about grading.
        print(f"\n{'-' * 78}\ncriteria vs their negations, per model:")
        for model in models:
            def rate(rows: list[dict]) -> tuple[int, int, float | None]:
                graded_rows = [r for r in rows if not r.get("error")]
                passed = sum(1 for r in graded_rows if r.get("verdict") == "pass")
                return passed, len(graded_rows), (
                    passed / len(graded_rows) if graded_rows else None)

            mine = [r for r in results if r["model"] == model]
            up, un, ur = rate([r for r in mine if r.get("polarity") != "negative"])
            np_, nn, nr = rate([r for r in mine if r.get("polarity") == "negative"])
            if not un or not nn:
                continue
            print(f"   {model.split('/')[-1]:<22} criteria {ur:.0%} ({up}/{un})   "
                  f"negated {nr:.0%} ({np_}/{nn})   drop {(ur - nr) * 100:.0f} pts")
        print("   a negated point expects `fail`; `unsure` is a miss too, but a "
              "`pass`\n   is a grader accepting a description of work that is not "
              "in the photograph.")

    if controls:
        # The positive verdict for the same condition, so a control can be read
        # against what the grader said when the criterion was true.
        positive = {(r["model"], r.get("check_id"), (r.get("target_id") or "").split("::")[0]):
                    r.get("verdict") for r in graded}
        print(f"\n{'-' * 78}\nmatch test — criteria the work should NOT satisfy:")
        print("   accepted = passed a criterion the work violates. No observability "
              "excuse\n   applies to it, so it is the one number here that is "
              "unambiguous.")
        for model in models:
            mine = [c for c in controls if c["model"] == model and not c.get("error")]
            if not mine:
                continue
            hits = [c for c in mine if vlm.expectation_met(c["expected"], c.get("verdict"))]
            accepted = [c for c in mine if c.get("verdict") == "pass"]
            # A control whose positive counterpart the grader could not settle
            # even when it was TRUE may be answered `unsure` because the feature
            # is not visible, not because the model failed to read. Those are
            # reported apart rather than dropped: in practice a gross violation
            # is often visible where subtle conformance is not, so a model can
            # and does answer `fail` here — treating them all as unscoreable
            # would throw that away.
            paired = [c for c in mine if c.get("negative_of")]
            decisive = [c for c in paired
                        if positive.get((model, c["negative_of"],
                                         (c.get("target_id") or "").split("#")[0]))
                        in ("pass", "fail")]
            d_hits = [c for c in decisive
                      if vlm.expectation_met(c["expected"], c.get("verdict"))]
            line = (f"   {model.split('/')[-1]:<22} "
                    f"{len(accepted)} accepted   {len(hits)}/{len(mine)} correct "
                    f"({len(hits) / len(mine):.0%})")
            if decisive:
                line += (f"   {len(d_hits)}/{len(decisive)} "
                         f"({len(d_hits) / len(decisive):.0%}) where its positive was decisive")
            print(line)
            for c in accepted:
                print(f"     ACCEPTED  {str(c.get('criterion'))[:66]}")
                if c.get("negative_of"):
                    base = (c.get("target_id") or "").split("#")[0]
                    print(f"               its positive ({c['negative_of']}) came back "
                          f"{positive.get((model, c['negative_of'], base))}")
        by_kind: dict[str, Counter] = {}
        for c in controls:
            if c.get("error"):
                continue
            k = c.get("negative_kind") or "?"
            counter = by_kind.setdefault(k, Counter())
            counter["hit" if vlm.expectation_met(c["expected"], c.get("verdict")) else "miss"] += 1
            counter["accepted"] += c.get("verdict") == "pass"
        print("   by kind, all models:  " + "   ".join(
            f"{k} {v['hit']}/{v['hit'] + v['miss']} correct, {v['accepted']} accepted"
            for k, v in sorted(by_kind.items())))

    # Coverage, stated at the end where a truncated log still shows it. A run
    # that graded two thirds of a task's steps and said so only in a line above
    # the results reads, from the summary, exactly like one that graded all of
    # them — which is how 58 of 157 pack steps went unnoticed across a sweep.
    pack_steps = [s["id"] for s in (pack or {}).get("steps") or [] if s.get("id")]
    if pack_steps:
        ran = {t.get("step_id") for t in runnable}
        covered = [s for s in pack_steps if s in ran]
        absent = [s for s in pack_steps if s not in ran]
        note = f"{len(covered)}/{len(pack_steps)} pack steps graded"
        if absent:
            # Say which mechanism dropped each one; "missing" without a reason
            # invites the assumption that the task simply has fewer steps.
            elsewhere = {t.get("step_id") for t in every
                         if t["kind"] != args.kind and t.get("step_id")}
            by_reason = {"covered by a reviewed interval (run --kind all)": [],
                         "no photo": [], "no criterion": [], "not built": []}
            for step_id in absent:
                target = next((t for t in every if t.get("step_id") == step_id
                               and t["kind"] == args.kind), None)
                if target is None:
                    key = ("covered by a reviewed interval (run --kind all)"
                           if step_id in elsewhere else "not built")
                elif not target.get("frame_exists"):
                    key = "no photo"
                elif not (target.get("criterion") or "").strip():
                    key = "no criterion"
                else:
                    key = "not built"
                by_reason[key].append(step_id)
            note += " — " + "; ".join(
                f"{len(v)} {k}" for k, v in by_reason.items() if v)
        print(f"\ncoverage: {note}")

    print(f"\n{len(results)} calls · {errors} errors · {spent:.4f} USD · {elapsed:.0f}s")

    run = {"schema_version": 1, "run_id": f"eval_{int(time.time())}_{acs}",
           "task_code": acs, "models": models, "mode": args.mode,
           "thresholds": {"pass": args.pass_threshold, "fail": vlm.DEFAULT_FAIL_THRESHOLD},
           "frames_per_step": server.clamp_frames_per_step(args.step_frames),
           "criteria_supplied": bool(criteria), "results": results}
    destination = args.json or (server.PHOTO_DIR / acs / f"{run['run_id']}.json")
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(run, indent=2) + "\n")
    print(f"run written to {destination}")

    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
