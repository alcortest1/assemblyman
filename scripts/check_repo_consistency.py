#!/usr/bin/env python3
# Copyright (c) Meta Platforms, Inc. and affiliates.
# All rights reserved.
#
# This source code is licensed under the license found in the
# LICENSE file in the root directory of this source tree.

"""Repo-level invariants that nothing else in the tree enforces.

Two classes of drift are checked:

1. **Knowledge-base surface sync.** Every ``SKILL.md`` body is duplicated verbatim into
   four derived surfaces (``AGENTS.md``, ``.github/copilot-instructions.md``, and the
   Cursor ``.mdc`` rules). Editing a skill without regenerating the surfaces leaves them
   silently divergent — see ``docs/system.md`` §5.

2. **Sample-app name coherence.** A sample app's name appears in its directory layout, its
   scheme, its bundle metadata and its project file. A partial rename still builds, so the
   mismatch only surfaces at runtime.

Run: ``python3 scripts/check_repo_consistency.py``  (exits non-zero on failure)
"""

from __future__ import annotations

import json
import plistlib
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SKILLS_DIR = REPO / "plugins" / "mwdat-ios" / "skills"
CURSOR_RULES = REPO / ".cursor" / "rules"
INLINE_SURFACES = [REPO / "AGENTS.md", REPO / ".github" / "copilot-instructions.md"]
SAMPLES_DIR = REPO / "samples"

# Text files worth scanning for a stale sibling name. Binary assets and Xcode user state
# are excluded; the latter is not committed.
SCANNED_SUFFIXES = {
    ".swift", ".md", ".json", ".plist", ".pbxproj", ".xcscheme", ".entitlements", ".resolved",
}

# Surfaces that were already out of sync with their ``SKILL.md`` before this check existed
# — the ``.mdc`` rules in particular carry guidance the skill sources never received. These
# are reported as warnings so the check is actionable today; fix a pair and delete its entry.
# Anything not listed here is a hard failure.
KNOWN_DIVERGENCE = {
    ("dat-conventions", ".cursor/rules/dat-conventions.mdc"),
    ("display-access", ".cursor/rules/display-access.mdc"),
    ("display-access", "AGENTS.md"),
    ("display-access", ".github/copilot-instructions.md"),
    ("getting-started", ".cursor/rules/getting-started.mdc"),
    ("getting-started", ".github/copilot-instructions.md"),
    ("mockdevice-testing", "AGENTS.md"),
    ("mockdevice-testing", ".github/copilot-instructions.md"),
    ("permissions-registration", "AGENTS.md"),
    ("permissions-registration", ".github/copilot-instructions.md"),
    ("sample-app-guide", "AGENTS.md"),
    ("sample-app-guide", ".github/copilot-instructions.md"),
    ("session-lifecycle", "AGENTS.md"),
    ("session-lifecycle", ".github/copilot-instructions.md"),
}

failures: list[str] = []
warnings: list[str] = []
observed_divergence: set[tuple[str, str]] = set()


def fail(message: str) -> None:
    failures.append(message)


def report_divergence(topic: str, surface: Path, skill: Path) -> None:
    """Record a surface that no longer matches its skill source."""
    key = (topic, str(surface.relative_to(REPO)))
    observed_divergence.add(key)
    message = (
        f"{topic}: {surface.relative_to(REPO)} has diverged from "
        f"{skill.relative_to(REPO)} — regenerate it"
    )
    (warnings if key in KNOWN_DIVERGENCE else failures).append(message)


def body_of(markdown: Path) -> str:
    """Return the document with its leading YAML front matter stripped."""
    text = markdown.read_text(encoding="utf-8")
    match = re.match(r"^---\n.*?\n---\n", text, re.DOTALL)
    return text[match.end():].strip() if match else text.strip()


def body_after_title(text: str) -> str:
    """Drop a leading ``# Title`` heading.

    The inline surfaces re-level each skill's H1 into their own section structure
    (``# Debugging (iOS)`` becomes ``## Debugging`` in ``AGENTS.md``); everything below
    the title is inlined verbatim.
    """
    lines = text.splitlines()
    if lines and lines[0].startswith("# "):
        lines = lines[1:]
    return "\n".join(lines).strip()


def check_surface_sync() -> None:
    skills = sorted(p for p in SKILLS_DIR.glob("*/SKILL.md"))
    if not skills:
        fail(f"no SKILL.md files found under {SKILLS_DIR.relative_to(REPO)}")
        return

    inline = {path: path.read_text(encoding="utf-8") for path in INLINE_SURFACES}

    for skill in skills:
        topic = skill.parent.name
        source = body_of(skill)

        rule = CURSOR_RULES / f"{topic}.mdc"
        if not rule.exists():
            fail(f"{topic}: missing Cursor rule {rule.relative_to(REPO)}")
        elif body_of(rule) != source:
            report_divergence(topic, rule, skill)

        inlined = body_after_title(source)
        for path, content in inline.items():
            if inlined not in content:
                report_divergence(topic, path, skill)

    stale = KNOWN_DIVERGENCE - observed_divergence
    for topic, surface in sorted(stale):
        fail(
            f"{topic}: {surface} is now in sync — remove it from KNOWN_DIVERGENCE in "
            f"{Path(__file__).name}"
        )


