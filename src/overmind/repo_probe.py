# SPDX-License-Identifier: MIT OR Apache-2.0
"""What verifies a task, and what tells the model what "good" looks like -
kept as two separate questions, deliberately.

⚠️ THE CHECK IS CODE THE HARNESS EXECUTES. THE CONTEXT IS TEXT A MODEL READS.
Decided 2026-09-17 with CireSnave, for the MCP dispatch tool DESIGN-PROPOSAL.md
§7 was written against: a repo's own CI config, a caller's free-text
requirements, and a caller-supplied URL are all safe to fold into a task's
GOAL, because none of them become a subprocess argv - the model that reads
them is still confined by `WorkspaceConfined`, and the run is still verified
against a check this module chose, never one read out of the repo's own
content. The SAME CI config text is unsafe to extract a command from and
RUN, because it is content the repo's own author controls, and "the repo is
one we don't otherwise trust" is exactly the case an arbitrary dispatched
repo is in.

So: `infer_check` never reads a CI file. It matches a marker file present in
the repo (`Cargo.toml`, `pyproject.toml`, ...) against a SMALL, FIXED table
this module owns, and returns the one argv that marker maps to. `context_text`
does the opposite: it reads CI config and an in-repo standards file, verbatim,
labelled, truncated - never executed, never parsed for a command.
"""

from __future__ import annotations

import pathlib
from dataclasses import dataclass

#: (marker file relative to repo root, check argv, public check name).
#: ⚠️ ORDER IS THE TIE-BREAK. Checked top to bottom; the first marker present
#: wins. A repo with both `Cargo.toml` and `package.json` (a Rust project with
#: a JS-based doc site, say) gets `cargo test` - the table is authored most
#: to least likely to be the project's OWN build, not alphabetically.
CHECK_TABLE: tuple[tuple[str, tuple[str, ...], str], ...] = (
    ("Cargo.toml", ("cargo", "test"), "cargo test"),
    ("go.mod", ("go", "test", "./..."), "go test ./..."),
    ("pyproject.toml", ("python", "-m", "pytest"), "python -m pytest"),
    ("setup.py", ("python", "-m", "pytest"), "python -m pytest"),
    ("package.json", ("npm", "test"), "npm test"),
)

#: CI config paths worth quoting into the goal as a hint. Read-only, and never
#: the same list as `lanework.PROTECTED` by coincidence - that list exists so
#: a task can't WRITE there; this one exists so context can READ from there.
CI_HINT_PATHS: tuple[str, ...] = (
    ".github/workflows",
    ".gitlab-ci.yml",
    ".circleci/config.yml",
    ".travis.yml",
)

#: Where a repo states its own must/should/must-not/should-not standards, if
#: it has any. One path, not a search - a repo that wants this read puts it
#: here, the same way this portfolio's own root docs are at a known path.
STANDARDS_PATH = ".overmind/STANDARDS.md"

#: ⚠️ A CI TREE CAN HOLD THOUSANDS OF FILES; THIS IS CONTEXT, NOT AN ARCHIVE.
#: One repo's `.github/workflows` must not be able to blow the model's whole
#: context budget - cap what's read, per file and in total, and say when it
#: was cut so the truncation itself isn't mistaken for the whole story.
MAX_HINT_FILE_CHARS = 2000
MAX_HINT_TOTAL_CHARS = 6000


def infer_check(root: pathlib.Path) -> tuple[list[str], str] | None:
    """The check this task will be verified against, or None if no marker
    in `CHECK_TABLE` is present. Never guesses past that table."""
    for marker, argv, name in CHECK_TABLE:
        if (root / marker).is_file():
            return list(argv), name
    return None


def _read_capped(path: pathlib.Path, budget: int) -> str:
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    if len(text) > budget:
        return text[:budget] + f"\n... truncated at {budget} chars ..."
    return text


def _ci_hint_text(root: pathlib.Path, remaining: int) -> str:
    blocks: list[str] = []
    for rel in CI_HINT_PATHS:
        target = root / rel
        files: list[pathlib.Path] = []
        if target.is_file():
            files = [target]
        elif target.is_dir():
            files = sorted(p for p in target.rglob("*") if p.is_file())
        for f in files:
            if remaining <= 0:
                return "\n\n".join(blocks)
            per_file = min(MAX_HINT_FILE_CHARS, remaining)
            text = _read_capped(f, per_file)
            if not text.strip():
                continue
            label = f.relative_to(root).as_posix()
            blocks.append(f"--- CI config, informational only, not executed: {label} ---\n{text}")
            remaining -= len(text)
    return "\n\n".join(blocks)


def context_text(root: pathlib.Path) -> str:
    """CI config and this repo's own standards file, quoted verbatim for the
    model's prompt. ⚠️ NEVER PARSED FOR A COMMAND - see the module docstring."""
    sections: list[str] = []
    standards = _read_capped(root / STANDARDS_PATH, MAX_HINT_TOTAL_CHARS)
    if standards.strip():
        sections.append(f"--- This repo's own standards ({STANDARDS_PATH}) ---\n{standards}")
    remaining = MAX_HINT_TOTAL_CHARS - sum(len(s) for s in sections)
    ci = _ci_hint_text(root, remaining) if remaining > 0 else ""
    if ci:
        sections.append(ci)
    return "\n\n".join(sections)


@dataclass
class RepoProbe:
    """What `infer_check` and `context_text` found, together, so a caller
    reads one result instead of the two functions separately."""
    check: list[str] | None
    check_name: str | None
    context: str

    @classmethod
    def run(cls, root: pathlib.Path) -> "RepoProbe":
        found = infer_check(root)
        check, name = found if found else (None, None)
        return cls(check=check, check_name=name, context=context_text(root))
