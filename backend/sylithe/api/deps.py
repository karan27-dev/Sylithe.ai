"""Application wiring: one place that builds and owns the long-lived objects."""

from dataclasses import dataclass

from sylithe.agent.loop import AgentRunner, ChatClient, deepseek_client
from sylithe.audit import AuditLog
from sylithe.config import Settings, get_settings
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

    def runner(self) -> AgentRunner:
        return AgentRunner(
            settings=self.settings, registry=self.registry,
            memory=self.memory, audit=self.audit, chat=self.chat,
        )


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
