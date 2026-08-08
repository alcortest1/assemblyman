#!/usr/bin/env python3
"""Build the portal's data extract from the alcor_agents working tree.

The portal is a standalone static bundle, so it cannot read `tasks/`, `build/`
and `data/` the way `alcor_agents/inspector/server.py` does. This script walks
the same sources and writes the subset the portal actually renders:

    portal/data/index.json          task list, counts, model names
    portal/data/tasks/<ACS>.json    steps, checks, error modes, criteria, runs
    portal/data/evals.json          model table and per-task table
    portal/data/frames/...          the frames a run actually graded
    portal/data/thumbs/...          Videos tab strip + Video assessment sequence

Only what the UI shows is emitted. `procedure.md` is confidential AIM material
and is never copied — the Documentation tab lists its section names, which is
what the design shows, and nothing more.

Usage:
    python3 scripts/build_portal_data.py [--no-images]
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
from collections import OrderedDict
from pathlib import Path

try:
    import yaml
except ImportError:  # pragma: no cover
    sys.exit("PyYAML is required: pip install pyyaml (or use alcor_agents/.venv)")

ROOT = Path(__file__).resolve().parent.parent
AGENTS = ROOT / "alcor_agents"
OUT = ROOT / "portal" / "data"

# Display names, in the column order the grid uses.
MODELS = OrderedDict([
    ("anthropic/claude-opus-5", "Opus 5"),
    ("google/gemini-3.1-pro-preview", "Gemini 3.1 Pro"),
    ("google/gemini-3.6-flash", "Gem 3.6 Flash"),
    ("openai/gpt-5.6-sol", "GPT-5.6 Sol"),
])
MODEL_IDS = list(MODELS)

# Arms a video run can carry that are not photo columns — today the on-device
# candidates. The video grid's columns are whatever arms its run graded (the
# design draws the run, not the registry), so labels live here rather than in
# MODELS, whose order is the photo grid.
ARM_LABELS = {
    "local/lfm2-vl-3b-q8": "LFM2-VL-3B Q8",
    "local/lfm2-vl-3b-q4": "LFM2-VL-3B Q4",
    "local/lfm2.5-vl-1.6b-q4": "LFM2.5-VL 1.6B",
}


def arm_label(mid: str) -> str:
    return MODELS.get(mid) or ARM_LABELS.get(mid) or mid.split("/")[-1]

# Frames per clip kept for the Videos tab strip.
STRIP = 16
THUMB_PX = 320

# The rate the Video assessment tab samples a span at. The extraction under
# build/frames/ is 4 fps; a criterion is graded on a sequence drawn from it at
# this rate, each frame labelled with its own timestamp. Unlike STRIP — a fixed
# count, so a 105 s clip came out four times sparser than a 28 s one — this is a
# rate, and it means the same thing on every clip.
SAMPLE_FPS = 0.5


# ── helpers ────────────────────────────────────────────────────────────────

def slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_")


def short_title(code: str, title: str) -> str:
    """The sidebar's two-word handle for a task."""
    known = {
        "AM.I.C.S3": "Ballast calc", "AM.I.C.S5": "Weight & balance",
        "AM.I.D.S1": "Rigid line", "AM.I.D.S7": "Flexible hose",
        "AM.I.D.S8": "Flareless fitting", "AM.I.E.S1": "Safety wire",
        "AM.I.I.S1": "FAA Form 337", "AM.II.A.S6": "Patch repair",
        "AM.II.K.S3": "Elec connector", "AM.III.F.S11": "Wire lacing",
        "AM.III.M.S5": "Propeller repair",
    }
    return known.get(code) or " ".join(title.split()[:2]).rstrip(".,")


def as_text(item) -> str:
    """Packs write assumptions and open questions as either a string or a dict."""
    if isinstance(item, str):
        return item
    if isinstance(item, dict):
        for key in ("statement", "question", "text", "note", "assumption"):
            if item.get(key):
                return str(item[key])
        return "; ".join(f"{k}: {v}" for k, v in item.items() if isinstance(v, str))
    return str(item)


def run_files(code: str) -> list:
    d = AGENTS / "build" / "photo_eval" / code
    return sorted(d.glob("run_*.json")) if d.is_dir() else []


def latest_run(code: str):
    """The newest run that actually graded something.

    A run whose every call failed is still written to disk — a sweep that runs
    the account out of credit saves one all-HTTP-402 run per remaining task. It
    is the newest file, so choosing on date alone would replace a good grid with
    an empty one and report the task as having no evidence at all.
    """
    for path in reversed(run_files(code)):
        with open(path) as fh:
            run = json.load(fh)
        if any(not r.get("error") for r in (run.get("results") or [])):
            return run
    return None


def is_probe(rolls_up_to: str | None, target_id: str | None) -> bool:
    """Whether a run target probes a section rather than being a subtask of its own.

    A run grades more than the pack's sections. `step:dl.s1` is one step of
    `section:determine-the-line-route`, and `section:...#vmsghdgf5` is that same
    section's criterion reworded for a match test. Both belong under a section that
    is already on the screen.

    Read off `rolls_up_to` where there is one and the target id otherwise, because a
    reworded probe carries no `rolls_up_to` at all — which is exactly the case that
    has to be caught, since it then groups under the empty string.

    Unknown id shapes are not probes. Dropping a graded result the Evals table still
    scores is the worse failure, so this names precisely what it removes.
    """
    probe = rolls_up_to or target_id or ""
    return probe.startswith("step:") or "#" in probe


