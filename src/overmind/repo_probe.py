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

⚠️ A MARKER FILE'S BARE PRESENCE IS NOT ENOUGH TO INFER A WORKING CHECK. PM
finding, 2026-09-19, OverMind's first real dispatch job (community#67): a
`package.json` matched the `npm test` marker, but the repo is a pnpm
workspace whose ROOT `package.json` carries no `"test"` script at all - every
package's own tests live one level down. The model (nvidia/z-ai/glm-5.3)
still did real, correct work in ~22 minutes / 20 steps, but the run could
never have verified PASS, because the inferred check could never have
succeeded regardless of what the model did. Each marker below now VALIDATES
its own content before `infer_check` returns a check for it - see each
`_resolve_*` function's own docstring for what "valid" means for that marker.
A marker that's present but fails validation refuses (with the reason
recorded, never silently) - it does NOT fall through to a lower-priority
marker; the table's own "first marker present wins" tie-break still decides
WHICH marker is considered.
"""

from __future__ import annotations

import json
import pathlib
import tomllib
from dataclasses import dataclass
from typing import Callable

#: What a marker's own resolver returns: either the (argv, public name) to
#: check against, or a `str` reason the marker's content didn't validate.
_Resolution = "tuple[list[str], str] | str"


def _resolve_cargo_toml(root: pathlib.Path) -> _Resolution:
    """Valid TOML, with a `[package]` or `[workspace]` table - catches a
    stray or placeholder `Cargo.toml` that isn't really a Cargo project."""
    path = root / "Cargo.toml"
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8", errors="replace"))
    except (OSError, tomllib.TOMLDecodeError) as exc:
        return f"Cargo.toml present but could not be parsed as TOML: {exc}"
    if "package" not in data and "workspace" not in data:
        return "Cargo.toml present but has neither a [package] nor a [workspace] table"
    return (["cargo", "test"], "cargo test")


def _resolve_go_mod(root: pathlib.Path) -> _Resolution:
    """`go.mod`'s own presence is the marker Go itself uses to find the
    module root - no further sanity check is meaningful without parsing the
    module graph, which this module does not do."""
    return (["go", "test", "./..."], "go test ./...")


def _resolve_pyproject_toml(root: pathlib.Path) -> _Resolution:
    """Valid TOML, with either a `tests/` directory at the repo root or a
    `[tool.pytest...]` config section - either is real evidence `pytest` has
    something to actually run here."""
    path = root / "pyproject.toml"
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8", errors="replace"))
    except (OSError, tomllib.TOMLDecodeError) as exc:
        return f"pyproject.toml present but could not be parsed as TOML: {exc}"
    tool = data.get("tool") if isinstance(data, dict) else None
    has_pytest_config = isinstance(tool, dict) and "pytest" in tool
    has_tests_dir = (root / "tests").is_dir()
    if not (has_pytest_config or has_tests_dir):
        return ('pyproject.toml present but neither a "tests/" directory nor a '
                '[tool.pytest...] config section was found')
    return (["python", "-m", "pytest"], "python -m pytest")


def _resolve_setup_py(root: pathlib.Path) -> _Resolution:
    """The legacy `setup.py` marker: sanity-checked the same way as
    `pyproject.toml` - a `tests/` directory must actually exist."""
    if not (root / "tests").is_dir():
        return 'setup.py present but no "tests/" directory was found'
    return (["python", "-m", "pytest"], "python -m pytest")


def _pnpm_workspace_globs(text: str) -> list[str]:
    """The glob entries under a `packages:` block in a `pnpm-workspace.yaml`.
    ⚠️ A NARROW, DELIBERATELY NON-GENERAL YAML READER - this module owns no
    YAML dependency (OverMind's core stays stdlib-only), and only needs to
    read the plain block-list form pnpm's own docs show
    (`packages:\\n  - "apps/*"\\n  - "packages/*"`), never a full YAML
    document."""
    globs: list[str] = []
    in_packages = False
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("packages:"):
            in_packages = True
            continue
        if not in_packages:
            continue
        if stripped.startswith("-"):
            item = stripped[1:].strip().strip("'\"")
            if item:
                globs.append(item)
        elif stripped and not stripped.startswith("#"):
            in_packages = False
    return globs


def _package_json_has_test_script(pkg: pathlib.Path) -> bool:
    try:
        data = json.loads(pkg.read_text(encoding="utf-8", errors="replace"))
    except (OSError, json.JSONDecodeError):
        return False
    scripts = data.get("scripts") if isinstance(data, dict) else None
    test_cmd = scripts.get("test") if isinstance(scripts, dict) else None
    return isinstance(test_cmd, str) and bool(test_cmd.strip())


