"""Command line for the erroneous-video pipeline.

    python -m erroneous_video_pipeline discover
    python -m erroneous_video_pipeline plan --task-code AM.I.D.S1
    python -m erroneous_video_pipeline generate --video data/videos/AM.I.D.S1/bend_the_line.mp4 \
            --error wrong_bend_angle --execute
    python -m erroneous_video_pipeline generate-all --dry-run
    python -m erroneous_video_pipeline qa --video <generated.mp4> --plan <error_plan.json>
    python -m erroneous_video_pipeline report

Spending is opt-in twice over. Commands that could submit a job run as a dry run
unless `--execute` is passed, and `--max-cost` caps the whole run. That is
deliberate: `POST /videos` has no cancel endpoint, so a job submitted by accident
runs to completion and bills in full.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .config import ROOT, Settings, load_api_key
from . import analysis as analysis_mod
from . import discovery, media, models, outputs, pipeline, planning
from .openrouter import Client, OpenRouterError


def _client(settings: Settings) -> Client:
    key = load_api_key()
    if not key:
        raise SystemExit(
            "No OPENROUTER_API_KEY found in the environment or .env — see .env.example")
    return Client(key, settings)


def _settings(args: argparse.Namespace) -> Settings:
    settings = Settings.from_env()
    if getattr(args, "video_model", None):
        settings.video_model = args.video_model
    if getattr(args, "analysis_model", None):
        settings.vlm_model = args.analysis_model
    if getattr(args, "max_cost", None) is not None:
        settings.max_cost = args.max_cost
    if getattr(args, "no_video_reference", False):
        settings.allow_video_reference = False
    elif getattr(args, "video_reference", False):
        settings.allow_video_reference = True
    if getattr(args, "confirm", False):
        settings.require_confirmation = True
    settings.dry_run = not getattr(args, "execute", False)
    return settings


def _records(args: argparse.Namespace) -> list[discovery.VideoRecord]:
    records = discovery.discover(task_code=getattr(args, "task_code", None),
                                 subtask=getattr(args, "subtask", None))
    if getattr(args, "video", None):
        name = Path(args.video).name
        records = [r for r in records if Path(r.video_path).name == name]
    records = [r for r in records if r.resolved]
    if getattr(args, "limit", None):
        records = records[: args.limit]
    return records


# ----------------------------------------------------------------- commands


def cmd_discover(args: argparse.Namespace) -> int:
    records = discovery.discover(task_code=args.task_code, subtask=args.subtask)
    if args.json:
        print(json.dumps([r.as_dict() for r in records], indent=2))
        return 0

    resolved = [r for r in records if r.resolved]
    print(f"{len(records)} source video(s) under data/videos; "
          f"{len(resolved)} bound to a subtask and its criteria.\n")
    current = None
    for record in records:
        if record.task_code != current:
            current = record.task_code
            print(f"{current}  {record.title or ''}")
        mark = "OK " if record.resolved else "   "
        name = Path(record.video_path).name
        if record.resolved:
            detail = f"-> {record.subtask_id}  ({record.match_basis})"
        elif record.suggested_subtask:
            detail = f"?  suggest --subtask {record.suggested_subtask}"
        else:
            detail = f"?  choose one of: {', '.join(record.candidate_subtasks) or 'none drafted'}"
        print(f"  {mark}{name:56} {detail}")
    if len(resolved) < len(records):
        print("\nClips marked '?' are not bound to a rubric. Their names do not "
              "identify a subtask, so pass --subtask rather than let the pipeline "
              "guess and attach an error to the wrong criteria.")
    return 0


def cmd_plan(args: argparse.Namespace) -> int:
    settings = _settings(args)
    settings.dry_run = True
    client = _client(settings)
    records = _records(args)
    if not records:
        print("No videos matched.", file=sys.stderr)
        return 1
    root = outputs.output_root(args.out)
    budget = settings.budget()

    for record in records:
        print(f"\n{record.task_code}/{record.subtask_id}  {Path(record.video_path).name}")
        try:
            result = pipeline.run_variant(record, client, settings, budget,
                                          error_id=args.error, root=root,
                                          log=lambda m: print(m))
            plan = result["plan"]
            print(f"  error      : {plan['error_id']}")
            print(f"  defect     : {plan['required_error']}")
            print(f"  violates   : {'; '.join(plan['violated_criteria']) or '(unmatched)'}")
            print(f"  plan       : {planning.variant_dir(plan, root) / 'error_plan.json'}")
        except Exception as exc:  # noqa: BLE001 - one bad clip must not stop the sweep
            print(f"  FAILED: {exc}", file=sys.stderr)
    return 0


def cmd_generate(args: argparse.Namespace) -> int:
    settings = _settings(args)
    client = _client(settings)
    records = _records(args)
    if not records:
        print("No videos matched.", file=sys.stderr)
        return 1
    root = outputs.output_root(args.out)
    budget = settings.budget()

    if not settings.dry_run:
        cap = f"${settings.max_cost:.2f}" if settings.max_cost else "NO CAP SET"
        print(f"Executing for real. Spend cap: {cap}. "
              f"Jobs cannot be cancelled once submitted.\n")

    failures = 0
    for record in records:
        print(f"{record.task_code}/{record.subtask_id}  {Path(record.video_path).name}")
        try:
            result = pipeline.run_variant(
                record, client, settings, budget, error_id=args.error,
                seed=args.seed, root=root, resume=args.resume,
                max_attempts=args.max_retries, log=lambda m: print(m))
            if result["status"] not in {"accepted", "dry_run", "skipped"}:
                failures += 1
        except Exception as exc:  # noqa: BLE001
            print(f"  FAILED: {exc}", file=sys.stderr)
            failures += 1

    if not settings.dry_run:
        outputs.write_summary(root)
        print(f"\nSpent ${budget.spent:.2f}. Summary: {root / outputs.SUMMARY}")
    return 1 if failures else 0


def cmd_qa(args: argparse.Namespace) -> int:
    from . import qa as qa_mod
    settings = _settings(args)
    settings.dry_run = False
    client = _client(settings)
    plan = json.loads(Path(args.plan).read_text())
    record = discovery.find_video(plan["source_video"])
    record.subtask_id = plan["subtask_id"]
    criteria = discovery.subtasks_for(plan["task_code"]).get(plan["subtask_id"])
    if criteria:
        record.criteria_path = str(criteria.relative_to(ROOT))
    verdict = qa_mod.evaluate(record, plan, Path(args.video), client, settings)
    print(json.dumps(verdict, indent=2))
    return 0 if verdict.get("accepted") else 1


def cmd_models(args: argparse.Namespace) -> int:
    """Show what /videos/models offers for a given source, and why."""
    settings = _settings(args)
    client = _client(settings)
    rows = client.video_models()
    record = discovery.find_video(args.video) if args.video else None
    if record is None:
        for row in rows:
            print(f"{row.get('id'):34} ar={','.join(row.get('supported_aspect_ratios') or []) or '-'}")
        return 0
    info = media.probe(ROOT / record.video_path)
    print(f"{Path(record.video_path).name}: {info.width}x{info.height} "
          f"({media.nearest_aspect_ratio(info.width, info.height)[0]}), "
          f"{info.duration_s:.1f}s, hdr={info.is_hdr}\n")
    for line in models.describe_rejections(rows, info, args.window,
                                           settings.allow_video_reference):
        print("  " + line)
    try:
        selection = models.select_model(rows, info, args.window,
                                        allow_video_reference=settings.allow_video_reference,
                                        requested=settings.video_model)
        print("\nselected:")
        print(json.dumps(selection.as_dict(), indent=2))
    except models.NoSuitableModel as exc:
        print(f"\nno usable model: {exc}")
        return 1
    return 0


def cmd_report(args: argparse.Namespace) -> int:
    root = outputs.output_root(args.out)
    path = outputs.write_summary(root)
    accepted = outputs.read_jsonl(root / outputs.MANIFEST)
    failed = outputs.read_jsonl(root / outputs.FAILURES)
    spent = sum(float(r.get("cost") or 0) for r in accepted + failed)
    print(f"accepted : {len(accepted)}")
    print(f"rejected : {len(failed)}")
    print(f"spend    : ${spent:.2f}")
    print(f"summary  : {path}")
    return 0


def cmd_credits(args: argparse.Namespace) -> int:
    settings = _settings(args)
    data = _client(settings).credits()
    print(f"limit     : {data.get('limit')}")
    print(f"used      : {data.get('usage')}")
    print(f"remaining : {data.get('limit_remaining')}")
    return 0


# -------------------------------------------------------------------- parse


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m erroneous_video_pipeline",
        description="Generate controlled erroneous versions of maintenance training videos.")
    sub = parser.add_subparsers(dest="command", required=True)

    def common(p, *, spending: bool = False):
        p.add_argument("--task-code")
        p.add_argument("--subtask")
        p.add_argument("--error")
        p.add_argument("--video")
        p.add_argument("--limit", type=int)
        p.add_argument("--out", help="output root (default generated_errors/)")
        p.add_argument("--analysis-model")
        p.add_argument("--video-model")
        if spending:
            p.add_argument("--execute", action="store_true",
                           help="actually submit paid jobs (default is a dry run)")
            p.add_argument("--max-cost", type=float,
                           help="hard cap in USD for this run")
            p.add_argument("--max-retries", type=int)
            p.add_argument("--seed", type=int)
            p.add_argument("--resume", action="store_true",
                           help="skip variants already accepted in the manifest")
            p.add_argument("--confirm", action="store_true",
                           help="ask before each submission")
            p.add_argument("--video-reference", action="store_true",
                           help="allow video-to-video, which publishes the edit-window "
                                "clip to a temporary public HTTPS URL")
            p.add_argument("--no-video-reference", action="store_true")
        return p

    d = sub.add_parser("discover", help="list source videos and their bindings")
    d.add_argument("--task-code")
    d.add_argument("--subtask")
    d.add_argument("--json", action="store_true")
    d.set_defaults(func=cmd_discover)

    p = common(sub.add_parser("plan", help="write error plans without spending"))
    p.set_defaults(func=cmd_plan)

    g = common(sub.add_parser("generate", help="generate one or more variants"), spending=True)
    g.set_defaults(func=cmd_generate)

    a = common(sub.add_parser("generate-all", help="sweep every resolved video"), spending=True)
    a.set_defaults(func=cmd_generate)

    q = sub.add_parser("qa", help="re-run QA on an existing generated file")
    q.add_argument("--video", required=True)
    q.add_argument("--plan", required=True)
    q.add_argument("--analysis-model")
    q.set_defaults(func=cmd_qa)

    m = sub.add_parser("models", help="show model eligibility for a source video")
    m.add_argument("--video")
    m.add_argument("--window", type=float, default=8.0)
    m.add_argument("--video-model")
    m.add_argument("--video-reference", action="store_true")
    m.set_defaults(func=cmd_models)

    r = sub.add_parser("report", help="rebuild generation_summary.md")
    r.add_argument("--out")
    r.set_defaults(func=cmd_report)

    c = sub.add_parser("credits", help="show remaining OpenRouter credit")
    c.set_defaults(func=cmd_credits)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except OpenRouterError as exc:
        print(f"OpenRouter error: {exc}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("\ninterrupted", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
