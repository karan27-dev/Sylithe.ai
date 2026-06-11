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
import sys
from pathlib import Path

from sylithe import __version__
from sylithe.agent.loop import AgentRunner, deepseek_client
from sylithe.audit import AuditLog
from sylithe.config import Settings
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


def _build_runner(workspace: Path, auto_yes: bool, model: str | None) -> AgentRunner:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    settings = Settings(
        workspace_root=str(workspace),
        database_url=f"sqlite:///{CONFIG_DIR / 'sylithe.db'}",
        audit_log_path=str(CONFIG_DIR / "audit.log.jsonl"),
        _env_file=None,
    )
    if model:
        settings.sylithe_model = model
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


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="sylithe",
        description="Sylithe AI — autonomous DevOps agent for the current project folder.",
    )
    parser.add_argument("task", nargs="*", help="task to run (omit for interactive mode)")
    parser.add_argument("--yes", "-y", action="store_true",
                        help="auto-approve destructive actions (use with care)")
    parser.add_argument("--model", help="override model (deepseek-chat / deepseek-reasoner)")
    parser.add_argument("--version", action="version", version=f"sylithe {__version__}")
    args = parser.parse_args()

    _load_global_config()
    _ensure_api_key()

    workspace = Path.cwd()
    repo = detect_repo(workspace)
    runner = _build_runner(workspace, auto_yes=args.yes, model=args.model)

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
        try:
            _run_task(runner, task, repo)
        except KeyboardInterrupt:
            print(_c(_YELLOW, "\n  interrupted"))


if __name__ == "__main__":
    main()