def video_run_files(code: str) -> list:
    d = AGENTS / "build" / "video_eval" / code
    return sorted(d.glob("vrun_*.json")) if d.is_dir() else []


def latest_video_run(code: str):
    """The newest video run that graded something, on the same rule as the photo one.

    A task with no video run is the normal case, not a fault: the runner is new
    and only some tasks have been through it. Returning None leaves the clip
    column empty, which is what it has always shown.
    """
    for path in reversed(video_run_files(code)):
        with open(path) as fh:
            run = json.load(fh)
        if any(p.get("verdict") for r in (run.get("results") or []) for p in r.get("points", [])):
            return run
    return None


def video_grid(gid: str, entries: list, vrun: dict | None) -> dict | None:
    """The Video assessment grid for one subtask, shaped as the design draws it.

    Columns are the video run's own arms, in the run's order — not MODEL_IDS,
    which is the photo grid. The two screens ask different questions of
    different runs, and a video graded by the on-device candidates must not be
    drawn as four hosted columns that were never called.

    Rows are the same points, in the same order, as the photo grid beside it —
    the runner keys its work off the photo run precisely so this line-up holds.
    """
    if not vrun:
        return None
    by_model: "OrderedDict[str, dict]" = OrderedDict()
    for row in vrun.get("results") or []:
        if row.get("gid") == gid:
            by_model[row.get("model")] = row
    if not by_model:
        return None
    models = ([m for m in (vrun.get("models") or []) if m in by_model]
              or list(by_model))

    rows, replies, frames_sent, notes = [], {}, {}, []
    for mi, mid in enumerate(models):
        r = by_model[mid]
        if r.get("raw_text"):
            replies[f"m{mi}"] = r["raw_text"].strip()
        elif r.get("error"):
            replies[f"m{mi}"] = f"[{r['error']}] {r.get('message') or ''}".strip()
        frames_sent[f"m{mi}"] = r.get("frames") or []
        # The design's cap note, computed off what the run recorded: which arms
        # had frames dropped at even spacing before the call, and how many.
        if r.get("dropped"):
            notes.append(f"{arm_label(mid)} accepts {r['frame_count']} frames per call — "
                         f"{r['dropped']} of {r['span_frames']} dropped at even spacing "
                         "before the call.")

    for ri, (tid, _blob) in enumerate(entries):
        cells = []
        for mid in models:
            p = next((p for p in (by_model[mid].get("points") or [])
                      if p.get("target_id") == tid), None)
            if not p or not p.get("verdict"):
                cells.append(["none", "not graded"])
                continue
            at = p.get("at")
            # The cell carries the cited moment and nothing else — a verdict
            # whose note ran forty words made the grid unreadable. The note
            # waits in the reply panel, one click away.
            note = (p.get("note") or "").strip()
            detail = (f"t={at}" if at not in (None, "", "null")
                      else (note[:21] + "…" if len(note) > 22 else note))
            cells.append([p["verdict"], detail])
        rows.append({"cells": cells})

    # Segment roll-up, one column per arm, in the photo roll-up's own shape —
    # status, a compact P/F/U split, and the full sentence on hover — so the two
    # tabs read the same way: one fail fails, an unsure sends the segment to
    # review, and an arm that graded nothing stays ungraded rather than passing
    # on silence.
    rollup = []
    for mi in range(len(models)):
        got = [r["cells"][mi][0] for r in rows if r["cells"][mi][0] != "none"]
        if not got:
            rollup.append(["none", "ungraded", "no verdicts from this arm"])
            continue
        p, f, u = (got.count(k) for k in ("pass", "fail", "unsure"))
        status = "fail" if f else ("review" if u else "pass")
        rollup.append([status, f"{p}P {f}F {u}U",
                       f"{p} pass · {f} fail · {u} unsure of {len(got)}"])

    any_row = next(iter(by_model.values()))
    cost = sum(r.get("cost_usd") or 0 for r in by_model.values())
    return {
        "runId": vrun.get("run_id"),
        "fps": vrun.get("sample_fps"),
        "models": [arm_label(m) for m in models],
        "clip": any_row.get("video"),
        "rows": rows, "rollup": rollup, "replies": replies,
        "framesSent": frames_sent, "capNotes": notes,
        "cost": round(cost, 4),
    }


def segmented_clips(code: str) -> set:
    """Clips with a reviewed segmentation, so a frame drawn from one is reviewed."""
    d = AGENTS / "build" / "analysis" / code
    if not d.is_dir():
        return set()
    return {p.name[: -len(".segments.json")] for p in d.glob("*.segments.json")}


def handbook_ref(pack: dict) -> tuple[str, str, str]:
    """Return (label, provenance, extract filename) for the governing pages."""
    refs = ((pack.get("references") or {}).get("handbook") or [])
    if not refs:
        return ("—", "none", "")
    r = refs[0]
    vol = (r.get("handbook") or "").replace("FAA-H-8083-", "")
    pages = r.get("pages") or []
    label = f"{vol} {pages[0]}..{pages[-1]}" if pages else vol
    if r.get("cited_by_source"):
        prov = "cited"
    elif (r.get("located_by") or "") == "search":
        prov = "searched"
    else:
        prov = "located, not cited"
    return (label, prov, os.path.basename(r.get("file") or ""))