def _pnpm_workspace_has_test_script(root: pathlib.Path, workspace_file: pathlib.Path) -> bool:
    text = workspace_file.read_text(encoding="utf-8", errors="replace")
    for glob in _pnpm_workspace_globs(text):
        for match in root.glob(glob):
            pkg = match / "package.json"
            if pkg.is_file() and _package_json_has_test_script(pkg):
                return True
    return False


def _resolve_package_json(root: pathlib.Path) -> _Resolution:
    """`npm test` only when the ROOT `package.json` itself declares a
    `"test"` script. If a `pnpm-workspace.yaml` is present (pnpm workspaces
    routinely carry their tests one level down, per each package's own
    `package.json`, never the root's), prefer `pnpm -r --if-present test`
    instead - but only when at least one workspace package under the
    workspace file's own `packages:` globs actually has a `"test"` script;
    otherwise refuse rather than run a check nothing there can satisfy."""
    pkg_path = root / "package.json"
    try:
        data = json.loads(pkg_path.read_text(encoding="utf-8", errors="replace"))
    except (OSError, json.JSONDecodeError) as exc:
        return f"package.json present but could not be parsed as JSON: {exc}"
    if not isinstance(data, dict):
        return "package.json present but its top level is not a JSON object"

    workspace_file = root / "pnpm-workspace.yaml"
    if workspace_file.is_file():
        if _pnpm_workspace_has_test_script(root, workspace_file):
            return (["pnpm", "-r", "--if-present", "test"], "pnpm -r --if-present test")
        return ('pnpm-workspace.yaml present but no workspace package under its '
                '"packages:" globs has a "test" script')

    scripts = data.get("scripts")
    test_cmd = scripts.get("test") if isinstance(scripts, dict) else None
    if isinstance(test_cmd, str) and test_cmd.strip():
        return (["npm", "test"], "npm test")
    return 'package.json present but has no "scripts.test" entry (and no pnpm-workspace.yaml)'


#: (marker file relative to repo root, resolver that validates that marker's
#: own content and returns either a (argv, public name) check or a `str`
#: refusal reason).
#: ⚠️ ORDER IS THE TIE-BREAK. Checked top to bottom; the first marker file
#: PRESENT is the only one considered - a repo with both `Cargo.toml` and
#: `package.json` (a Rust project with a JS-based doc site, say) is decided
#: by `Cargo.toml` alone, the table is authored most to least likely to be
#: the project's OWN build, not alphabetically - and if that marker's own
#: content fails validation, this refuses outright rather than trying a
#: lower-priority marker instead.
CHECK_TABLE: tuple[tuple[str, Callable[[pathlib.Path], _Resolution]], ...] = (
    ("Cargo.toml", _resolve_cargo_toml),
    ("go.mod", _resolve_go_mod),
    ("pyproject.toml", _resolve_pyproject_toml),
    ("setup.py", _resolve_setup_py),
    ("package.json", _resolve_package_json),
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


@dataclass
class CheckResult:
    """What `infer_check` found: either a validated `check`/`check_name`, or
    (when a marker matched but its own content didn't validate)
    `refused_reason` explaining why - never both, and `refused_reason` is
    `None` (not a generic message) when no marker was even present at all."""
    check: list[str] | None
    check_name: str | None
    refused_reason: str | None = None


def infer_check(root: pathlib.Path) -> CheckResult:
    """The check this task will be verified against, VALIDATED against the
    matched marker's own content before it's ever returned - see the module
    docstring for why a marker's bare presence stopped being enough. Never
    guesses past `CHECK_TABLE`, and never falls through to a lower-priority
    marker just because a higher-priority one failed validation."""
    for marker, resolve in CHECK_TABLE:
        if (root / marker).is_file():
            result = resolve(root)
            if isinstance(result, str):
                return CheckResult(check=None, check_name=None, refused_reason=result)
            check, name = result
            return CheckResult(check=check, check_name=name)
    return CheckResult(check=None, check_name=None)


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
    refused_reason: str | None = None

    @classmethod
    def run(cls, root: pathlib.Path) -> "RepoProbe":
        result = infer_check(root)
        return cls(check=result.check, check_name=result.check_name,
                   context=context_text(root), refused_reason=result.refused_reason)
