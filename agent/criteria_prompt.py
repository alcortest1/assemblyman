"""Loads the drafted grading rubrics into the realtime agent's system prompt.

`alcor_agents/criteria/` holds one rubric per *subtask* — the conditions a
photograph of that finished subtask must satisfy, and the defects that condemn
it outright. Those files are what `alcor_agents/inspector` grades against
offline, one photo at a time. This module puts the same rubrics in front of the
live agent so the operator can ask for a verdict while the work is still on the
bench.

The whole corpus is loaded, all eleven task codes, because the agent is not told
in advance which task the operator is doing. That is a deliberate trade: roughly
seventeen thousand tokens of rubric sit in every session's system prompt, paid
for in setup latency and per-session cost, in exchange for needing no
configuration to grade anything in the pilot.

The directory is gitignored — it is regenerated from `data/` by
`packs/generate_criteria.py`, so it does not exist on a fresh clone. Every
failure here is therefore soft: no criteria means an agent that still joins the
room, still sees and answers, and simply declines to grade. An assistant that
refuses to start because a generated directory is missing would be a worse
outcome than one that cannot mark work.
"""

from __future__ import annotations

import logging
import os
import re
from pathlib import Path
from typing import NamedTuple

logger = logging.getLogger("assemblyman-criteria")

# agent/ sits beside alcor_agents/ at the repo root.
DEFAULT_DIR = Path(__file__).resolve().parent.parent / "alcor_agents" / "criteria"

CRITERIA_DIR = Path(os.getenv("ASSEMBLYMAN_CRITERIA_DIR", str(DEFAULT_DIR)))

# `Source basis` is 27% of the corpus and pure provenance — the procedure section
# and handbook page each condition rests on. It is kept by default because
# naming the standard is worth more to a student than the tokens cost, but it is
# the first thing to drop if the prompt needs to get smaller.
INCLUDE_SOURCES = os.getenv("ASSEMBLYMAN_CRITERIA_SOURCES", "1") == "1"

# Only the task-code directories hold subtask rubrics. `atoms/`,
# `generated_criteria/` and `results/` are other artifacts of the same pipeline
# at other grains, and sweeping them in would multiply the prompt several times
# over with material at the wrong grain for a single photograph.
TASK_DIR_RE = re.compile(r"^AM\.[IVX]+\.[A-Z]+\.S\d+$")

RULE = "=" * 78

# Repeated verbatim at the top of all 41 files. Said once, in the preamble.
DISCLAIMER = re.compile(
    r"Machine-drafted from the procedure sheet and FAA handbook\.\s*Not reviewed by a\s*"
    r"subject-matter expert\.",
    re.IGNORECASE,
)


class Rubric(NamedTuple):
    task_code: str
    task_title: str
    subtask_code: str
    subtask: str
    body: str
    # Pulled out of `body` so a grade can be reported one condition at a time.
    # The overlay shows which conditions passed and which failed, and that is
    # only possible if the conditions are addressable individually rather than
    # as a block of prose.
    criteria: tuple[str, ...]
    critical_defects: tuple[str, ...]
    subject: str

    @property
    def key(self) -> str:
        return f"{self.task_code}/{self.subtask_code}"


class Corpus(NamedTuple):
    text: str
    tasks: int
    items: tuple[Rubric, ...]
    words: int

    @property
    def rubrics(self) -> int:
        return len(self.items)

    @property
    def empty(self) -> bool:
        return not self.items

    def find(self, task_code: str, subtask_code: str) -> Rubric | None:
        """Look up one rubric, tolerantly.

        The realtime model is choosing these codes out of a 15k-token prompt and
        will occasionally return a near-miss — right subtask, wrong case, or the
        task code omitted because only one task is in play. A lookup that only
        accepted exact pairs would turn a recoverable slip into "no rubric for
        that", which the operator hears as the grader refusing to work.
        """
        task = (task_code or "").strip().upper()
        subtask = (subtask_code or "").strip().lower().replace(" ", "_")
        if not subtask:
            return None
        for item in self.items:
            if item.task_code.upper() == task and item.subtask_code.lower() == subtask:
                return item
        matches = [i for i in self.items if i.subtask_code.lower() == subtask]
        return matches[0] if len(matches) == 1 else None


def _header_field(text: str, label: str) -> str:
    match = re.search(rf"^{label}:\s*(.+)$", text, re.MULTILINE)
    return match.group(1).strip() if match else ""


# The rubric's own section headings, each on a line of its own. A section runs
# until the next one of these — not until the next unindented line, because every
# criterion is itself unindented.
HEADINGS = ("Criteria", "Critical defects", "Overall decision", "Source basis")


def _section(body: str, heading: str) -> str:
    """The lines under `heading`, up to the next known heading or the end."""
    others = "|".join(re.escape(h) for h in HEADINGS if h != heading)
    match = re.search(
        rf"^{re.escape(heading)}\s*$\n(.*?)(?=^(?:{others})\s*$|\Z)",
        body,
        re.MULTILINE | re.DOTALL,
    )
    return match.group(1) if match else ""