KINDS = {
    "workbook_xlsx": "Workbook — title, subject, photo fit, week/day",
    "workbook_csv": "Workbook row, normalized",
    "procedure_docx": "AIM skill sheet — verbatim step text",
    "steps_json": "Sections, steps, note references",
    "handbook_md": "Handbook extract with provenance sidecar",
}


def read_sources(code: str, prov: dict) -> list:
    """Compilation inputs with their recorded hashes.

    Only the basename is emitted: the recorded path points into the confidential
    AIM drive export, and the directory name is not the portal's to publish.
    """
    path = AGENTS / "tasks" / code / "sources.json"
    rows = []
    if path.is_file():
        with open(path) as fh:
            for s in (json.load(fh).get("sources") or []):
                sha = (s.get("sha256") or "")
                rows.append({
                    "a": os.path.basename(s.get("path") or ""),
                    "b": f"{sha[:6]}…{sha[-4:]}" if sha else "—",
                    "c": KINDS.get(s.get("kind") or "", s.get("kind") or "compilation input"),
                })
    for s in (prov.get("sources") or []):
        name = os.path.basename(s)
        if not any(r["a"] == name for r in rows):
            rows.append({"a": name, "b": "—", "c": "compilation input"})
    return rows


def sheet_sections(code: str) -> tuple[list, int]:
    """The normalized sheet's own sections, as `steps.json` parsed them.

    Names and counts only — the sheet is confidential AIM material, so nothing
    it says is copied. The pack's compiled sections are a subset of these: the
    front matter ("Before You Begin", "Safety and Equipment") carries no steps
    to grade, so `compile_pack.py` drops it and the Documentation tab must not
    pretend the sheet is shaped like the pack.
    """
    path = AGENTS / "tasks" / code / "steps.json"
    if not path.is_file():
        return ([], 0)
    with open(path) as fh:
        doc = json.load(fh)
    variants = doc.get("variants") or []
    out = []
    for v in variants:
        for s in (v.get("sections") or []):
            out.append({
                "name": s.get("section") or "Procedure",
                "variant": (v.get("variant") or "") if len(variants) > 1 else "",
                "steps": len(s.get("steps") or []),
                "notes": len(s.get("notes") or []),
                "safety": len(s.get("safety") or []),
                "equipment": len(s.get("equipment") or []),
                "prereqs": len(s.get("you_need_to") or []),
            })
    return (out, len(variants))


def criteria_points(criteria: dict, section: str) -> list:
    """Compiled points for a section the latest run did not grade.

    Without this a subtask with a drafted criterion and no run fell through to a
    generic placeholder set in the UI, which reads exactly like compiled text.
    """
    pts = []
    for e in (criteria.get("entries") or {}).values():
        if (e.get("section") or "") != section:
            continue
        for line in (e.get("criterion") or "").splitlines():
            line = line.strip().lstrip("-•").strip()
            if line:
                pts.append({"n": f"{len(pts) + 1}.", "text": line})
    return pts


def excluded_note(sec_steps: list) -> str:
    """What this subtask's criterion leaves out, counted from the pack."""
    kinds: "OrderedDict[str, int]" = OrderedDict()
    for s in sec_steps:
        for c in (s.get("checks") or []):
            obs = c.get("observable") or "photo"
            if obs != "photo":
                kinds[obs] = kinds.get(obs, 0) + 1
    if not kinds:
        return "None — every check in this subtask is photo-observable."
    parts = ", ".join(f"{n} [{k}]" for k, n in kinds.items())
    return f"{parts} — held beside the frame, never folded into the criterion."


def rollup_cells(run: dict, target_id: str, polarity: str) -> list:
    """The run's own per-model roll-up for one subtask, as it recorded it.

    `rollups` is written by the eval pipeline and carries both polarities, so the
    controls roll up on the same rule as the criteria instead of going unreported.
    """
    by_model = {}
    for r in (run.get("rollups") or []):
        if (r.get("polarity") or "original") != polarity:
            continue
        owner = r.get("negative_of") if polarity == "negative" else r.get("target_id")
        if owner == target_id:
            by_model[r.get("model")] = r

    cells = []
    for mid in MODEL_IDS:
        r = by_model.get(mid)
        if not r:
            cells.append(["none", "not graded", ""])
            continue
        p = r.get("passed") or 0
        f = len(r.get("failed") or [])
        u = len(r.get("unsettled") or [])
        cells.append([r.get("verdict") or "review", f"{p}P {f}F {u}U",
                      f"{p} pass · {f} fail · {u} unsure of {r.get('checks', p + f + u)}"])
    return cells


def verdict_detail(res: dict) -> str:
    """The short string under a verdict chip."""
    v = res.get("verdict")
    conf = res.get("confidence")
    if v in ("pass", "fail") and isinstance(conf, (int, float)):
        return f"{conf:.2f}"
    # `unsure` carries a sentence saying what the photo would have needed. The cell
    # has room for a phrase, so cut on a word and let the reply drawer carry the rest.
    missing = res.get("missing_evidence")
    if isinstance(missing, list) and missing:
        missing = missing[0]
    if isinstance(missing, str) and missing.strip():
        words, out = missing.strip().split(), []
        for w in words:
            if len(" ".join(out + [w])) > 24:
                break
            out.append(w)
        return " ".join(out) + "…" if len(out) < len(words) else " ".join(out)
    return "not observable"


