"""Detect the git repo context for the current workspace.

This runs once at startup and gives the agent (and the user) a clear picture
of what repo they are working on before any task begins.
"""

import subprocess
from dataclasses import dataclass
from pathlib import Path


@dataclass
class RepoContext:
    is_git: bool
    root: str = ""          # absolute path of the repo root
    remote_url: str = ""    # e.g. git@github.com:acme/shop.git
    remote_name: str = ""   # usually "origin"
    branch: str = ""        # current branch name
    last_commit: str = ""   # short hash + subject
    is_dirty: bool = False  # uncommitted changes present
    ahead: int = 0          # commits ahead of remote tracking branch
    behind: int = 0         # commits behind

    @property
    def repo_name(self) -> str:
        """Best-guess human name: owner/repo or just folder name."""
        if self.remote_url:
            name = self.remote_url.rstrip("/").removesuffix(".git")
            return name.split(":")[-1].split("/")[-2] + "/" + name.split("/")[-1]
        return Path(self.root).name if self.root else ""

    def as_prompt_block(self) -> str:
        """One-shot context block prepended to the agent's first message."""
        if not self.is_git:
            return (
                "WORKSPACE CONTEXT\n"
                f"  Path   : {self.root or 'unknown'}\n"
                "  Git    : not a git repository\n"
            )
        lines = [
            "WORKSPACE CONTEXT",
            f"  Repo   : {self.repo_name}",
            f"  Path   : {self.root}",
            f"  Remote : {self.remote_url or 'none'}",
            f"  Branch : {self.branch}",
            f"  Commit : {self.last_commit}",
            f"  Status : {'dirty (uncommitted changes)' if self.is_dirty else 'clean'}",
        ]
        if self.ahead or self.behind:
            lines.append(f"  Sync   : {self.ahead} ahead, {self.behind} behind remote")
        return "\n".join(lines)


def _run(args: list[str], cwd: Path) -> str:
    try:
        result = subprocess.run(
            args, cwd=cwd, capture_output=True, text=True, timeout=8
        )
        return result.stdout.strip()
    except Exception:
        return ""


def detect(workspace: Path) -> RepoContext:
    root = _run(["git", "rev-parse", "--show-toplevel"], cwd=workspace)
    if not root:
        return RepoContext(is_git=False, root=str(workspace))

    branch = _run(["git", "rev-parse", "--abbrev-ref", "HEAD"], cwd=workspace)
    last_commit = _run(["git", "log", "-1", "--format=%h %s"], cwd=workspace)
    is_dirty = bool(_run(["git", "status", "--porcelain"], cwd=workspace))

    remote_name = _run(["git", "remote"], cwd=workspace).splitlines()[0] if \
        _run(["git", "remote"], cwd=workspace) else ""
    remote_url = _run(["git", "remote", "get-url", remote_name], cwd=workspace) \
        if remote_name else ""

    ahead = behind = 0
    tracking = _run(
        ["git", "rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}"],
        cwd=workspace,
    )
    if tracking and "no upstream" not in tracking:
        ab = _run(
            ["git", "rev-list", "--left-right", "--count", f"HEAD...{tracking}"],
            cwd=workspace,
        )
        parts = ab.split()
        if len(parts) == 2:
            ahead, behind = int(parts[0]), int(parts[1])

    return RepoContext(
        is_git=True,
        root=root,
        remote_url=remote_url,
        remote_name=remote_name,
        branch=branch,
        last_commit=last_commit,
        is_dirty=is_dirty,
        ahead=ahead,
        behind=behind,
    )