def declared_url_schemes(info: dict) -> list[str]:
    return [
        scheme
        for entry in info.get("CFBundleURLTypes", [])
        for scheme in entry.get("CFBundleURLSchemes", [])
    ]


def check_sample(sample: Path, sibling_names: list[str]) -> None:
    name = sample.name

    expected = [
        sample / f"{name}.xcodeproj",
        sample / name,
        sample / f"{name}.xcodeproj/xcshareddata/xcschemes/{name}.xcscheme",
        sample / name / "Info.plist",
    ]
    for path in expected:
        if not path.exists():
            fail(f"{name}: expected {path.relative_to(REPO)} to exist")

    info_plist = sample / name / "Info.plist"
    if info_plist.exists():
        with info_plist.open("rb") as handle:
            info = plistlib.load(handle)

        if info.get("CFBundleName") != name:
            fail(
                f"{name}: CFBundleName is {info.get('CFBundleName')!r}, "
                f"expected {name!r}"
            )

        # AppLinkURLScheme is how Meta AI calls the app back; iOS only routes it if the
        # same scheme is registered under CFBundleURLTypes.
        app_link = info.get("MWDAT", {}).get("AppLinkURLScheme")
        if not app_link:
            fail(f"{name}: Info.plist has no MWDAT.AppLinkURLScheme")
        else:
            scheme = app_link.removesuffix("://")
            if scheme not in declared_url_schemes(info):
                fail(
                    f"{name}: MWDAT.AppLinkURLScheme {scheme!r} is not declared in "
                    f"CFBundleURLTypes — registration callbacks will not arrive"
                )

    # Asset catalogs reference their image files by name from Contents.json. Renaming the
    # file without updating the manifest (or vice versa) still compiles; the image just
    # never draws.
    for contents in sorted(sample.rglob("Assets.xcassets/**/Contents.json")):
        try:
            manifest = json.loads(contents.read_text(encoding="utf-8"))
        except json.JSONDecodeError as error:
            fail(f"{name}: {contents.relative_to(REPO)} is not valid JSON ({error})")
            continue
        for entry in manifest.get("images", []) + manifest.get("colors", []):
            filename = entry.get("filename")
            if filename and not (contents.parent / filename).exists():
                fail(
                    f"{name}: {contents.relative_to(REPO)} references missing asset file "
                    f"{filename!r}"
                )

    # A leftover sibling name inside a sample is the signature of a partial rename.
    for path in sorted(sample.rglob("*")):
        if not path.is_file() or path.suffix not in SCANNED_SUFFIXES:
            continue
        if "xcuserdata" in path.parts:
            continue
        try:
            content = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        for sibling in sibling_names:
            if re.search(sibling, content, re.IGNORECASE):
                fail(
                    f"{name}: {path.relative_to(REPO)} still references the other sample "
                    f"app {sibling!r}"
                )


# Ways prose and shell snippets name a sample app. Any name captured here must correspond
# to a real directory under ``samples/``.
SAMPLE_REFERENCE_PATTERNS = [
    re.compile(r"([A-Za-z0-9_]+)\.xcodeproj"),
    re.compile(r"-scheme\s+([A-Za-z0-9_]+)"),
    re.compile(r"samples/([A-Za-z0-9_]+)"),
]

# Schemes that belong to the SDK packages rather than to a sample app.
NON_SAMPLE_SCHEMES = re.compile(r"^MWDAT")

DOC_GLOBS = ["*.md", ".github/*.md", ".cursor/rules/*.mdc", "docs/*.md", "plugins/**/*.md"]


def check_sample_references() -> None:
    """Every sample app named in documentation must actually exist.

    This is deliberately independent of the surface-sync check: a stale name in a surface
    that is already baselined as divergent would otherwise go unnoticed, and a renamed
    sample leaves exactly that kind of dangling reference behind.
    """
    known = {p.name for p in SAMPLES_DIR.iterdir() if p.is_dir()}

    docs = sorted({path for glob in DOC_GLOBS for path in REPO.glob(glob) if path.is_file()})
    for doc in docs:
        content = doc.read_text(encoding="utf-8")
        referenced = {name for p in SAMPLE_REFERENCE_PATTERNS for name in p.findall(content)}
        for name in sorted(referenced - known):
            if NON_SAMPLE_SCHEMES.match(name):
                continue
            fail(
                f"{doc.relative_to(REPO)} references sample app {name!r}, which does "
                f"not exist under samples/ (known: {', '.join(sorted(known))})"
            )


def check_samples() -> None:
    samples = sorted(p for p in SAMPLES_DIR.iterdir() if p.is_dir())
    if not samples:
        fail(f"no sample apps found under {SAMPLES_DIR.relative_to(REPO)}")
        return
    for sample in samples:
        check_sample(sample, [other.name for other in samples if other != sample])


def main() -> int:
    check_surface_sync()
    check_sample_references()
    check_samples()

    if warnings:
        print(f"{len(warnings)} known surface divergence(s), pre-dating this check:\n")
        for warning in warnings:
            print(f"  ! {warning}")
        print()

    if failures:
        print(f"{len(failures)} consistency failure(s):\n", file=sys.stderr)
        for failure in failures:
            print(f"  - {failure}", file=sys.stderr)
        return 1

    print("Repo consistency checks passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