# ── per-task extraction ────────────────────────────────────────────────────

def build_task(code: str) -> dict | None:
    pack_path = AGENTS / "tasks" / code / "pack.yaml"
    if not pack_path.is_file():
        return None
    with open(pack_path) as fh:
        pack = yaml.safe_load(fh)

    steps = pack.get("steps") or []
    corr = sum(len(s.get("checks") or []) for s in steps)
    defect = sum(len(s.get("error_modes") or []) for s in steps)

    prov = pack.get("provenance") or {}
    hand = not prov.get("generator")
    hb_label, hb_prov, hb_file = handbook_ref(pack)

    frames_dir = AGENTS / "build" / "frames" / code
    clip_names = sorted(p.name for p in frames_dir.glob("*") if p.is_dir()) if frames_dir.is_dir() else []
    segmented = (AGENTS / "build" / "analysis" / code).is_dir()

    crit_path = AGENTS / "build" / "criteria" / f"{code}.json"
    criteria = json.load(open(crit_path)) if crit_path.is_file() else {"entries": {}}

    run = latest_run(code)

    # Photo targets: the criterion entries a photo is graded against. A hand-compiled
    # pack has no build/criteria/ file — its sheets were built straight from the pack
    # and only the run records them — so counting the file alone reported 0 targets
    # for AM.II.K.S3 and AM.I.E.S1 while the portal rendered their graded points.
    targets = len(criteria.get("entries") or {})
    targets_prov = "build/criteria/"
    if not targets and run:
        targets = len({r.get("rolls_up_to") for r in (run.get("results") or [])
                       if (r.get("polarity") or "original") == "original" and r.get("rolls_up_to")
                       and not is_probe(r.get("rolls_up_to"), r.get("target_id"))})
        targets_prov = "saved run" if targets else "none compiled"

    # Sections are the design's subtasks; steps keep their order inside one.
    sections: "OrderedDict[str, list]" = OrderedDict()
    for s in steps:
        sections.setdefault(s.get("section") or "Procedure", []).append(s)

    subtasks = []
    for name, sec_steps in sections.items():
        atoms = sum(len(s.get("checks") or []) + len(s.get("error_modes") or []) for s in sec_steps)
        subtasks.append({
            "label": name,
            "sheet": slug(name),
            "stepsCount": len(sec_steps),
            "atomsCount": atoms,
            "excluded": excluded_note(sec_steps),
            "steps": [{
                "id": s["id"],
                "text": s.get("text") or "",
                "checks": [{
                    "id": (c.get("id") or "").split(".")[-1],
                    "text": c.get("statement") or "",
                    "obs": c.get("observable") or "photo",
                    "src": c.get("note") or (f"Source: {c.get('source')}" if c.get("source") else ""),
                } for c in (s.get("checks") or [])],
                "errors": [{
                    "id": (e.get("id") or "").split(".")[-1],
                    "text": e.get("statement") or "",
                    "sev": e.get("severity") or "major",
                } for e in (s.get("error_modes") or [])],
            } for s in sec_steps],
        })

    vrun = latest_video_run(code)
    attach_runs(subtasks, run, len(run_files(code)), segmented_clips(code), vrun)

    # A subtask the latest run did not grade still has its compiled criterion; show
    # that rather than letting the UI fall back to a generic placeholder set.
    for st in subtasks:
        if not st.get("sheetPoints"):
            pts = criteria_points(criteria, st["label"])
            if pts:
                st["sheetPoints"] = pts

    sheet_secs, sheet_variants = sheet_sections(code)

    return {
        "code": code,
        "short": short_title(code, pack.get("title") or code),
        "title": pack.get("title") or code,
        "subject": pack.get("subject") or "General",
        "steps": len(steps),
        "corr": corr,
        "def": defect,
        "atoms": corr + defect,
        "targets": targets,
        "targetsProv": targets_prov,
        "handbook": hb_label,
        "hbProv": ("hand-compiled · " if hand else "") + hb_prov,
        "handbookFile": hb_file,
        "clipNames": clip_names,
        "clips": len(clip_names),
        "segmented": segmented,
        "segClips": sorted(segmented_clips(code)),
        "hand": hand,
        "sheetSections": sheet_secs,
        "sheetVariants": sheet_variants,
        "thresholds": (run or {}).get("thresholds") or {},
        "runCount": len(run_files(code)),
        "assumptions": [as_text(a) for a in (pack.get("assumptions") or [])][:6],
        "openQuestions": [as_text(q) for q in (pack.get("open_questions") or [])][:6],
        "sources": read_sources(code, prov),
        "runId": (run or {}).get("run_id"),
        "runCost": round((run or {}).get("summary", {}).get("cost_usd", 0.0), 2),
        "runCalls": (run or {}).get("summary", {}).get("calls", 0),
        "subtasks": subtasks,
    }


