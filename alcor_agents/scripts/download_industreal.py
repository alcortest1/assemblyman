#!/usr/bin/env python3
"""Download a size-capped slice of the IndustReal dataset from 4TU.ResearchData.

IndustReal is 5.8 h of HoloLens 2 egocentric video of an industrial-like
assembly *and maintenance* procedure, with 22 correct assembly states, 27 error
states, and 38 execution-error types of which 14 appear only in val/test. That
unseen-error holdout is the reason we want it: it is the protocol our own eval
splits should copy (hold out whole error modes, not neighbouring frames), and it
gives `evals/datasets/` labelled negatives without waiting on AIM to film
failures.

The archive is ~86 GiB across 19 zips. Under a budget we take the label and
geometry files first, then val/test recordings (errors are concentrated there),
then train, and only then the synthetic data and model weights.

4TU does not publish stable per-file URLs, so the file list is resolved at
runtime from the Djehuty API and matched against the priority table below by
name. If the API shape changes, --dry-run will show it before anything is
fetched.

Licence: Apache 2.0 (code and data) — commercial use allowed, attribution kept.
Source:  https://data.4tu.nl/datasets/b008dd74-020d-4ea4-a8ba-7bb60769d224
Paper:   https://arxiv.org/abs/2310.17323
Repo:    https://github.com/TimSchoonbeek/IndustReal

    python3 scripts/download_industreal.py --budget-gb 25 --dry-run
    python3 scripts/download_industreal.py --budget-gb 25
    python3 scripts/download_industreal.py --budget-gb 25 --extract
"""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
import urllib.error
import urllib.request
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT_DIR = ROOT / "data" / "external" / "industreal"

ARTICLE = "b008dd74-020d-4ea4-a8ba-7bb60769d224"
API = "https://data.4tu.nl/v2"
GiB = 1024 ** 3

# Priority order, cheapest-and-most-useful first. `gib` is the size advertised
# on the landing page in Aug 2026 and is used only for --dry-run planning; the
# real size comes from the API. Anything the API returns that is not listed here
# sorts last and is taken only if budget remains.
PRIORITY = [
    ("README.md", 0.00006,
     "dataset README — label schemas and error-type table"),
    ("all_rgb_videos.zip", 0.0007,
     "index of RGB recordings"),
    ("ASD_results_IndustRealplusSynthetic_test.zip", 0.001,
     "assembly-state-detection results, real+synthetic"),
    ("part_geometries.zip", 0.016,
     "36 part models (FBX + 3MF) + overview_of_states.pdf"),
    ("action_recognition_labels.zip", 1.179,
     "AR / OD / PSR labels incl. PSR_labels_with_errors.csv"),
    ("val_p1.zip", 3.523,
     "val recordings part 1 — contains held-out error types"),
    ("train_p3.zip", 4.112,
     "train recordings part 3"),
    ("train_p2.zip", 4.471,
     "train recordings part 2"),
    ("train_p4.zip", 4.505,
     "train recordings part 4"),
    ("test_p2.zip", 5.544,
     "test recordings part 2 — contains held-out error types"),
    ("val_p2.zip", 5.795,
     "val recordings part 2 — contains held-out error types"),
    ("train_p1.zip", 5.934,
     "train recordings part 1"),
    ("test_p1.zip", 7.018,
     "test recordings part 1 — contains held-out error types"),
    ("test_p3.zip", 9.533,
     "test recordings part 3 — contains held-out error types"),
    ("ASD_results_SyntheticOnly_test.zip", 0.531,
     "assembly-state-detection results, synthetic only"),
    ("action_recognition_model_weights.zip", 4.620,
     "pretrained action-recognition weights"),
    ("assembly_state_detection_synthetic_data.zip.001", 7.813,
     "synthetic assembly-state data, part 1 of 2"),
    ("assembly_state_detection_synthetic_data.zip.002", 7.179,
     "synthetic assembly-state data, part 2 of 2"),
    ("assembly_state_detection_model_weights.zip", 7.813,
     "pretrained assembly-state-detection weights"),
]
ORDER = {name: i for i, (name, _, _) in enumerate(PRIORITY)}
BLURB = {name: what for name, _, what in PRIORITY}


def human(n_bytes: float) -> str:
    return f"{n_bytes / GiB:.2f} GiB"


