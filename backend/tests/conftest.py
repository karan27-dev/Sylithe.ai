import pytest

from sylithe.api.deps import AppState
from sylithe.audit import AuditLog
from sylithe.config import Settings
from sylithe.memory.store import MemoryStore
from sylithe.skills.builtin import build_registry


@pytest.fixture
def settings(tmp_path) -> Settings:
    return Settings(
        deepseek_api_key="test-key",
        database_url=f"sqlite:///{tmp_path}/test.db",
        audit_log_path=str(tmp_path / "audit.jsonl"),
        workspace_root=str(tmp_path / "workspace"),
        github_webhook_secret="hook-secret",
        _env_file=None,
    )


@pytest.fixture
def memory(settings) -> MemoryStore:
    return MemoryStore(settings.database_url)


@pytest.fixture
def audit(settings) -> AuditLog:
    return AuditLog(settings.audit_log_path)


@pytest.fixture
def workspace(settings, tmp_path):
    ws = tmp_path / "workspace"
    ws.mkdir(exist_ok=True)
    return ws


@pytest.fixture
def registry(settings, memory, workspace):
    return build_registry(settings, memory)


class FakeMessage:
    def __init__(self, content=None, tool_calls=None):
        self.content = content
        self.tool_calls = tool_calls


class FakeToolCall:
    def __init__(self, id, name, arguments):
        self.id = id
        self.type = "function"
        self.function = type("F", (), {"name": name, "arguments": arguments})()


class FakeChat:
    """Scripted stand-in for the DeepSeek chat-completions client."""

    def __init__(self, script):
        self.script = list(script)
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        message = self.script.pop(0)
        choice = type("Choice", (), {"message": message})()
        return type("Response", (), {"choices": [choice]})()


@pytest.fixture
def make_state(settings, memory, audit, registry):
    def _make(chat) -> AppState:
        return AppState(settings=settings, memory=memory, audit=audit,
                        registry=registry, chat=chat)
    return _make
