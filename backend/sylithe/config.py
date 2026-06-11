from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime configuration, loaded from environment / .env."""

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # DeepSeek API (OpenAI-compatible)
    deepseek_api_key: str = ""
    deepseek_base_url: str = "https://api.deepseek.com"
    sylithe_model: str = "deepseek-chat"

    # Storage
    database_url: str = "sqlite:///./sylithe.db"
    audit_log_path: str = "./audit.log.jsonl"

    # Integrations
    github_webhook_secret: str = ""

    # Agent loop
    max_agent_iterations: int = 20
    request_timeout_seconds: float = 120.0

    # Workspace root the agent's skills are allowed to touch
    workspace_root: str = "."


@lru_cache
def get_settings() -> Settings:
    return Settings()
