"""End-to-end orchestration for one variant: analyse, plan, generate, QA, record.

The retry loop is the part worth reading. A rejected candidate is not simply
re-rolled — the QA verdict is fed back into the next prompt through
`prompts.retry_note`, naming what changed that should not have and what defect
failed to appear. Re-rolling an unchanged prompt mostly reproduces the same
failure at full price.

Retries are capped, every rejection is written to `failed_generations.jsonl` with
its reasons, and the budget is consulted before each attempt, so a clip the
generator simply cannot render stops costing money after a bounded number of
tries instead of looping.
"""

from __future__ import annotations

from pathlib import Path

from .config import ROOT, Budget, BudgetExceeded, Settings
from . import analysis as analysis_mod
from . import generation, outputs, planning, prompts, qa
from .discovery import VideoRecord
from .openrouter import Client


def run_variant(record: VideoRecord, client: Client, settings: Settings,
                budget: Budget, *, error_id: str | None = None,
                seed: int | None = None, root: Path | None = None,
                max_attempts: int | None = None, resume: bool = False,
                log=print) -> dict:
    """Analyse, plan, generate and QA one (video, error) pair."""
    root = outputs.output_root(root)
    max_attempts = max_attempts or max(1, settings.max_retries)

    analysis = analysis_mod.analyse(record, client, settings)
    error = analysis_mod.pick_error(analysis, error_id, record.subtask_id)
    plan = planning.build_plan(record, analysis, error)

    dest = planning.variant_dir(plan, root)
    outputs.write_json(dest / "error_plan.json", plan)

    if resume:
        existing = outputs.already_accepted(root, plan)
        if existing:
            log(f"  already accepted, skipping: {existing.get('generated_video')}")
            return {"status": "skipped", "plan": plan, "manifest": existing}

    if settings.dry_run:
        result = generation.generate_variant(
            record, plan, client, settings, budget,
            analysis=analysis, error=error, seed=seed, root=root)
        log(f"  dry run: {result.get('selection', {}).get('model')} "
            f"est ${result.get('estimated_cost') or 0:.2f} "
            f"window {plan['edit_window']['start']:.1f}-{plan['edit_window']['end']:.1f}s")
        return {"status": "dry_run", "plan": plan, "result": result}

    feedback = ""
    spent = 0.0
    last: dict = {}
    for attempt in range(1, max_attempts + 1):
        try:
            result = generation.generate_variant(
                record, plan, client, settings, budget, analysis=analysis,
                error=error, seed=seed, root=root, retry_feedback=feedback)
        except BudgetExceeded as exc:
            log(f"  budget stop: {exc}")
            _record_failure(root, plan, {"reason": str(exc), "stage": "budget"})
            return {"status": "budget_exceeded", "plan": plan, "error": str(exc)}

        last = result
        spent += float(result.get("cost") or 0.0)
        outputs.record_cost(
            root, task_code=plan["task_code"], subtask_id=plan["subtask_id"],
            error_id=plan["error_id"], stage=f"generate:attempt{attempt}",
            model=result.get("selection", {}).get("model", "?"),
            estimated=result.get("selection", {}).get("estimated_cost_usd"),
            actual=result.get("cost"), job_id=result.get("job_id"))

        if result["status"] == "declined":
            return {"status": "declined", "plan": plan}
        if result["status"] != "generated":
            log(f"  attempt {attempt} {result['status']}: {result.get('error')}")
            _record_failure(root, plan, {"reason": result.get("error"),
                                         "stage": result["status"],
                                         "job_id": result.get("job_id"),
                                         "cost": result.get("cost")})
            continue

        verdict = qa.evaluate(record, plan, result["video"], client, settings)
        outputs.write_json(dest / "qa_result.json", verdict)
        outputs.record_cost(
            root, task_code=plan["task_code"], subtask_id=plan["subtask_id"],
            error_id=plan["error_id"], stage=f"qa:attempt{attempt}",
            model=verdict.get("_model", "?"), estimated=None,
            actual=(verdict.get("_usage") or {}).get("cost"),
            accepted=verdict.get("accepted"))
        spent += float((verdict.get("_usage") or {}).get("cost") or 0.0)

        if verdict.get("accepted"):
            row = outputs.manifest_row(
                plan, generated_video=result["video"], qa=verdict,
                selection=result["selection"],
                analysis_model=(analysis.get("_meta") or {}).get("model", "?"),
                qa_model=verdict.get("_model", "?"), cost=spent)
            outputs.append_jsonl(root / outputs.MANIFEST, row)
            outputs.write_json(dest / "metadata.json", {
                "plan": plan, "selection": result["selection"],
                "prompt": result["prompt"], "splice": result.get("splice"),
                "qa": verdict, "attempts": attempt, "cost_usd": round(spent, 4),
                "analysis": {k: v for k, v in analysis.items() if k != "_meta"},
                "analysis_meta": analysis.get("_meta"),
            })
            log(f"  accepted on attempt {attempt}: {Path(result['video']).name} "
                f"(${spent:.2f})")
            return {"status": "accepted", "plan": plan, "manifest": row,
                    "qa": verdict, "result": result}

        reasons = "; ".join(verdict.get("rejection_reasons") or []) or "unspecified"
        log(f"  attempt {attempt} rejected: {reasons}")
        _record_failure(root, plan, {
            "reason": reasons, "stage": "qa", "job_id": result.get("job_id"),
            "cost": result.get("cost"), "attempt": attempt,
            "video": str(Path(result["video"]).relative_to(ROOT)),
            "qa": verdict})
        feedback = prompts.retry_note(verdict)

    return {"status": "rejected", "plan": plan, "result": last, "cost": spent}


def _record_failure(root: Path, plan: dict, extra: dict) -> None:
    row = {
        "source_video": plan["source_video"],
        "task_code": plan["task_code"],
        "subtask_id": plan["subtask_id"],
        "error_id": plan["error_id"],
        "timestamp": outputs.now(),
    }
    row.update(extra)
    outputs.append_jsonl(Path(root) / outputs.FAILURES, row)
