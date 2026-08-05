#!/usr/bin/env python3
"""Validate compiled task packs: schema, referential integrity, assumptions.

    python3 packs/pack_lint.py                      # lint every pack
    python3 packs/pack_lint.py AM.I.E.S1            # lint one
    python3 packs/pack_lint.py --require-reviewed   # session-use gate

Checks performed:

  schema       required keys, enum values, unique ids, non-empty verbatim text
  referential  every referenced file exists; sources.json hashes still match the
               files on disk; handbook sidecars agree with the pack's citation
  assumptions  every item flagged `assumed: true` has a matching entry in the
               top-level `assumptions:` list, and vice versa

Errors fail the lint. Warnings do not, except under --require-reviewed, which
additionally fails any pack not marked `status: reviewed` — a draft pack has not
been through subject-matter review and must not drive a live student session.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
TASK_DIR = ROOT / "tasks"

STATUSES = {"draft", "reviewed"}
OBSERVABLES = {"photo", "video", "document", "measurement"}
SEVERITIES = {"critical", "major", "minor"}
REQUIRED_TOP = ["schema_version", "status", "acs_code", "task_no", "title", "steps", "evidence"]


class Report:
    def __init__(self, name: str):
        self.name = name
        self.errors: list[str] = []
        self.warnings: list[str] = []

    def error(self, msg: str) -> None:
        self.errors.append(msg)

    def warn(self, msg: str) -> None:
        self.warnings.append(msg)

    @property
    def ok(self) -> bool:
        return not self.errors


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def check_ids(items: list[dict], kind: str, rep: Report, seen: set[str]) -> None:
    for item in items:
        ident = item.get("id")
        if not ident:
            rep.error(f"{kind}: entry missing 'id' ({str(item)[:60]})")
        elif ident in seen:
            rep.error(f"{kind}: duplicate id '{ident}'")
        else:
            seen.add(ident)


def collect_assumed(node, path: str, found: list[tuple[str, str]]) -> None:
    """Walk the pack collecting every object with assumed: true."""
    if isinstance(node, dict):
        if node.get("assumed") is True:
            found.append((node.get("id") or "<no id>", path))
        for key, value in node.items():
            collect_assumed(value, f"{path}.{key}", found)
    elif isinstance(node, list):
        for i, value in enumerate(node):
            collect_assumed(value, f"{path}[{i}]", found)


def lint_pack(pack_dir: Path, require_reviewed: bool) -> Report:
    rep = Report(pack_dir.name)
    pack_file = pack_dir / "pack.yaml"
    if not pack_file.exists():
        rep.error("no pack.yaml")
        return rep

    try:
        pack = yaml.safe_load(pack_file.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        rep.error(f"pack.yaml does not parse: {str(exc)[:200]}")
        return rep
    if not isinstance(pack, dict):
        rep.error("pack.yaml is not a mapping")
        return rep

    # --- schema -----------------------------------------------------------
    for key in REQUIRED_TOP:
        if key not in pack:
            rep.error(f"missing required key '{key}'")

    status = pack.get("status")
    if status not in STATUSES:
        rep.error(f"status must be one of {sorted(STATUSES)}, got {status!r}")
    if require_reviewed and status != "reviewed":
        rep.error(f"status is '{status}', but --require-reviewed demands 'reviewed'")

    if pack.get("acs_code") != pack_dir.name:
        rep.error(f"acs_code {pack.get('acs_code')!r} does not match directory {pack_dir.name!r}")

    seen_ids: set[str] = set()
    steps = pack.get("steps") or []
    if not steps:
        rep.error("pack defines no steps")
    check_ids(steps, "steps", rep, seen_ids)

    for step in steps:
        sid = step.get("id", "?")
        if not (step.get("text") or "").strip():
            rep.error(f"step {sid}: empty verbatim text")
        checks = step.get("checks") or []
        if not checks:
            rep.error(f"step {sid}: no checks defined")
        check_ids(checks, f"step {sid} checks", rep, seen_ids)
        for chk in checks:
            obs = chk.get("observable")
            if obs not in OBSERVABLES:
                rep.error(f"check {chk.get('id', '?')}: observable {obs!r} "
                          f"not in {sorted(OBSERVABLES)}")
        errs = step.get("error_modes") or []
        check_ids(errs, f"step {sid} error_modes", rep, seen_ids)
        for err in errs:
            sev = err.get("severity")
            if sev not in SEVERITIES:
                rep.error(f"error_mode {err.get('id', '?')}: severity {sev!r} "
                          f"not in {sorted(SEVERITIES)}")
        if not errs:
            rep.warn(f"step {sid}: no error modes listed")

    evidence = (pack.get("evidence") or {}).get("required") or []
    if not evidence:
        rep.error("evidence.required is empty")
    check_ids(evidence, "evidence", rep, seen_ids)
    for ev in evidence:
        if ev.get("medium") not in OBSERVABLES:
            rep.error(f"evidence {ev.get('id', '?')}: medium {ev.get('medium')!r} "
                      f"not in {sorted(OBSERVABLES)}")

    # --- referential ------------------------------------------------------
    sources_file = pack_dir / "sources.json"
    if not sources_file.exists():
        rep.error("no sources.json (run packs/ingest.py)")
    else:
        for src in json.loads(sources_file.read_text()).get("sources", []):
            path = ROOT / src["path"]
            if not path.exists():
                rep.error(f"source missing from disk: {src['path']}")
            elif sha256(path) != src["sha256"]:
                rep.error(f"source changed since ingest (sha256 mismatch): {src['path']}")

    refs = pack.get("references") or {}
    for ref in refs.get("handbook") or []:
        rel = ref.get("file")
        if not rel:
            rep.error("handbook reference missing 'file'")
            continue
        md = pack_dir / rel
        if not md.exists():
            rep.error(f"handbook reference file missing: {rel}")
            continue
        sidecar = md.with_suffix(".json")
        if not sidecar.exists():
            rep.warn(f"handbook reference has no sidecar metadata: {sidecar.name}")
            continue
        meta = json.loads(sidecar.read_text())
        if meta.get("cited_by_source") != ref.get("cited_by_source"):
            rep.error(f"{rel}: cited_by_source disagrees with extractor sidecar "
                      f"({ref.get('cited_by_source')} vs {meta.get('cited_by_source')})")
        if meta.get("labels") and ref.get("pages") and meta["labels"] != ref["pages"]:
            rep.error(f"{rel}: pages {ref['pages']} disagree with extracted "
                      f"{meta['labels']}")

    for vid in refs.get("videos") or []:
        path = ROOT / vid.get("path", "")
        if not vid.get("path") or not path.exists():
            rep.error(f"video missing from disk: {vid.get('path')!r}")

    # --- assumptions ------------------------------------------------------
    declared = {a.get("id") for a in (pack.get("assumptions") or [])}
    found: list[tuple[str, str]] = []
    for key in ("steps", "evidence", "references"):
        collect_assumed(pack.get(key), key, found)
    for ident, where in found:
        if ident not in declared:
            rep.error(f"assumed item '{ident}' at {where} has no entry in assumptions:")
    flagged = {i for i, _ in found}
    for ident in declared - flagged:
        rep.warn(f"assumptions lists '{ident}' but nothing is flagged assumed: true")

    return rep


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("acs_code", nargs="?")
    ap.add_argument("--require-reviewed", action="store_true",
                    help="fail packs not marked status: reviewed (session-use gate)")
    args = ap.parse_args()

    if args.acs_code:
        dirs = [TASK_DIR / args.acs_code]
    else:
        dirs = sorted(d for d in TASK_DIR.iterdir() if (d / "pack.yaml").exists()) \
            if TASK_DIR.exists() else []

    if not dirs:
        print("no packs found (looked for tasks/*/pack.yaml)")
        return 1

    failed = 0
    for pack_dir in dirs:
        rep = lint_pack(pack_dir, args.require_reviewed)
        mark = "PASS" if rep.ok else "FAIL"
        print(f"[{mark}] {rep.name}")
        for msg in rep.errors:
            print(f"    error:   {msg}")
        for msg in rep.warnings:
            print(f"    warning: {msg}")
        failed += 0 if rep.ok else 1

    print(f"\n{len(dirs) - failed}/{len(dirs)} packs passed")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
