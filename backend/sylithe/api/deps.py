"""Application wiring: one place that builds and owns the long-lived objects."""

import threading
from dataclasses import dataclass, field

from sylithe.agent.loop import AgentRunner, ChatClient, deepseek_client
from sylithe.audit import AuditLog
from sylithe.config import Settings, get_settings
from sylithe.devops.repo import RepoContext, detect as detect_repo
from sylithe.devops.watcher import GitWatcher
from sylithe.memory.store import MemoryStore
from sylithe.skills.builtin import build_registry
from sylithe.skills.registry import SkillRegistry


@dataclass
class AppState:
    settings: Settings
    memory: MemoryStore
    audit: AuditLog
    registry: SkillRegistry
    chat: ChatClient
    watch_enabled: bool = False
    _watcher: GitWatcher | None = field(default=None, repr=False)

    def runner(self) -> AgentRunner:
        return AgentRunner(
            settings=self.settings, registry=self.registry,
            memory=self.memory, audit=self.audit, chat=self.chat,
        )

    def repo(self) -> RepoContext:
        from pathlib import Path
        return detect_repo(Path(self.settings.workspace_root))

    def set_workspace(self, path: str) -> RepoContext:
        """Re-point every skill at a new project folder."""
        self.settings.workspace_root = path
        self.registry = build_registry(self.settings, self.memory)
        self.audit.record("workspace_changed", path=path)
        return self.repo()

    def set_watch(self, enabled: bool) -> None:
        self.watch_enabled = enabled
        self.audit.record("git_watch_toggled", enabled=enabled)
        if enabled and self._watcher is None:
            def dispatch(task: str) -> None:
                threading.Thread(
                    target=self.runner().run, args=(task,),
                    kwargs={"source": "git-watch",
                            "repo_context": self.repo().as_prompt_block()},
                    daemon=True,
                ).start()

            self._watcher = GitWatcher(
                get_workspace=lambda: self.settings.workspace_root,
                is_enabled=lambda: self.watch_enabled,
                dispatch=dispatch,
            )
            self._watcher.poll_once()  # seed the current HEAD
            self._watcher.start()


def build_state(settings: Settings | None = None, chat: ChatClient | None = None) -> AppState:
    settings = settings or get_settings()
    memory = MemoryStore(settings.database_url)
    return AppState(
        settings=settings,
        memory=memory,
        audit=AuditLog(settings.audit_log_path),
        registry=build_registry(settings, memory),
        chat=chat or deepseek_client(settings),
    )
