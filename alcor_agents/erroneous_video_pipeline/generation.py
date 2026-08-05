"""Stage 2 — submit the generation job, retrieve the segment, splice it back in.

Request shapes were established against the live API, not guessed:

* `input_references[].type` is a discriminated union over exactly
  `image_url` / `audio_url` / `video_url`. There is no `first_frame` type, and
  extra keys such as `role` are accepted but ignored — so **first versus last
  frame is positional**: index 0 is the opening frame, index 1 the closing one.
* Video references must be HTTPS; image references accept `data:` URIs. That one
  asymmetry is why the frame-guided tiers run with no infrastructure at all and
  the video-to-video tier needs `hosting.serve_file`.
* Optional parameters are sent only when the chosen model publishes support for
  them. Sending `duration` to a model whose `supported_durations` is null, or
  `seed` to one reporting `seed: false`, is how a job fails after it has billed.

Because there is no cancel endpoint, everything that can be checked is checked
before submission: the budget, the capability match, and — when hosting is in
play — that the URL actually serves before the provider tries to fetch it.
"""

from __future__ import annotations

import json
from pathlib import Path

from .config import ROOT, Budget, BudgetExceeded, Settings
from . import analysis as analysis_mod
from . import hosting, media, models, outputs, planning, prompts
from .discovery import VideoRecord
from .openrouter import Client, OpenRouterError

WORK_DIR = ROOT / "build" / "error_generation"


class GenerationError(RuntimeError):
    pass


def build_request(plan: dict, selection: models.Selection, prompt: str, *,
                  references: list[dict], seed: int | None = None) -> dict:
    """Assemble the POST /videos body for one attempt."""
    payload: dict = {"model": selection.model_id, "prompt": prompt}
    if references:
        payload["input_references"] = references
    cap = selection.capability
    if cap.aspect_ratios:
        payload["aspect_ratio"] = selection.aspect_ratio
    if cap.resolutions and selection.resolution:
        payload["resolution"] = selection.resolution
    if cap.durations and selection.duration_s:
        payload["duration"] = selection.duration_s
    if seed is not None and cap.seed:
        payload["seed"] = seed
    return payload


def _frame_references(source: Path, info: media.MediaInfo, start: float, end: float,
                      work: Path, mode: str) -> list[dict]:
    """First (and optionally last) frame of the edit window, as data URIs.

    The last frame is taken slightly inside the window rather than exactly at its
    end: the final frame of a cut often lands mid-motion-blur, and handing that
    to a generator as the target end state produces a smeared result.
    """
    refs = []
    first = media.extract_frame(source, start, work / "first_frame.jpg", info)
    refs.append({"type": "image_url",
                 "image_url": {"url": analysis_mod.data_uri(first, "image/jpeg")}})
    if mode == models.MODE_FIRST_LAST_FRAME:
        last_t = max(start, end - (1.0 / (info.fps or 30.0)) * 3)
        last = media.extract_frame(source, last_t, work / "last_frame.jpg", info)
        refs.append({"type": "image_url",
                     "image_url": {"url": analysis_mod.data_uri(last, "image/jpeg")}})
    return refs


def _confirm(selection: models.Selection, plan: dict, prompt: str) -> bool:
    cost = selection.estimated_cost
    print("\n" + "=" * 72)
    print("ABOUT TO SUBMIT A PAID GENERATION JOB — THIS CANNOT BE CANCELLED")
    print("=" * 72)
    print(f"  model        : {selection.model_id} ({selection.mode})")
    print(f"  task/subtask : {plan['task_code']} / {plan['subtask_id']}")
    print(f"  error        : {plan['error_id']}")
    print(f"  defect       : {plan['required_error']}")
    print(f"  edit window  : {plan['edit_window']['start']:.2f}s - "
          f"{plan['edit_window']['end']:.2f}s")
    print(f"  est. cost    : {'$%.2f' % cost if cost is not None else 'UNKNOWN'}")
    print(f"\n  prompt:\n    " + prompt.replace("\n", "\n    "))
    print("=" * 72)
    try:
        return input("Type 'yes' to submit: ").strip().lower() == "yes"
    except EOFError:
        print("no TTY available — refusing to submit unattended")
        return False