def attach_runs(subtasks: list, run: dict | None, run_count: int = 0,
                seg_clips: set | None = None, vrun: dict | None = None) -> None:
    """Map a saved run's results onto the subtask that owns them."""
    if not run:
        return
    seg_clips = seg_clips or set()

    # results are flat: one row per (target, model).
    by_target: "OrderedDict[str, dict]" = OrderedDict()
    for r in run.get("results") or []:
        tid = r.get("target_id")
        if not tid:
            continue
        by_target.setdefault(tid, {"meta": r, "models": {}})["models"][r.get("model")] = r

    # A negative names the subtask it perturbs plus the point within it
    # (`section:cut-the-tubing` + `c1`), which rebuilds the original's target id.
    originals, negatives = OrderedDict(), {}
    for tid, blob in by_target.items():
        meta = blob["meta"]
        if (meta.get("polarity") or "original") == "negative":
            parent, point = meta.get("negative_of"), meta.get("negative_of_point")
            negatives[f"{parent}::{point}" if parent and point else tid] = blob
        else:
            originals[tid] = blob

    # Group originals under their subtask (rolls_up_to), preserving order — minus the
    # probes (see `is_probe`), which cost twice over:
    #
    #   · appended as subtasks of their own they put the same clip on the rail three
    #     to six times — AM.I.D.S1 came out at thirty subtasks over seven clips and
    #     AM.I.D.S7 at nineteen over four, where the runs that graded sections alone
    #     came out right, AM.II.A.S6 at eight and AM.I.D.S8 at three;
    #   · and a reworded probe groups under the empty string, which `slug("")` makes
    #     a substring of every label below, letting it win the match for a section it
    #     has nothing to do with. That is what put AM.I.D.S7's "Cut The Hose" on a
    #     1-point reworded probe and pushed its real 7-point `section:cut-the-hose`
    #     into the appended rows under the same name, on a different clip.
    #
    # Filtered here rather than at the append below, so it fixes the match too.
    groups: "OrderedDict[str, list]" = OrderedDict()
    for tid, blob in originals.items():
        gid = blob["meta"].get("rolls_up_to") or ""
        if is_probe(gid, tid):
            continue
        groups.setdefault(gid, []).append((tid, blob))

    def norm(text: str) -> str:
        return slug(re.sub(r"\(.*?\)", "", (text or "").split("—")[-1]))

    for st in subtasks:
        want = slug(st["label"])
        gid = next((g for g in groups if want in slug(g) or slug(g) in want), None)
        if gid is None:
            gid = next((g for g in groups
                        if want in norm(groups[g][0][1]["meta"].get("parent_label", ""))), None)
        if gid is None:
            continue
        entries = groups.pop(gid)
        st.update(build_grid(gid, entries, negatives, run, run_count, seg_clips, vrun))

    # A run can grade targets no pack section matches: AM.I.E.S1 compiles to a single
    # "Procedure" section while the run grades its three sheet variants separately.
    # Carrying them keeps a graded result on the screen that reports it — without
    # this the task's own page said "no saved run" while the Evals table scored it.
    for gid, entries in groups.items():
        raw = entries[0][1]["meta"].get("parent_label") or gid
        part = raw.split("—")[1] if "—" in raw else raw
        label = re.sub(r"\(.*?\)", "", part).strip().replace("_", " ").title() or gid
        st = {
            "label": label, "sheet": slug(label), "stepsCount": 0, "atomsCount": 0,
            "steps": [], "fromRun": True,
            "excluded": "Not compiled into a pack section — this target exists only in the run.",
        }
        st.update(build_grid(gid, entries, negatives, run, run_count, seg_clips, vrun))
        subtasks.append(st)


