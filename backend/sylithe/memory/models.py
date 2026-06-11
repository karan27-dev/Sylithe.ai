from datetime import datetime, timezone

from sqlalchemy import Float, Integer, String, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


class Pattern(Base):
    """One learned pattern: { trigger, context, action_taken, outcome, confidence_score }."""

    __tablename__ = "patterns"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    trigger: Mapped[str] = mapped_column(String(255), index=True)
    context: Mapped[str] = mapped_column(Text, default="")
    action_taken: Mapped[str] = mapped_column(Text)
    outcome: Mapped[str] = mapped_column(String(32), index=True)  # success | failure | partial
    confidence_score: Mapped[float] = mapped_column(Float, default=0.5)
    repo: Mapped[str] = mapped_column(String(255), default="", index=True)
    created_at: Mapped[datetime] = mapped_column(default=_utcnow)


class Proposal(Base):
    """A code change the agent suggests; the operator approves/rejects (y/n)."""

    __tablename__ = "proposals"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    run_id: Mapped[int] = mapped_column(Integer, default=0, index=True)
    file_path: Mapped[str] = mapped_column(String(1024))
    description: Mapped[str] = mapped_column(Text, default="")
    original_content: Mapped[str] = mapped_column(Text, default="")
    proposed_content: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(32), default="pending", index=True)
    created_at: Mapped[datetime] = mapped_column(default=_utcnow)
    resolved_at: Mapped[datetime | None] = mapped_column(nullable=True)


class AgentRun(Base):
    """One agent task execution, for the dashboard and audit cross-reference."""

    __tablename__ = "agent_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    task: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(32), default="running", index=True)
    result: Mapped[str] = mapped_column(Text, default="")
    iterations: Mapped[int] = mapped_column(Integer, default=0)
    source: Mapped[str] = mapped_column(String(64), default="api")  # api | webhook
    created_at: Mapped[datetime] = mapped_column(default=_utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(nullable=True)
