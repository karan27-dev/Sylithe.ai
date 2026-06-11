"""Sylithe's built-in skill set.

All filesystem skills are sandboxed to the configured workspace root; all git
execution goes through one audited entry point. Deploy/merge are wired through
the policy engine's hard rules (rollback plan, tests-before-merge, confirmation).
"""

import json
import shlex
import subprocess
from pathlib import Path

from sylithe.agent.policy import Risk
from sylithe.config import Settings
from sylithe.devops import conflicts as conflict_mod
from sylithe.memory.store import MemoryStore
from sylithe.skills.registry import SkillRegistry

_CONFLICT_MARKER = "<<<<<<< "


def _safe_path(workspace: Path, relative: str) -> Path:
    candidate = Path(relative)
    if not candidate.is_absolute():
        candidate = workspace / candidate
    candidate = candidate.resolve()
    if not candidate.is_relative_to(workspace.resolve()):
        raise PermissionError(
            f"'{relative}' escapes the workspace. Sylithe can only access files under "
            f"{workspace}. Use a path relative to the workspace root, and write any "
            f"intermediate output inside the workspace instead of /tmp."
        )
    return candidate


def _run(cmd: list[str], cwd: Path, timeout: int = 120) -> str:
    proc = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=timeout)
    out = (proc.stdout + proc.stderr).strip()
    return f"exit={proc.returncode}\n{out[:20000]}"