def build_grid(gid: str, entries: list, negatives: dict, run: dict,
               run_count: int = 0, seg_clips: set | None = None,
               vrun: dict | None = None) -> dict:
    seg_clips = seg_clips or set()
    rows, neg_lines, replies, points = [], [], {}, []
    frame_file = frame_video = None
    calls = 0

    for ri, (tid, blob) in enumerate(entries):
        meta = blob["meta"]
        frame_file = frame_file or meta.get("frame")
        frame_video = frame_video or meta.get("video")
        text = meta.get("criterion") or meta.get("label") or tid
        points.append({"n": f"{ri + 1}.", "text": text})

        cells = []
        for mi, mid in enumerate(MODEL_IDS):
            res = blob["models"].get(mid)
            if not res:
                # Not graded is not the same as unsure: an ungraded cell must not
                # read as a model declining to call it.
                cells.append(["none", "not graded"])
                continue
            calls += 1
            cells.append([res.get("verdict") or "unsure", verdict_detail(res)])
            if res.get("raw_text"):
                replies[f"r{ri}m{mi}"] = res["raw_text"].strip()

        row = {"label": f"{ri + 1} · {text[:76]}", "cells": cells}

        nblob = negatives.get(tid)
        if nblob:
            nmeta = nblob["meta"]
            ncells = []
            for mi, mid in enumerate(MODEL_IDS):
                res = nblob["models"].get(mid)
                if not res:
                    ncells.append(["none", "not graded"])
                    continue
                calls += 1
                v = res.get("verdict") or "unsure"
                # A control that passed where the same model passed the original is
                # an accepted contradiction — the photo settled it and it said yes anyway.
                if v == "pass" and cells[mi][0] == "pass":
                    ncells.append(["accepted", "pass ✗ accepted"])
                elif v == "fail":
                    ncells.append(["fail", "✓"])
                elif v == "unsure":
                    ncells.append(["unsure", "not_pass ✓"])
                else:
                    ncells.append([v, verdict_detail(res)])
                if res.get("raw_text"):
                    replies[f"n{ri}m{mi}"] = res["raw_text"].strip()
            ncrit = nmeta.get("criterion") or ""
            row["neg"] = {"label": f"P{ri + 1} · {ncrit[:96]}", "src": "", "cells": ncells}
            neg_lines.append({"mark": f"P{ri + 1}", "from": f"{ri + 1}. {text}",
                              "text": ncrit, "status": "perturbed"})
        else:
            row["skip"] = f"P{ri + 1} dropped — no negative was generated for this point."
            neg_lines.append({"mark": f"P{ri + 1}", "from": f"{ri + 1}. {text}",
                              "text": text, "status": "skipped · not generated"})
        rows.append(row)

    if not rows:
        return {}

    # The roll-up is the pipeline's, not a second opinion computed here: the grid
    # shows the points it displays, while the run rolled up every check it graded,
    # and recomputing from the visible rows quietly disagreed with the run.
    rollup = {
        "criteria": rollup_cells(run, gid, "original"),
        "controls": rollup_cells(run, gid, "negative"),
    }

    flat = [c for r in rows if "neg" in r for c in r["neg"]["cells"]]
    accepted = sum(1 for c in flat if c[0] == "accepted")
    graded_ctl = [c for c in flat if c[0] != "none"]
    vgrid = video_grid(gid, entries, vrun)
    return {
        "sheetPoints": points,
        "run": {
            "rows": rows, "rollup": rollup, "negLines": neg_lines, "replies": replies,
            "controlStats": (f"{len(graded_ctl)} perturbed points · "
                             f"not passed {sum(1 for c in graded_ctl if c[0] != 'accepted')} · "
                             f"accepted {accepted}"),
        },
        **({"vrun": vgrid} if vgrid else {}),
        "frameFile": frame_file or "",
        "frameVideo": frame_video or "",
        "frameProv": "frame_reviewed" if frame_video in seg_clips else "frame_suggested",
        "runs": (f"{run_count} saved · {calls} calls in the latest"
                 if run_count else f"{calls} calls"),
    }


# ── evals tables ───────────────────────────────────────────────────────────

def readiness(tasks: list) -> dict:
    """Counted off the tree, not typed into the screen.

    The three zeros the dashboard used to state as fact are still zeros today —
    but they are now measured, so the day one of these directories appears the
    screen stops claiming there is nothing in it.
    """
    ds = AGENTS / "evals" / "datasets"
    ag = AGENTS / "build" / "evals" / "runs"
    eg = AGENTS / "build" / "error_generation"
    clips = [p for p in eg.glob("*/*") if p.is_dir()] if eg.is_dir() else []
    labeled = [p for p in ds.glob("*.json")] if ds.is_dir() else []
    labeled_atoms = 0
    for p in labeled:
        try:
            with open(p) as fh:
                doc = json.load(fh)
        except (OSError, ValueError):
            continue
        labeled_atoms += len(doc.get("negatives") or doc.get("items") or [])
    return {
        "labeledDatasets": len(labeled),
        "agentRuns": len([p for p in ag.glob("*") if p.is_dir()]) if ag.is_dir() else 0,
        "photoEvalTasks": sum(1 for t in tasks if t["runId"]),
        "tasks": len(tasks),
        "atoms": sum(t["atoms"] for t in tasks),
        # Counted from the datasets themselves. Generated error clips are not
        # labels, so they are reported separately rather than folded in here.
        "labeledNegativeAtoms": labeled_atoms,
        "errorClips": len(clips),
        "errorClipTasks": len({p.parent.name for p in clips}),
    }


