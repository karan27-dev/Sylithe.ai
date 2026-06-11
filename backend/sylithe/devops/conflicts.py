"""Merge-conflict parsing, classification, and resolution.

Implements the spec's conflict-resolution algorithm:
  formatting-only  -> auto-resolve, prefer HEAD
  dependency files -> escalate, never auto-resolve
  test files       -> prefer incoming
  logic divergence -> escalate with both sides shown
"""

import re
from dataclasses import dataclass, field
from enum import Enum
from pathlib import PurePosixPath

_CONFLICT_RE = re.compile(
    r"<<<<<<< (?P<ours_label>.*?)\n(?P<ours>.*?)^=======\n(?P<theirs>.*?)^>>>>>>> (?P<theirs_label>.*?)$",
    re.DOTALL | re.MULTILINE,
)

_DEPENDENCY_FILES = {
    "package.json", "package-lock.json", "yarn.lock", "pnpm-lock.yaml",
    "requirements.txt", "pyproject.toml", "poetry.lock", "uv.lock", "pipfile.lock",
    "go.mod", "go.sum", "cargo.toml", "cargo.lock", "gemfile.lock", "pom.xml",
    "build.gradle", "composer.lock",
}

_TEST_PATH_RE = re.compile(r"(^|/)(tests?|__tests__|spec)(/|$)|(_test\.|\.test\.|\.spec\.|test_)")


class ConflictType(str, Enum):
    FORMATTING = "formatting"
    DEPENDENCY = "dependency"
    TEST = "test"
    LOGIC = "logic"


class Strategy(str, Enum):
    PREFER_OURS = "prefer_ours"
    PREFER_THEIRS = "prefer_theirs"
    ESCALATE = "escalate"


@dataclass
class Conflict:
    file_path: str
    ours: str
    theirs: str
    ours_label: str
    theirs_label: str
    type: ConflictType = ConflictType.LOGIC
    strategy: Strategy = Strategy.ESCALATE
    rationale: str = ""


@dataclass
class ConflictReport:
    file_path: str
    conflicts: list[Conflict] = field(default_factory=list)

    @property
    def auto_resolvable(self) -> bool:
        return bool(self.conflicts) and all(
            c.strategy is not Strategy.ESCALATE for c in self.conflicts
        )


def _normalize(code: str) -> str:
    """Strip all whitespace inside lines so formatting-only differences compare equal."""
    lines = [re.sub(r"\s+", "", ln) for ln in code.splitlines()]
    return "\n".join(ln for ln in lines if ln)


def parse_conflicts(content: str, file_path: str) -> ConflictReport:
    report = ConflictReport(file_path=file_path)
    for m in _CONFLICT_RE.finditer(content):
        conflict = Conflict(
            file_path=file_path,
            ours=m.group("ours"),
            theirs=m.group("theirs"),
            ours_label=m.group("ours_label").strip(),
            theirs_label=m.group("theirs_label").strip(),
        )
        classify(conflict)
        report.conflicts.append(conflict)
    return report


def classify(conflict: Conflict) -> Conflict:
    name = PurePosixPath(conflict.file_path).name.lower()
    path = conflict.file_path.replace("\\", "/").lower()

    if name in _DEPENDENCY_FILES:
        conflict.type = ConflictType.DEPENDENCY
        conflict.strategy = Strategy.ESCALATE
        conflict.rationale = "Dependency/lockfile conflicts are never auto-resolved."
    elif _normalize(conflict.ours) == _normalize(conflict.theirs):
        conflict.type = ConflictType.FORMATTING
        conflict.strategy = Strategy.PREFER_OURS
        conflict.rationale = "Both sides are semantically identical; formatting-only difference."
    elif _TEST_PATH_RE.search(path):
        conflict.type = ConflictType.TEST
        conflict.strategy = Strategy.PREFER_THEIRS
        conflict.rationale = "Test-file conflict: prefer incoming changes, then re-run the suite."
    else:
        conflict.type = ConflictType.LOGIC
        conflict.strategy = Strategy.ESCALATE
        conflict.rationale = "Logic divergence: both sides shown; human review recommended."
    return conflict


def resolve(content: str, file_path: str, override: Strategy | None = None) -> tuple[str, ConflictReport]:
    """Apply per-conflict strategies (or a uniform override) to conflicted content.

    Conflicts whose strategy is ESCALATE are left untouched unless an override
    is supplied — partial resolution keeps the file honest about what remains.
    """
    report = ConflictReport(file_path=file_path)

    def _sub(m: re.Match) -> str:
        conflict = Conflict(
            file_path=file_path,
            ours=m.group("ours"),
            theirs=m.group("theirs"),
            ours_label=m.group("ours_label").strip(),
            theirs_label=m.group("theirs_label").strip(),
        )
        classify(conflict)
        strategy = override or conflict.strategy
        report.conflicts.append(conflict)
        if strategy is Strategy.PREFER_OURS:
            return conflict.ours
        if strategy is Strategy.PREFER_THEIRS:
            return conflict.theirs
        return m.group(0)

    resolved = _CONFLICT_RE.sub(_sub, content)
    return resolved, report
