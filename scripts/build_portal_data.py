#!/usr/bin/env python3
"""Build the portal's data extract from the alcor_agents working tree.

The portal is a standalone static bundle, so it cannot read `tasks/`, `build/`
and `data/` the way `alcor_agents/inspector/server.py` does. This script walks
the same sources and writes the subset the portal actually renders:

    portal/data/index.json          task list, counts, model names
    portal/data/tasks/<ACS>.json    steps, checks, error modes, criteria, runs
    portal/data/evals.json          model table and per-task table
    portal/data/frames/...          the frames a run actually graded
    portal/data/thumbs/...          downscaled strip for the Videos tab

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

# Frames per clip kept for the Videos tab strip.
STRIP = 16
THUMB_PX = 320


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


def latest_run(code: str):
    d = AGENTS / "build" / "photo_eval" / code
    runs = sorted(d.glob("run_*.json")) if d.is_dir() else []
    if not runs:
        return None
    with open(runs[-1]) as fh:
        return json.load(fh)


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
    targets = len(criteria.get("entries") or {})

    run = latest_run(code)

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

    attach_runs(subtasks, run)

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
        "handbook": hb_label,
        "hbProv": ("hand-compiled · " if hand else "") + hb_prov,
        "handbookFile": hb_file,
        "clipNames": clip_names,
        "clips": len(clip_names),
        "segmented": segmented,
        "hand": hand,
        "assumptions": [as_text(a) for a in (pack.get("assumptions") or [])][:6],
        "openQuestions": [as_text(q) for q in (pack.get("open_questions") or [])][:6],
        "sources": read_sources(code, prov),
        "runId": (run or {}).get("run_id"),
        "runCost": round((run or {}).get("summary", {}).get("cost_usd", 0.0), 2),
        "runCalls": (run or {}).get("summary", {}).get("calls", 0),
        "subtasks": subtasks,
    }


def attach_runs(subtasks: list, run: dict | None) -> None:
    """Map a saved run's results onto the subtask that owns them."""
    if not run:
        return

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

    # Group originals under their subtask (rolls_up_to), preserving order.
    groups: "OrderedDict[str, list]" = OrderedDict()
    for tid, blob in originals.items():
        groups.setdefault(blob["meta"].get("rolls_up_to") or "", []).append((tid, blob))

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
        st.update(build_grid(entries, negatives, run))


def build_grid(entries: list, negatives: dict, run: dict) -> dict:
    rows, neg_lines, replies, points = [], [], {}, []
    frame_file = frame_video = None

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
                cells.append(["unsure", "no result"])
                continue
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
                    ncells.append(["unsure", "no result"])
                    continue
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

    # Roll-up: one fail fails, any unsure sends it to review.
    rollup = []
    for mi in range(len(MODEL_IDS)):
        vs = [r["cells"][mi][0] for r in rows]
        if "fail" in vs:
            rollup.append(["fail", f"fail · {vs.count('fail')}"])
        elif "unsure" in vs:
            rollup.append(["review", f"review · {vs.count('unsure')} unsure"])
        else:
            rollup.append(["pass", f"pass · {len(vs)}/{len(vs)}"])

    graded = sum(1 for r in rows if "neg" in r)
    flat = [c for r in rows if "neg" in r for c in r["neg"]["cells"]]
    accepted = sum(1 for c in flat if c[0] == "accepted")
    return {
        "sheetPoints": points,
        "run": {
            "rows": rows, "rollup": rollup, "negLines": neg_lines, "replies": replies,
            "controlStats": (f"{len(flat)} perturbed points · "
                             f"not passed {sum(1 for c in flat if c[0] != 'accepted')} · "
                             f"accepted {accepted}"),
        },
        "frameFile": frame_file or "",
        "frameVideo": frame_video or "",
        "frameProv": "frame_suggested",
        "runs": f"1 · {len(rows) * len(MODEL_IDS) * (2 if graded else 1)} calls",
    }


# ── evals tables ───────────────────────────────────────────────────────────

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

    tot_o = sum(d["orig"][0] for d in per_model.values()) / (sum(d["orig"][1] for d in per_model.values()) or 1)
    tot_n = sum(d["neg"][0] for d in per_model.values()) / (sum(d["neg"][1] for d in per_model.values()) or 1)
    tot_f = sum(d["flipped"] for d in per_model.values())
    return {
        "modelRows": model_rows,
        "taskRows": task_rows,
        "totals": ["All tasks", f"{totals['points']:,}", f"{tot_o:.0%}", f"{tot_n:.0%}",
                   f"{round((tot_o - tot_n) * 100)} pts", str(totals["pairs"]),
                   f"{(tot_f / totals['pairs']):.0%}" if totals["pairs"] else "—",
                   str(totals["accepted"])],
    }


# ── images ─────────────────────────────────────────────────────────────────

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

        # An evenly spaced strip per clip, downscaled for the Videos tab grid.
        for clip in t["clipNames"]:
            allf = sorted((AGENTS / "build" / "frames" / code / clip).glob("t*.jpg"))
            if not allf:
                continue
            picks = [allf[round(i * (len(allf) - 1) / (STRIP - 1))] for i in range(STRIP)]
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
            t.setdefault("strips", {})[clip] = [p.name for p in picks]
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
            "targets", "handbook", "hbProv", "clips", "clipNames", "segmented",
            "hand", "runCost", "runCalls")} for t in tasks],
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
