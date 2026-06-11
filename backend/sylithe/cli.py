"""Sylithe CLI — run the DevOps agent in any project folder.

Usage:
    cd /path/to/your/project
    sylithe                      # interactive session on this folder
    sylithe "run tests and explain failures"   # one-shot task
    sylithe --yes "deploy api to staging"      # pre-approve destructive skills

The current directory becomes the agent's workspace. Pattern memory and the
audit log live in ~/.sylithe so the agent gets smarter across all projects.
"""

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path

from sylithe import __version__
from sylithe.agent.loop import AgentRunner, deepseek_client
from sylithe.audit import AuditLog
from sylithe.config import Settings
from sylithe.devops.proposals import apply_proposal, reject_proposal
from sylithe.devops.repo import RepoContext, detect as detect_repo
from sylithe.memory.store import MemoryStore
from sylithe.skills.builtin import build_registry

CONFIG_DIR = Path.home() / ".sylithe"
CONFIG_FILE = CONFIG_DIR / "config.env"

_DIM = "\033[2m"
_BOLD = "\033[1m"
_CYAN = "\033[36m"
_YELLOW = "\033[33m"
_RED = "\033[31m"
_GREEN = "\033[32m"
_RESET = "\033[0m"


def _c(code: str, text: str) -> str:
    return f"{code}{text}{_RESET}" if sys.stdout.isatty() else text


def _load_global_config() -> None:
    """~/.sylithe/config.env supplies defaults; real env vars win."""
    if not CONFIG_FILE.exists():
        return
    for line in CONFIG_FILE.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip())


def _ensure_api_key() -> None:
    if os.environ.get("DEEPSEEK_API_KEY"):
        return
    print(_c(_BOLD, "First run — Sylithe needs your DeepSeek API key."))
    print("Get one at https://platform.deepseek.com (it will be saved to "
          f"{CONFIG_FILE}, never to your project).")
    key = input("API key: ").strip()
    if not key:
        sys.exit("No key provided; aborting.")
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    existing = CONFIG_FILE.read_text() if CONFIG_FILE.exists() else ""
    CONFIG_FILE.write_text(existing + f"\nDEEPSEEK_API_KEY={key}\n")
    CONFIG_FILE.chmod(0o600)
    os.environ["DEEPSEEK_API_KEY"] = key
    print(_c(_GREEN, "Saved.") + "\n")


def _build_runner(workspace: Path, auto_yes: bool, model: str | None,
                  max_iter: int | None = None) -> AgentRunner:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    # Read ~/.sylithe/config.env so MAX_AGENT_ITERATIONS and other user
    # preferences are respected; CLI flags override env file values.
    env_file = str(CONFIG_FILE) if CONFIG_FILE.exists() else None
    settings = Settings(
        workspace_root=str(workspace),
        database_url=f"sqlite:///{CONFIG_DIR / 'sylithe.db'}",
        audit_log_path=str(CONFIG_DIR / "audit.log.jsonl"),
        _env_file=env_file,
    )
    if model:
        settings.sylithe_model = model
    if max_iter is not None:
        settings.max_agent_iterations = max_iter
    memory = MemoryStore(settings.database_url)

    def on_event(kind: str, data: dict) -> None:
        if kind == "skill_start":
            args = json.dumps(data["args"], default=str)
            if len(args) > 120:
                args = args[:120] + "…"
            print(_c(_DIM, f"  → {data['skill']} {args}"))
        elif kind == "skill_end" and not data["ok"]:
            detail = data.get("error") or str(data["verdict"])
            if len(detail) > 160:
                detail = detail[:160] + "…"
            print(_c(_YELLOW, f"  ✗ {data['skill']}: {detail}"))

    def confirm(skill: str, args: dict) -> bool:
        if auto_yes:
            return True
        print(_c(_RED, f"\n⚠ The agent wants to run destructive skill "
                       f"'{skill}' with {json.dumps(args, default=str)}"))
        answer = input("Allow? [y/N] ").strip().lower()
        return answer in ("y", "yes")

    return AgentRunner(
        settings=settings,
        registry=build_registry(settings, memory),
        memory=memory,
        audit=AuditLog(settings.audit_log_path),
        chat=deepseek_client(settings),
        on_event=on_event,
        confirm=confirm,
    )


def _run_task(runner: AgentRunner, task: str, repo: RepoContext) -> None:
    result = runner.run(task, source="cli", repo_context=repo.as_prompt_block())
    color = _GREEN if result.status == "completed" else _RED
    print(f"\n{_c(color, '●')} {result.summary}")
    print(_c(_DIM, f"  [{result.status} · {result.iterations} iteration(s)]"))
    _review_proposals(runner, result.run_id)


def _review_proposals(runner: AgentRunner, run_id: int) -> None:
    """y/n review of code changes the agent proposed during this run."""
    proposals = runner.memory.list_proposals(status="pending", run_id=run_id)
    if not proposals:
        return
    workspace = Path(runner.settings.workspace_root)
    print(_c(_BOLD, f"\n{len(proposals)} proposed change(s) need your y/n:"))
    for p in proposals:
        print(f"\n  {_c(_CYAN, p.file_path)}")
        print(f"  {p.description}")
        _print_mini_diff(p.original_content, p.proposed_content)
        try:
            answer = input(_c(_BOLD, "  Apply this change? [y/N] ")).strip().lower()
        except (EOFError, KeyboardInterrupt):
            print(_c(_DIM, "\n  left pending — review later in the dashboard"))
            return
        if answer in ("y", "yes"):
            ok, message = apply_proposal(runner.memory, workspace, p.id)
            print(_c(_GREEN if ok else _RED, f"  {message}"))
        else:
            ok, message = reject_proposal(runner.memory, p.id)
            print(_c(_DIM, f"  {message}"))


