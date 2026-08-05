"""Find the source videos and bind each to its task, procedure and criteria.

The repository already carries every fact this needs, in four places that have
to agree:

    data/videos/<ACS>/<clip>.mp4          the footage, foldered by ACS code
    data/processed/videos_manifest.csv    ACS code -> path, with Drive provenance
    tasks/<ACS>/procedure.md              the procedure the work must follow
    criteria/<ACS>/<ACS>__<subtask>.txt   the rubric a generated error must fail

The one genuinely ambiguous join is clip -> subtask, and the repository has
already settled it once: `packs/criteria_sources.preserved_ids` records that
AM.I.D.S1's clips are *named for the operations they show* — `bend_the_line`,
`flare_the_line` — while every other task numbers its clips (`flex_hose_1..6`),
which carries no name to match on. So an exact stem match against a criteria
filename is treated as authoritative, and a numbered clip is left ambiguous with
its candidate subtasks listed rather than guessed. Guessing here would attach an
error to the wrong rubric, which is the one failure this dataset cannot absorb.

Nothing in this module writes to `data/`, `tasks/` or `criteria/`.
"""

from __future__ import annotations

import csv
import re
from dataclasses import dataclass, asdict, field
from pathlib import Path

from .config import ROOT
from .media import VIDEO_SUFFIXES

VIDEO_DIR = ROOT / "data" / "videos"
TASK_DIR = ROOT / "tasks"
CRITERIA_DIR = ROOT / "criteria"
MANIFEST_CSV = ROOT / "data" / "processed" / "videos_manifest.csv"
SEGMENT_DIR = ROOT / "build" / "analysis"

ACS_RE = re.compile(r"^AM\.[IVX]+\.[A-Z]\.S\d+$")
NUMBERED_CLIP = re.compile(r"^(.*?)[._-]?(\d+)(_rf)?$")

# Words that carry no discriminating signal between one subtask and another.
_STOPWORDS = {"the", "a", "an", "of", "on", "in", "for", "and", "to", "with",
              "using", "from", "at", "by", "out", "up", "into", "rf"}


def _tokens(name: str) -> set[str]:
    return {w for w in re.findall(r"[a-z]+", name.lower()) if w not in _STOPWORDS}


def suggest_subtask(stem: str, candidates: list[str]) -> str | None:
    """Best-guess subtask for a clip whose name does not match one exactly.

    A *suggestion* only — never written into `subtask_id`. `safety_wire_by_hand`
    overlaps `wire_safety_on_bolts_by_hand` and `wire_safety_on_a_turnbuckle_by_hand`
    equally well, and silently picking one would bind a generated error to the
    wrong rubric. Ties therefore return nothing and the operator must choose.
    """
    clip = _tokens(stem)
    if not clip or not candidates:
        return None
    scored = sorted(((len(clip & _tokens(c)), c) for c in candidates), reverse=True)
    if not scored or scored[0][0] == 0:
        return None
    if len(scored) > 1 and scored[0][0] == scored[1][0]:
        return None
    return scored[0][1]


@dataclass
class VideoRecord:
    video_path: str
    task_code: str
    subtask_id: str | None
    procedure_path: str | None
    criteria_path: str | None
    candidate_subtasks: list[str] = field(default_factory=list)
    suggested_subtask: str | None = None
    pack_path: str | None = None
    segments_path: str | None = None
    match_basis: str = "unmatched"
    title: str | None = None

    def as_dict(self) -> dict:
        return asdict(self)

    @property
    def resolved(self) -> bool:
        return bool(self.subtask_id and self.criteria_path)


def _task_title(task_code: str) -> str | None:
    pack = TASK_DIR / task_code / "pack.yaml"
    if not pack.exists():
        return None
    for line in pack.read_text(errors="replace").splitlines():
        if line.startswith("title:"):
            return line.split(":", 1)[1].strip().strip("'\"")
    return None


def subtasks_for(task_code: str) -> dict[str, Path]:
    """Subtask id -> criteria file, taken from the compiled criteria filenames.

    `criteria/` is generated (and gitignored), so an empty result means the
    criteria have not been drafted for this task yet, not that none exist.
    """
    directory = CRITERIA_DIR / task_code
    if not directory.is_dir():
        return {}
    out: dict[str, Path] = {}
    for path in sorted(directory.glob(f"{task_code}__*.txt")):
        out[path.stem.split("__", 1)[1]] = path
    return out


def manifest_rows() -> dict[str, dict]:
    """Drive provenance keyed by repo-relative video path, when the CSV exists."""
    if not MANIFEST_CSV.exists():
        return {}
    with MANIFEST_CSV.open() as handle:
        return {row["path"]: row for row in csv.DictReader(handle) if row.get("path")}


def task_codes() -> list[str]:
    if not VIDEO_DIR.is_dir():
        return []
    return sorted(p.name for p in VIDEO_DIR.iterdir()
                  if p.is_dir() and ACS_RE.match(p.name))