def build_registry(settings: Settings, memory: MemoryStore) -> SkillRegistry:
    registry = SkillRegistry()
    workspace = Path(settings.workspace_root).resolve()

    @registry.register(
        name="run_git",
        description="Run a git command in the workspace (e.g. 'status', 'diff --stat', 'log --oneline -10'). History-destroying operations are blocked by policy.",
        parameters={
            "type": "object",
            "properties": {"args": {"type": "string", "description": "Arguments after 'git'"}},
            "required": ["args"],
        },
        risk=Risk.REVERSIBLE,
    )
    def run_git(args: str) -> str:
        return _run(["git", *shlex.split(args)], cwd=workspace)

    @registry.register(
        name="read_file",
        description="Read a file from the workspace (path relative to workspace root).",
        parameters={
            "type": "object",
            "properties": {"path": {"type": "string"}},
            "required": ["path"],
        },
    )
    def read_file(path: str) -> str:
        return _safe_path(workspace, path).read_text()[:50000]

    @registry.register(
        name="list_dir",
        description="List files in a workspace directory.",
        parameters={
            "type": "object",
            "properties": {"path": {"type": "string", "default": "."}},
        },
    )
    def list_dir(path: str = ".") -> str:
        target = _safe_path(workspace, path)
        entries = sorted(p.name + ("/" if p.is_dir() else "") for p in target.iterdir())
        return "\n".join(entries) or "(empty)"

    @registry.register(
        name="detect_conflicts",
        description="Scan a file or directory for git merge-conflict markers and classify each conflict (formatting / dependency / test / logic) with a recommended strategy.",
        parameters={
            "type": "object",
            "properties": {"path": {"type": "string", "default": "."}},
        },
    )
    def detect_conflicts(path: str = ".") -> str:
        target = _safe_path(workspace, path)
        files = [target] if target.is_file() else [
            p for p in target.rglob("*") if p.is_file() and ".git" not in p.parts
        ]
        reports = []
        for f in files:
            try:
                content = f.read_text()
            except (UnicodeDecodeError, OSError):
                continue
            if _CONFLICT_MARKER not in content:
                continue
            report = conflict_mod.parse_conflicts(content, str(f.relative_to(workspace)))
            reports.append({
                "file": report.file_path,
                "auto_resolvable": report.auto_resolvable,
                "conflicts": [
                    {"type": c.type, "strategy": c.strategy, "rationale": c.rationale,
                     "ours": c.ours[:500], "theirs": c.theirs[:500]}
                    for c in report.conflicts
                ],
            })
        return json.dumps({"conflicted_files": reports, "count": len(reports)})

    @registry.register(
        name="resolve_conflicts",
        description="Resolve merge conflicts in one file using per-conflict classification, or force a uniform strategy ('prefer_ours'/'prefer_theirs'). Escalated conflicts are left in place. Always run tests afterwards.",
        parameters={
            "type": "object",
            "properties": {
                "path": {"type": "string"},
                "strategy": {"type": "string", "enum": ["auto", "prefer_ours", "prefer_theirs"], "default": "auto"},
            },
            "required": ["path"],
        },
        risk=Risk.REVERSIBLE,
    )
    def resolve_conflicts(path: str, strategy: str = "auto") -> str:
        target = _safe_path(workspace, path)
        content = target.read_text()
        override = None if strategy == "auto" else conflict_mod.Strategy(strategy)
        resolved, report = conflict_mod.resolve(content, path, override)
        target.write_text(resolved)
        remaining = sum(1 for c in report.conflicts
                        if override is None and c.strategy is conflict_mod.Strategy.ESCALATE)
        return json.dumps({
            "resolved": len(report.conflicts) - remaining,
            "escalated_remaining": remaining,
            "details": [{"type": c.type, "strategy": (override or c.strategy), "rationale": c.rationale}
                        for c in report.conflicts],
        })

    @registry.register(
        name="run_tests",
        description="Run the project's test command in the workspace and report the result.",
        parameters={
            "type": "object",
            "properties": {"command": {"type": "string", "default": "pytest -q"}},
        },
        risk=Risk.REVERSIBLE,
    )
    def run_tests(command: str = "pytest -q", _context: dict | None = None) -> str:
        result = _run(shlex.split(command), cwd=workspace, timeout=600)
        if _context is not None:
            _context["tests_passed"] = result.startswith("exit=0")
        return result

    @registry.register(
        name="merge_branch",
        description="Merge a source branch into a target branch. Merging into main/master is blocked unless tests passed in this session.",
        parameters={
            "type": "object",
            "properties": {"source": {"type": "string"}, "target": {"type": "string"}},
            "required": ["source", "target"],
        },
        risk=Risk.REVERSIBLE,
    )
    def merge_branch(source: str, target: str) -> str:
        checkout = _run(["git", "checkout", target], cwd=workspace)
        if not checkout.startswith("exit=0"):
            return checkout
        return _run(["git", "merge", "--no-ff", source], cwd=workspace)

    @registry.register(
        name="deploy",
        description="Deploy a service to an environment. Production deploys require a rollback_plan and operator confirmation.",
        parameters={
            "type": "object",
            "properties": {
                "service": {"type": "string"},
                "environment": {"type": "string", "enum": ["staging", "production"]},
                "rollback_plan": {"type": "string", "description": "Concrete rollback procedure"},
            },
            "required": ["service", "environment"],
        },
        risk=Risk.DESTRUCTIVE,
    )
    def deploy(service: str, environment: str, rollback_plan: str = "") -> str:
        # Integration point: wire to ArgoCD / Helm / your CD system here.
        return json.dumps({
            "status": "deploy_requested",
            "service": service,
            "environment": environment,
            "rollback_plan": rollback_plan,
            "note": "No CD backend configured; request recorded to audit log only.",
        })

    @registry.register(
        name="query_memory",
        description="Search Sylithe's pattern memory for similar past situations before acting.",
        parameters={
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "repo": {"type": "string", "default": ""},
            },
            "required": ["query"],
        },
    )
    def query_memory(query: str, repo: str = "") -> str:
        results = memory.query_patterns(query, repo=repo)
        return json.dumps([
            {"trigger": sp.pattern.trigger, "action_taken": sp.pattern.action_taken,
             "outcome": sp.pattern.outcome, "confidence": sp.pattern.confidence_score,
             "score": sp.score}
            for sp in results
        ])

    @registry.register(
        name="record_pattern",
        description="Record a learned pattern to memory: what triggered it, what was done, and how it turned out.",
        parameters={
            "type": "object",
            "properties": {
                "trigger": {"type": "string"},
                "context": {"type": "string"},
                "action_taken": {"type": "string"},
                "outcome": {"type": "string", "enum": ["success", "failure", "partial"]},
                "confidence_score": {"type": "number", "minimum": 0, "maximum": 1},
                "repo": {"type": "string", "default": ""},
            },
            "required": ["trigger", "action_taken", "outcome"],
        },
    )
    def record_pattern(trigger: str, action_taken: str, outcome: str, context="",
                       confidence_score=0.5, repo: str = "") -> str:
        # Models sometimes send numbers as strings or context as an object;
        # coerce rather than fail the whole learning step.
        if not isinstance(context, str):
            context = json.dumps(context, default=str)
        memory.record_pattern(trigger=trigger, context=context, action_taken=action_taken,
                              outcome=outcome, confidence_score=float(confidence_score),
                              repo=repo)
        return json.dumps({"recorded": True})

    return registry