def _print_mini_diff(original: str, proposed: str, max_lines: int = 30) -> None:
    import difflib
    diff = list(difflib.unified_diff(
        original.splitlines(), proposed.splitlines(), lineterm="", n=2))[2:]
    for line in diff[:max_lines]:
        if line.startswith("+"):
            print(_c(_GREEN, f"    {line}"))
        elif line.startswith("-"):
            print(_c(_RED, f"    {line}"))
        else:
            print(_c(_DIM, f"    {line}"))
    if len(diff) > max_lines:
        print(_c(_DIM, f"    … {len(diff) - max_lines} more lines (see dashboard)"))


def _quick_conflict_scan(workspace: Path) -> list[str]:
    """Local, token-free check for unmerged paths (run at startup)."""
    try:
        result = subprocess.run(
            ["git", "diff", "--name-only", "--diff-filter=U"],
            cwd=workspace, capture_output=True, text=True, timeout=8,
        )
        return [f for f in result.stdout.strip().splitlines() if f]
    except Exception:
        return []


def _watch_mode(runner: AgentRunner, repo: RepoContext, workspace: Path) -> None:
    """Stay in the terminal and react to every new commit instantly."""
    from sylithe.devops.watcher import GitWatcher

    print(_c(_BOLD, "Sylithe agent watching ") + _c(_CYAN, repo.repo_name or str(workspace)))
    print(_c(_DIM, "Every new commit triggers an instant review: conflicts, tests, "
                   "proposed fixes. Ctrl-C to stop.\n"))

    def dispatch(task: str) -> None:
        print(_c(_BOLD, "\n⚡ New commit detected — Sylithe agent reviewing…"))
        _run_task(runner, task, detect_repo(workspace))

    watcher = GitWatcher(get_workspace=lambda: str(workspace),
                         is_enabled=lambda: True, dispatch=dispatch)
    watcher.poll_once()  # seed current HEAD
    try:
        while True:
            time.sleep(5)
            watcher.poll_once()
    except KeyboardInterrupt:
        print(_c(_DIM, "\nwatch stopped"))


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="sylithe",
        description="Sylithe AI — autonomous DevOps agent for the current project folder.",
    )
    parser.add_argument("task", nargs="*", help="task to run (omit for interactive mode)")
    parser.add_argument("--yes", "-y", action="store_true",
                        help="auto-approve destructive actions (use with care)")
    parser.add_argument("--model", help="override model (deepseek-chat / deepseek-reasoner)")
    parser.add_argument("--max-iter", type=int, default=None,
                        help="max agent iterations (default: 50)")
    parser.add_argument("--watch", "-w", action="store_true",
                        help="watch the repo: every new commit triggers an instant agent review")
    parser.add_argument("--version", action="version", version=f"sylithe {__version__}")
    args = parser.parse_args()

    _load_global_config()
    _ensure_api_key()

    workspace = Path.cwd()
    repo = detect_repo(workspace)
    runner = _build_runner(workspace, auto_yes=args.yes, model=args.model,
                           max_iter=args.max_iter)

    if args.watch:
        _watch_mode(runner, repo, workspace)
        return

    if args.task:
        _run_task(runner, " ".join(args.task), repo)
        return

    # ── header ──────────────────────────────────────────────────────────────
    print(_c(_BOLD, f"Sylithe {__version__}"))
    if repo.is_git:
        branch_info = _c(_CYAN, repo.branch)
        dirty = _c(_YELLOW, " *") if repo.is_dirty else ""
        sync = ""
        if repo.ahead:
            sync += _c(_YELLOW, f" ↑{repo.ahead}")
        if repo.behind:
            sync += _c(_RED, f" ↓{repo.behind}")
        print(f"  repo   {_c(_BOLD, repo.repo_name)}  {branch_info}{dirty}{sync}")
        print(f"  commit {_c(_DIM, repo.last_commit)}")
        print(f"  remote {_c(_DIM, repo.remote_url or 'none')}")
    else:
        print(f"  folder {_c(_DIM, str(workspace))}  {_c(_YELLOW, '(not a git repo)')}")

    conflicted = _quick_conflict_scan(workspace) if repo.is_git else []
    if conflicted:
        print(_c(_RED, f"\n  ⚠ {len(conflicted)} file(s) have merge conflicts:"))
        for f in conflicted[:8]:
            print(_c(_YELLOW, f"    {f}"))
        print(_c(_BOLD, "  Type 'fix' to resolve them now."))
    print(_c(_DIM, "\nDescribe a task. Ctrl-D or 'exit' to quit.\n"))
    # ────────────────────────────────────────────────────────────────────────

    while True:
        try:
            task = input(_c(_CYAN, "sylithe> ")).strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break
        if not task:
            continue
        if task.lower() in ("exit", "quit"):
            break
        if task.lower() == "fix":
            task = ("Resolve all merge conflicts in this repo as fast as possible: "
                    "detect_conflicts, auto-resolve what is safe, escalate the rest "
                    "with both sides shown, then run tests and summarize.")
        try:
            _run_task(runner, task, detect_repo(workspace))
        except KeyboardInterrupt:
            print(_c(_YELLOW, "\n  interrupted"))


if __name__ == "__main__":
    main()