def build_evals(tasks: list) -> dict:
    per_model = {m: {"orig": [0, 0], "neg": [0, 0], "pairs": 0, "flipped": 0, "accepted": 0}
                 for m in MODEL_IDS}
    task_rows, totals = [], {"points": 0, "pairs": 0, "accepted": 0}

    for t in tasks:
        run = latest_run(t["code"])
        if not run:
            continue
        pm = (run.get("summary", {}).get("polarity", {}) or {}).get("models", {}) or {}
        o = n = pr = fl = ac = 0
        for mid, blk in pm.items():
            if mid not in per_model:
                continue
            orig, neg = blk.get("original", {}), blk.get("negative", {})
            paired = blk.get("paired", {})
            per_model[mid]["orig"][0] += orig.get("pass", 0)
            per_model[mid]["orig"][1] += orig.get("graded", 0)
            per_model[mid]["neg"][0] += neg.get("pass", 0)
            per_model[mid]["neg"][1] += neg.get("graded", 0)
            per_model[mid]["pairs"] += paired.get("pairs", 0)
            per_model[mid]["flipped"] += paired.get("flipped", 0)
            per_model[mid]["accepted"] += paired.get("accepted", 0)
            o += orig.get("pass", 0); n += neg.get("pass", 0)
            pr += paired.get("pairs", 0); fl += paired.get("flipped", 0)
            ac += paired.get("accepted", 0)

        pts = sum(b.get("original", {}).get("points", 0) for b in pm.values())
        gr_o = sum(b.get("original", {}).get("graded", 0) for b in pm.values()) or 1
        gr_n = sum(b.get("negative", {}).get("graded", 0) for b in pm.values()) or 1
        crit, negr = o / gr_o, n / gr_n
        task_rows.append([
            t["code"], t["short"], str(pts), f"{crit:.0%}", f"{negr:.0%}",
            f"{round((crit - negr) * 100)} pts", str(pr),
            f"{(fl / pr):.0%}" if pr else "—", str(ac),
        ])
        totals["points"] += pts; totals["pairs"] += pr; totals["accepted"] += ac
        totals["controls"] = totals.get("controls", 0) + sum(
            b.get("negative", {}).get("points", 0) for b in pm.values())
        totals["cost"] = totals.get("cost", 0.0) + (run.get("summary", {}).get("cost_usd") or 0.0)
        totals["calls"] = totals.get("calls", 0) + (run.get("summary", {}).get("calls") or 0)

    task_rows.sort(key=lambda r: -int(r[5].split()[0]))

    model_rows = []
    for mid, d in per_model.items():
        crit = d["orig"][0] / (d["orig"][1] or 1)
        negr = d["neg"][0] / (d["neg"][1] or 1)
        model_rows.append([
            MODELS[mid], f"{crit:.0%}", f"{negr:.0%}", f"{round((crit - negr) * 100)} pts",
            str(d["pairs"]), f"{(d['flipped'] / d['pairs']):.0%}" if d["pairs"] else "—",
            str(d["accepted"]), mid == "anthropic/claude-opus-5",
        ])

    # Video assessment, tallied over the newest valid run per task — the same
    # rule the Video assessment tab reads by, so this tally and those grids
    # agree. Ungraded is counted and shown: a reply that stopped short of a
    # point left it ungraded, and a tally that hid that would read as coverage.
    v_models: "OrderedDict[str, dict]" = OrderedDict()
    v_tot = {"pass": 0, "fail": 0, "unsure": 0, "ungraded": 0}
    v_tasks = v_calls = 0
    v_cost = 0.0
    for t in tasks:
        vrun = latest_video_run(t["code"])
        if not vrun:
            continue
        v_tasks += 1
        v_calls += vrun.get("summary", {}).get("calls") or 0
        v_cost += vrun.get("summary", {}).get("cost_usd") or 0.0
        for row in vrun.get("results") or []:
            rec = v_models.setdefault(row.get("model"),
                                      {"pass": 0, "fail": 0, "unsure": 0, "ungraded": 0})
            for p in row.get("points") or []:
                verdict = p.get("verdict")
                k = verdict if verdict in ("pass", "fail", "unsure") else "ungraded"
                rec[k] += 1
                v_tot[k] += 1
    ordered = [m for m in MODEL_IDS if m in v_models] + \
              [m for m in v_models if m not in MODEL_IDS]
    v_graded = v_tot["pass"] + v_tot["fail"] + v_tot["unsure"]
    video = {
        "models": [[arm_label(m),
                    str(v_models[m]["pass"]), str(v_models[m]["fail"]),
                    str(v_models[m]["unsure"]), str(v_models[m]["ungraded"]),
                    (lambda g: f"{v_models[m]['pass'] / g:.0%}" if g else "—")(
                        v_models[m]["pass"] + v_models[m]["fail"] + v_models[m]["unsure"])]
                   for m in ordered],
        "totals": {**v_tot, "graded": v_graded, "tasks": v_tasks,
                   "calls": v_calls, "cost": round(v_cost, 2)},
    }

    tot_o = sum(d["orig"][0] for d in per_model.values()) / (sum(d["orig"][1] for d in per_model.values()) or 1)
    tot_n = sum(d["neg"][0] for d in per_model.values()) / (sum(d["neg"][1] for d in per_model.values()) or 1)
    tot_f = sum(d["flipped"] for d in per_model.values())
    return {
        "video": video,
        "modelRows": model_rows,
        "taskRows": task_rows,
        "totals": ["All tasks", f"{totals['points']:,}", f"{tot_o:.0%}", f"{tot_n:.0%}",
                   f"{round((tot_o - tot_n) * 100)} pts", str(totals["pairs"]),
                   f"{(tot_f / totals['pairs']):.0%}" if totals["pairs"] else "—",
                   str(totals["accepted"])],
        "readiness": readiness(tasks),
        "run": {
            "points": totals["points"],
            "controls": totals.get("controls", 0),
            "calls": totals.get("calls", 0),
            "cost": round(totals.get("cost", 0.0), 2),
            "models": len(MODEL_IDS),
        },
    }


# ── images ─────────────────────────────────────────────────────────────────

def frame_seconds(name: str) -> float:
    """Seconds off an extracted frame's own name — t000041_50.jpg is 41.50 s.

    The extractor encodes the source timestamp in the filename precisely so a
    frame is citable back to the video without a lookup table. Everything that
    needs a time reads it here rather than counting positions in a directory.
    """
    m = re.match(r"t(\d+)_(\d+)", name)
    return int(m.group(1)) + int(m.group(2)) / 100 if m else 0.0