def discover(task_code: str | None = None,
             subtask: str | None = None) -> list[VideoRecord]:
    """Every source video, bound to whatever metadata the repo can supply."""
    records: list[VideoRecord] = []
    manifest = manifest_rows()

    for code in task_codes():
        if task_code and code != task_code:
            continue
        criteria = subtasks_for(code)
        procedure = TASK_DIR / code / "procedure.md"
        pack = TASK_DIR / code / "pack.yaml"
        title = _task_title(code)

        for video in sorted((VIDEO_DIR / code).iterdir()):
            if video.suffix.lower() not in VIDEO_SUFFIXES:
                continue
            stem = video.stem
            matched, basis = None, "unmatched"

            if stem in criteria:
                # AM.I.D.S1's convention: the clip is named for the operation.
                matched, basis = stem, "clip name matches subtask id"
            elif len(criteria) == 1:
                matched, basis = next(iter(criteria)), "task has a single subtask"
            elif criteria:
                basis = "clip name does not name a subtask — pass --subtask"

            segments = SEGMENT_DIR / code / f"{stem}.segments.json"
            record = VideoRecord(
                video_path=str(video.relative_to(ROOT)),
                task_code=code,
                subtask_id=matched,
                procedure_path=str(procedure.relative_to(ROOT)) if procedure.exists() else None,
                criteria_path=(str(criteria[matched].relative_to(ROOT))
                               if matched and matched in criteria else None),
                candidate_subtasks=sorted(criteria),
                suggested_subtask=(None if matched
                                   else suggest_subtask(stem, sorted(criteria))),
                pack_path=str(pack.relative_to(ROOT)) if pack.exists() else None,
                segments_path=str(segments.relative_to(ROOT)) if segments.exists() else None,
                match_basis=basis,
                title=title or (manifest.get(str(video.relative_to(ROOT)), {}) or {}).get("task"),
            )
            records.append(record)

    if subtask:
        chosen = []
        for record in records:
            if record.subtask_id == subtask:
                chosen.append(record)
            elif record.subtask_id is None and subtask in record.candidate_subtasks:
                # An explicit --subtask resolves exactly the ambiguity that made
                # a numbered clip unmatched, so honour it.
                criteria = subtasks_for(record.task_code)
                record.subtask_id = subtask
                record.criteria_path = str(criteria[subtask].relative_to(ROOT))
                record.match_basis = "supplied on the command line"
                chosen.append(record)
        records = chosen
    return records


def find_video(spec: str) -> VideoRecord:
    """Resolve a path or bare clip name to one record."""
    candidate = Path(spec)
    target = candidate if candidate.is_absolute() else ROOT / spec
    matches = [r for r in discover()
               if Path(r.video_path).name == candidate.name
               or (ROOT / r.video_path) == target]
    if not matches:
        raise FileNotFoundError(f"no discovered video matches {spec!r}")
    if len(matches) > 1:
        paths = ", ".join(m.video_path for m in matches)
        raise ValueError(f"{spec!r} is ambiguous: {paths}")
    return matches[0]


def read_criteria(record: VideoRecord) -> str:
    return (ROOT / record.criteria_path).read_text(errors="replace") if record.criteria_path else ""


def read_procedure(record: VideoRecord, subtask_hint: str | None = None) -> str:
    """The procedure, narrowed to the relevant section when one is identifiable.

    Whole procedure sheets run several thousand words across every phase of the
    task; handing all of it to the analysis model dilutes the section that
    actually governs the edit window.
    """
    if not record.procedure_path:
        return ""
    text = (ROOT / record.procedure_path).read_text(errors="replace")
    hint = (subtask_hint or record.subtask_id or "").replace("_", " ").strip()
    if not hint:
        return text
    sections = re.split(r"^##\s+", text, flags=re.M)
    # Filtered by stopword, not by length: `cut_the_line`'s operation is three
    # characters, and dropping it leaves "line" leading, which matches every
    # section on this task.
    words = [w for w in hint.split() if w not in _STOPWORDS]
    if not words:
        return text
    # The leading token of a subtask id is the operation — `bend`, `flare`,
    # `deburr` — and it is the only part that discriminates between sections.
    # The rest is the object, and on this task every section is about the same
    # object: "line" matches "Determine the Line Route" exactly as well as
    # "bend" matches "Bending the Tubing", so an unweighted count ties and the
    # earlier section wins by position. Weighting the verb resolves all seven
    # AM.I.D.S1 subtasks to their own section.
    best, score = None, 0
    for section in sections[1:]:
        heading = section.splitlines()[0].lower() if section.splitlines() else ""
        if _is_boilerplate_heading(heading, record.task_code):
            continue
        tokens = re.findall(r"[a-z]+", heading)
        # Prefix matching so "bend" reaches "Bending" and "flare" reaches "Flaring".
        hits = sum((10 if index == 0 else 1)
                   for index, w in enumerate(words)
                   if any(t.startswith(w[:4]) for t in tokens))
        if hits > score:
            best, score = section, hits
    return f"## {best}" if best else text


def _is_boilerplate_heading(heading: str, task_code: str) -> bool:
    """Headings that name no gradeable operation.

    The document title repeats the task name — "…Fabricate a Rigid Line with a
    Flare and a Bend" — so it matches nearly every subtask hint and, being first,
    would beat the real section on any tie. The other two are the standard
    preamble that `packs/criteria_sources.py` already excludes by the same names.
    """
    if task_code.lower() in heading or heading.startswith("#"):
        return True
    return heading.strip() in {"before you begin", "safety and equipment",
                               "safety & equipment", "procedure", "procedures"}