def get_json(url: str) -> object:
    req = urllib.request.Request(url, headers={"Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=60) as resp:
        return json.load(resp)


def resolve_files() -> list[dict]:
    """Ask 4TU for the current file list. Returns name/size/url/md5 dicts."""
    attempts = [
        (f"{API}/articles/{ARTICLE}/files", lambda d: d),
        (f"{API}/articles/{ARTICLE}", lambda d: d.get("files", [])),
    ]
    raw = None
    for url, pick in attempts:
        try:
            raw = pick(get_json(url))
        except (urllib.error.URLError, ValueError, AttributeError) as exc:
            print(f"  . {url} -> {str(exc)[:120]}", file=sys.stderr)
            continue
        if raw:
            break

    if not raw:
        print(
            "\ncannot reach the 4TU API. The dataset is still downloadable by\n"
            "hand from https://data.4tu.nl/datasets/" + ARTICLE + "\n"
            "Drop the zips into " + str(OUT_DIR) + " and re-run with --extract.",
            file=sys.stderr,
        )
        return []

    out = []
    for entry in raw:
        name = entry.get("name") or entry.get("filename") or ""
        if not name:
            continue
        out.append({
            "name": name,
            "size": int(entry.get("size") or 0),
            "url": entry.get("download_url")
                   or f"https://data.4tu.nl/file/{ARTICLE}/{entry.get('id')}",
            "md5": entry.get("computed_md5") or entry.get("supplied_md5") or "",
        })
    return out


def supports_ranges(url: str) -> bool:
    """Does the host honour Range requests?

    4TU does not: it omits Accept-Ranges and answers a Range request with
    HTTP 200 and the *full* Content-Length. curl then refuses to resume with
    error 33. Detect this so we never leave a half-file that looks resumable
    but is not.
    """
    proc = subprocess.run(
        ["curl", "-s", "--max-time", "45", "-r", "1000-1999", "-D", "-", "-o", "/dev/null", url],
        capture_output=True, text=True,
    )
    return "206" in proc.stdout.split("\n")[0] or "content-range" in proc.stdout.lower()


def download(url: str, dest: Path, target: int = 0, attempts: int = 4) -> int:
    """Fetch url to dest, verifying the result against `target` bytes.

    Where the host supports ranges we resume in place. Where it does not (4TU),
    a partial file is worthless, so each attempt restarts into a .part file and
    only renames into place once the full length has arrived — a truncated
    transfer never masquerades as a complete download.
    """
    dest.parent.mkdir(parents=True, exist_ok=True)
    resumable = supports_ranges(url)
    part = dest.with_suffix(dest.suffix + ".part")

    for attempt in range(1, attempts + 1):
        if resumable:
            have = dest.stat().st_size if dest.exists() else 0
            if target and have >= target:
                break
            subprocess.run(["curl", "-fL", "--retry", "5", "--retry-delay", "3",
                            "--progress-bar", "-C", "-", "-o", str(dest), url])
            now = dest.stat().st_size if dest.exists() else 0
            if target and now >= target:
                break
            if now == have and attempt == attempts:
                break
            print(f"      attempt {attempt}: {human(now)} / {human(target)} — resuming",
                  flush=True)
            continue

        # Not resumable: always start clean, keep the partial out of the way.
        if attempt == 1 and dest.exists() and target and dest.stat().st_size < target:
            print(f"      discarding unusable partial ({human(dest.stat().st_size)}) "
                  f"— this host cannot resume", flush=True)
            dest.unlink()
        proc = subprocess.run(["curl", "-fL", "--retry", "3", "--retry-delay", "3",
                               "--max-time", "7200", "--progress-bar",
                               "-o", str(part), url])
        now = part.stat().st_size if part.exists() else 0
        if target and now >= target:
            part.replace(dest)
            break
        print(f"      attempt {attempt}: got {human(now)} of {human(target)} "
              f"(curl {proc.returncode})", file=sys.stderr)
        if part.exists():
            part.unlink()

    return dest.stat().st_size if dest.exists() else 0


def md5sum(path: Path, chunk: int = 8 << 20) -> str:
    h = hashlib.md5()
    with path.open("rb") as fh:
        for block in iter(lambda: fh.read(chunk), b""):
            h.update(block)
    return h.hexdigest()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--budget-gb", type=float, default=25.0,
                    help="total download cap in GiB")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--extract", action="store_true",
                    help="unzip archives after download (needs ~2x the space)")
    ap.add_argument("--verify", action="store_true",
                    help="check md5 against the 4TU manifest (slow)")
    ap.add_argument("--only", metavar="NAME",
                    help="fetch just this file (substring match). 4TU cannot "
                         "resume, so one file per run keeps each run short.")
    ap.add_argument("--list-missing", action="store_true",
                    help="show which budgeted files are still incomplete")
    args = ap.parse_args()

    files = resolve_files()
    if not files:
        return 1

    files.sort(key=lambda f: (ORDER.get(f["name"], len(ORDER)), f["name"]))
    total = sum(f["size"] for f in files)
    budget = int(args.budget_gb * GiB)

    chosen: list[dict] = []
    spent = 0
    for f in files:
        if f["size"] and spent + f["size"] > budget:
            continue  # skip, but keep scanning for smaller files that still fit
        chosen.append(f)
        spent += f["size"]

    print(f"IndustReal — budget {args.budget_gb:.0f} GiB of "
          f"{human(total)} total ({len(files)} files)\n")
    for f in files:
        take = "take" if f in chosen else "  - "
        print(f"  {take}  {f['name']:<48} {human(f['size']):>10}  "
              f"{BLURB.get(f['name'], '')}")
    print(f"\n  total planned: {human(spent)} across {len(chosen)} files")

    if args.list_missing:
        print()
        for f in chosen:
            p = OUT_DIR / f["name"]
            have = p.stat().st_size if p.exists() else 0
            if not f["size"] or have < f["size"]:
                print(f"  MISSING  {f['name']:<48} {human(have)} / {human(f['size'])}")
        return 0

    if args.only:
        chosen = [f for f in chosen if args.only in f["name"]]
        if not chosen:
            print(f"\nno budgeted file matches {args.only!r}", file=sys.stderr)
            return 1
        print(f"\n  --only {args.only!r}: {', '.join(f['name'] for f in chosen)}")

    if args.dry_run:
        return 0

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    got: list[dict] = []
    incomplete: list[str] = []
    for i, f in enumerate(chosen, 1):
        dest = OUT_DIR / f["name"]
        if dest.exists() and f["size"] and dest.stat().st_size == f["size"]:
            print(f"  [{i}/{len(chosen)}] {f['name']} — already complete")
            got.append({"file": f["name"], "bytes": dest.stat().st_size,
                        "expected_bytes": f["size"], "complete": True,
                        "md5_ok": None})
            continue
        print(f"\n  [{i}/{len(chosen)}] {f['name']} ({human(f['size'])})",
              flush=True)
        n = download(f["url"], dest, f["size"])
        complete = bool(f["size"]) and n >= f["size"]
        if not complete:
            print(f"      ** INCOMPLETE: {human(n)} of {human(f['size'])} **")
            incomplete.append(f["name"])
        ok = None
        if args.verify and f["md5"] and n and complete:
            ok = md5sum(dest) == f["md5"]
            print(f"      md5 {'ok' if ok else 'MISMATCH'}")
            if ok is False:
                incomplete.append(f["name"])
        got.append({"file": f["name"], "bytes": n, "expected_bytes": f["size"],
                    "complete": complete, "md5_ok": ok})

    if args.extract:
        for f in got:
            src = OUT_DIR / f["file"]
            if src.suffix != ".zip" or not src.exists():
                continue
            print(f"  extracting {src.name} ...", flush=True)
            try:
                with zipfile.ZipFile(src) as zf:
                    zf.extractall(OUT_DIR / "unpacked")
            except zipfile.BadZipFile:
                # .zip.001/.002 are a split archive; join before unzipping.
                print(f"    ! {src.name} is not a standalone zip — skipped "
                      "(split archive: cat the parts together first)")

    downloaded = sum(g["bytes"] for g in got)
    (OUT_DIR / "manifest.json").write_text(json.dumps({
        "dataset": "IndustReal",
        "source": f"https://data.4tu.nl/datasets/{ARTICLE}",
        "doi": f"10.4121/{ARTICLE}",
        "paper": "https://arxiv.org/abs/2310.17323",
        "license": "Apache 2.0 — commercial use allowed, attribution required",
        "why": "unseen-error holdout protocol; 22 correct + 27 error assembly "
               "states; 38 execution-error types, 14 val/test-only; includes a "
               "maintenance procedure, not just assembly",
        "full_dataset_gib": round(total / GiB, 2),
        "budget_gib": args.budget_gb,
        "downloaded_gib": round(downloaded / GiB, 2),
        "file_count": len(got),
        "files": got,
    }, indent=2), encoding="utf-8")

    print(f"\ndownloaded {human(downloaded)} across {len(got)} files "
          f"-> {OUT_DIR.relative_to(ROOT)}")
    print(f"manifest: {(OUT_DIR / 'manifest.json').relative_to(ROOT)}")
    if incomplete:
        print(f"\n{len(incomplete)} file(s) INCOMPLETE or md5-mismatched: "
              f"{', '.join(sorted(set(incomplete)))}")
        print("Re-run to resume; each file continues from where it stopped.")
        return 1
    print("\nnext: PSR_labels_with_errors.csv in each recording is the "
          "wrongly-executed-step ground truth; train.csv/val.csv/test.csv "
          "carry the split. 14 of 38 error types are val/test-only — keep that "
          "boundary when you mirror the protocol into evals/datasets/.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