def generate_variant(record: VideoRecord, plan: dict, client: Client,
                     settings: Settings, budget: Budget, *,
                     analysis: dict, error: dict, seed: int | None = None,
                     version: int | None = None, root: Path | None = None,
                     retry_feedback: str = "") -> dict:
    """Produce one candidate video. Returns a result record for QA and manifests."""
    root = outputs.output_root(root)
    source = ROOT / record.video_path
    info = media.probe(source)
    start = plan["edit_window"]["start"]
    end = plan["edit_window"]["end"]
    window = end - start
    version = version or planning.next_version(plan, root)

    dest_dir = planning.variant_dir(plan, root)
    work = WORK_DIR / plan["task_code"] / f"{Path(record.video_path).stem}__{plan['error_id']}__v{version:02d}"
    work.mkdir(parents=True, exist_ok=True)

    rows = client.video_models()
    selection = models.select_model(
        rows, info, window,
        allow_video_reference=settings.allow_video_reference,
        requested=settings.video_model,
        with_audio=False)

    # Retry feedback goes through the same length budget rather than being
    # concatenated afterwards; appending it is what pushed the first real
    # submission past aleph-2's 1000-character ceiling.
    prompt = prompts.generation_prompt(analysis, error, info_note=retry_feedback or "")

    # The segment the model is asked to replace, normalised to SDR H.264 so both
    # the reference clip and the eventual splice share one colour pipeline.
    segment = media.extract_clip(source, start, end, work / "source_segment.mp4",
                                 info, with_audio=False)

    estimate = selection.estimated_cost
    if settings.dry_run:
        outputs.write_json(dest_dir / "generation_request.json", {
            "dry_run": True, "selection": selection.as_dict(), "prompt": prompt,
            "edit_window": plan["edit_window"], "source_segment": str(segment.relative_to(ROOT)),
        })
        return {"status": "dry_run", "selection": selection.as_dict(), "prompt": prompt,
                "estimated_cost": estimate, "version": version}

    budget.reserve(estimate if estimate is not None else 0.0)
    if settings.require_confirmation and not _confirm(selection, plan, prompt):
        budget.settle(estimate or 0.0, 0.0)
        return {"status": "declined", "selection": selection.as_dict(), "prompt": prompt}

    job = None
    try:
        if selection.mode == models.MODE_VIDEO_REFERENCE:
            with hosting.serve_file(segment) as url:
                references = [{"type": "video_url", "video_url": {"url": url}}]
                payload = build_request(plan, selection, prompt,
                                        references=references, seed=seed)
                outputs.write_json(dest_dir / "generation_request.json",
                                   _redacted_request(payload))
                job = client.submit_video(payload)
                # Poll inside the tunnel: the provider fetches the asset
                # asynchronously and tearing the URL down early fails the job.
                job = client.poll_video(job.id, timeout_s=2400)
        else:
            references = _frame_references(source, info, start, end, work, selection.mode)
            payload = build_request(plan, selection, prompt,
                                    references=references, seed=seed)
            outputs.write_json(dest_dir / "generation_request.json",
                               _redacted_request(payload))
            job = client.submit_video(payload)
            job = client.poll_video(job.id, timeout_s=2400)
    except BudgetExceeded:
        raise
    except (OpenRouterError, hosting.HostingError) as exc:
        budget.settle(estimate or 0.0, None if job else 0.0)
        return {"status": "error", "error": str(exc), "selection": selection.as_dict(),
                "prompt": prompt, "job_id": getattr(job, "id", None), "version": version}

    actual = job.cost
    budget.settle(estimate or 0.0, actual)
    outputs.write_json(dest_dir / "generation_response.json", job.raw)

    if not job.ok:
        return {"status": "failed", "error": f"job ended {job.status}",
                "selection": selection.as_dict(), "prompt": prompt,
                "job_id": job.id, "cost": actual, "version": version}

    raw_segment = client.download_video(job.id, work / "generated_segment.mp4")
    playable, why = media.is_playable(raw_segment)
    if not playable:
        return {"status": "unusable", "error": f"generated segment {why}",
                "selection": selection.as_dict(), "prompt": prompt,
                "job_id": job.id, "cost": actual, "version": version}

    final = dest_dir / planning.output_name(plan, version)
    splice_record = media.splice(source, raw_segment, start, end, final, info, work_dir=work)
    ok, why = media.is_playable(final, min_duration=max(1.0, info.duration_s * 0.5))
    if not ok:
        return {"status": "unusable", "error": f"spliced output {why}",
                "selection": selection.as_dict(), "prompt": prompt,
                "job_id": job.id, "cost": actual, "version": version}

    return {
        "status": "generated",
        "video": final,
        "selection": selection.as_dict(),
        "prompt": prompt,
        "job_id": job.id,
        "cost": actual,
        "version": version,
        "splice": splice_record,
        "generated_segment": str(raw_segment.relative_to(ROOT)),
    }


def _redacted_request(payload: dict) -> dict:
    """Strip inlined asset bytes so the saved request stays readable and small.

    A first/last-frame request carries two base64 JPEGs; writing them into
    `generation_request.json` would bloat the artifact for no reviewer benefit.
    The tunnel URL is dropped too — it is dead by the time anyone reads the file,
    and recording a public link to confidential footage serves no purpose.
    """
    clone = json.loads(json.dumps(payload))
    for ref in clone.get("input_references", []):
        for key in ("image_url", "video_url", "audio_url"):
            if key in ref and isinstance(ref[key], dict):
                url = ref[key].get("url", "")
                if url.startswith("data:"):
                    head = url.split(",", 1)[0]
                    ref[key]["url"] = f"<{head}, {len(url)} chars omitted>"
                else:
                    ref[key]["url"] = "<ephemeral tunnel URL omitted>"
    return clone