def _numbered(section: str) -> tuple[str, ...]:
    """"1. condition" lines, in order."""
    return tuple(
        line.strip()
        for line in re.findall(r"^\s*\d+\.\s*(.+?)\s*$", section, re.MULTILINE)
        if line.strip()
    )


def _bulleted(section: str) -> tuple[str, ...]:
    return tuple(
        line.strip()
        for line in re.findall(r"^\s*[-•]\s*(.+?)\s*$", section, re.MULTILINE)
        if line.strip()
    )


# "Assess the completed rivet layout marked out on ... visible in the image."
SUBJECT_RE = re.compile(
    r"Assess the completed\s+(.+?)\s+visible in the image", re.IGNORECASE | re.DOTALL
)


def _parse(path: Path) -> Rubric | None:
    """Split one rubric file into its header fields and its gradeable body."""
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError as error:
        logger.warning("could not read %s: %s", path.name, error)
        return None

    task_code = _header_field(raw, "TASK CODE")
    subtask_code = _header_field(raw, "SUBTASK CODE")
    if not task_code or not subtask_code:
        logger.warning("skipping %s: no TASK CODE / SUBTASK CODE header", path.name)
        return None

    # The body is everything past the rule. Falling back to the whole file would
    # push the header block into the prompt twice, since it is restated below.
    _, _, body = raw.partition(RULE)
    body = body or raw

    # First line after the rule is "<subtask_code> — VLM GRADING CRITERIA", which
    # the section heading below already says.
    body = re.sub(r"^\s*\S+\s+—\s+VLM GRADING CRITERIA\s*$", "", body, count=1,
                  flags=re.MULTILINE)

    if not INCLUDE_SOURCES:
        body = re.split(r"^Source basis\s*$", body, maxsplit=1, flags=re.MULTILINE)[0]

    body = DISCLAIMER.sub("", body)
    body = re.sub(r"\n{3,}", "\n\n", body).strip()
    if not body:
        return None

    subject = SUBJECT_RE.search(body)
    return Rubric(
        task_code=task_code,
        task_title=_header_field(raw, "TASK TITLE"),
        subtask_code=subtask_code,
        subtask=_header_field(raw, "SUBTASK"),
        body=body,
        criteria=_numbered(_section(body, "Criteria")),
        critical_defects=_bulleted(_section(body, "Critical defects")),
        subject=re.sub(r"\s+", " ", subject.group(1)).strip() if subject else "",
    )


PREAMBLE = """\
GRADING RUBRICS

The rubrics below are the acceptance criteria for every task in the pilot. They \
are machine-drafted from the campus procedure sheets and the FAA handbook, and \
have NOT been reviewed by a subject-matter expert — so a verdict you give from \
them is a first opinion for an instructor to confirm, never a final mark on a \
student's record. Say so if you are asked whether your grade is authoritative.

Each rubric names the finished article it applies to, the conditions that \
article must satisfy, and the critical defects that fail it outright.\
"""


def load(directory: Path | None = None) -> Corpus:
    """Read every subtask rubric into one prompt block.

    Returns an empty corpus rather than raising when the directory is absent —
    see the module docstring for why that is the right failure.
    """
    root = Path(directory) if directory else CRITERIA_DIR
    if not root.is_dir():
        logger.warning("no criteria directory at %s — grading disabled", root)
        return Corpus("", 0, (), 0)

    rubrics: list[Rubric] = []
    for task_dir in sorted(p for p in root.iterdir() if p.is_dir()):
        if not TASK_DIR_RE.match(task_dir.name):
            continue
        for path in sorted(task_dir.glob("*.txt")):
            parsed = _parse(path)
            if parsed:
                rubrics.append(parsed)

    if not rubrics:
        logger.warning("no rubrics found under %s — grading disabled", root)
        return Corpus("", 0, (), 0)

    by_task: dict[str, list[Rubric]] = {}
    for rubric in rubrics:
        by_task.setdefault(rubric.task_code, []).append(rubric)

    parts = [PREAMBLE]
    for task_code, group in by_task.items():
        title = next((r.task_title for r in group if r.task_title), task_code)
        parts.append(f"\n{RULE}\nTASK {task_code} — {title}\n{RULE}")
        for rubric in group:
            heading = rubric.subtask or rubric.subtask_code
            parts.append(f"\n--- {task_code} / {rubric.subtask_code} — {heading} ---\n\n"
                         f"{rubric.body}")

    text = "\n".join(parts)
    return Corpus(text=text, tasks=len(by_task), items=tuple(rubrics),
                  words=len(text.split()))


if __name__ == "__main__":  # quick check: python criteria_prompt.py
    corpus = load()
    print(f"{corpus.rubrics} rubrics across {corpus.tasks} tasks, "
          f"{corpus.words} words (~{corpus.words * 4 // 3} tokens)")
    print(corpus.text[:2000])