def clip_frames(code: str, clip: str) -> list:
    return sorted((AGENTS / "build" / "frames" / code / clip).glob("t*.jpg"))


def strip_picks(code: str, clip: str) -> list:
    """An evenly spaced strip of a clip's extracted frames."""
    allf = clip_frames(code, clip)
    if not allf:
        return []
    return [allf[round(i * (len(allf) - 1) / (STRIP - 1))] for i in range(STRIP)]


def sample_picks(code: str, clip: str) -> list:
    """A clip's frames at SAMPLE_FPS — the sequence a video assessment grades on.

    Picked by timestamp, not by position, so the interval between two kept frames
    is the same on a 28 s clip and a 105 s one. The frame nearest each tick is
    kept; a tick that lands past the last extracted frame is dropped rather than
    repeating the final image.
    """
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


def record_strips(tasks: list) -> None:
    """Name the strip frames in the JSON, whether or not images are copied.

    This ran inside copy_images, so `--no-images` — documented as re-emitting the
    JSON without recopying frames — emitted it with no strip at all, and every
    Videos tab reported "no extracted frames" over a full thumbs/ directory.
    """
    for t in tasks:
        for clip in t["clipNames"]:
            picks = strip_picks(t["code"], clip)
            if picks:
                t.setdefault("strips", {})[clip] = [p.name for p in picks]
            # The Video assessment sequence, at a rate rather than a count.
            sample = sample_picks(t["code"], clip)
            if sample:
                t.setdefault("samples", {})[clip] = [p.name for p in sample]


def copy_images(tasks: list) -> tuple[int, int]:
    graded = thumbs = 0
    for t in tasks:
        code = t["code"]
        # Frames a run actually graded, at full resolution.
        for st in t["subtasks"]:
            f, v = st.get("frameFile"), st.get("frameVideo")
            if not f or not v:
                continue
            src = AGENTS / "build" / "frames" / code / v / f
            if not src.is_file():
                continue
            dst = OUT / "frames" / code / v / f
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)
            graded += 1

        # The Videos tab strip and the Video assessment sequence, both downscaled.
        # They overlap on most clips; the copy below skips a file already written,
        # so a frame in both is fetched once.
        for clip in t["clipNames"]:
            picks = strip_picks(code, clip) + sample_picks(code, clip)
            if not picks:
                continue
            outdir = OUT / "thumbs" / code / clip
            outdir.mkdir(parents=True, exist_ok=True)
            for src in picks:
                dst = outdir / src.name
                if dst.exists():
                    continue
                try:
                    subprocess.run(["sips", "-Z", str(THUMB_PX), str(src), "--out", str(dst)],
                                   check=True, capture_output=True)
                except (OSError, subprocess.CalledProcessError):
                    shutil.copy2(src, dst)
                thumbs += 1
    return graded, thumbs


# ── main ───────────────────────────────────────────────────────────────────

def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-images", action="store_true", help="emit JSON only")
    args = ap.parse_args()

    codes = sorted(p.name for p in (AGENTS / "tasks").glob("AM.*") if p.is_dir())
    if not codes:
        sys.exit(f"no task packs under {AGENTS / 'tasks'}")

    tasks = [t for t in (build_task(c) for c in codes) if t]

    # Clear only what this run rebuilds: --no-images must leave copied frames alone.
    for sub in ["tasks"] + ([] if args.no_images else ["frames", "thumbs"]):
        if (OUT / sub).exists():
            shutil.rmtree(OUT / sub)
    (OUT / "tasks").mkdir(parents=True, exist_ok=True)

    record_strips(tasks)

    graded = thumbs = 0
    if not args.no_images:
        graded, thumbs = copy_images(tasks)

    order = {"General": 0, "Airframe": 1, "Powerplant": 2}
    tasks.sort(key=lambda t: (order.get(t["subject"], 9), t["code"]))

    for t in tasks:
        with open(OUT / "tasks" / f"{t['code']}.json", "w") as fh:
            json.dump(t, fh, ensure_ascii=False, separators=(",", ":"))

    index = {
        "models": list(MODELS.values()),
        "stats": {
            "tasks": len(tasks),
            "atoms": sum(t["atoms"] for t in tasks),
            "targets": sum(t["targets"] for t in tasks),
            "reviewed": 0,
        },
        "tasks": [{k: t[k] for k in (
            "code", "short", "title", "subject", "steps", "corr", "def", "atoms",
            "targets", "targetsProv", "handbook", "hbProv", "clips", "clipNames",
            "segmented", "hand", "runCost", "runCalls")} for t in tasks],
    }
    with open(OUT / "index.json", "w") as fh:
        json.dump(index, fh, ensure_ascii=False, indent=1)
    with open(OUT / "evals.json", "w") as fh:
        json.dump(build_evals(tasks), fh, ensure_ascii=False, indent=1)

    size = sum(f.stat().st_size for f in OUT.rglob("*") if f.is_file())
    print(f"tasks      {len(tasks)}")
    print(f"atoms      {index['stats']['atoms']:,}")
    print(f"targets    {index['stats']['targets']:,}")
    print(f"with runs  {sum(1 for t in tasks if t['runId'])}")
    print(f"images     {graded} graded, {thumbs} thumbs")
    print(f"total      {size / 1e6:.1f} MB → {OUT.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
