"""Pattern memory: record what worked, query it before acting.

Retrieval is keyword-overlap scoring weighted by confidence — deliberately
dependency-free. The interface is the contract; swap ``query`` for a vector
search (Qdrant/Weaviate) without touching callers.
"""

import re
from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker

from sylithe.memory.models import AgentRun, Base, Pattern

_WORD_RE = re.compile(r"[a-z0-9_\-]{3,}")


def _tokens(text: str) -> set[str]:
    return set(_WORD_RE.findall(text.lower()))


@dataclass
class ScoredPattern:
    pattern: Pattern
    score: float


class MemoryStore:
    def __init__(self, database_url: str) -> None:
        connect_args = {"check_same_thread": False} if database_url.startswith("sqlite") else {}
        self._engine = create_engine(database_url, connect_args=connect_args)
        Base.metadata.create_all(self._engine)
        self._session_factory = sessionmaker(self._engine, expire_on_commit=False)

    def session(self) -> Session:
        return self._session_factory()

    # -- patterns ----------------------------------------------------------

    def record_pattern(self, *, trigger: str, context: str, action_taken: str,
                       outcome: str, confidence_score: float = 0.5, repo: str = "") -> Pattern:
        pattern = Pattern(
            trigger=trigger, context=context, action_taken=action_taken,
            outcome=outcome, confidence_score=max(0.0, min(1.0, confidence_score)),
            repo=repo,
        )
        with self.session() as s:
            s.add(pattern)
            s.commit()
        return pattern

    def query_patterns(self, query: str, *, repo: str = "", limit: int = 5) -> list[ScoredPattern]:
        query_tokens = _tokens(query)
        if not query_tokens:
            return []
        with self.session() as s:
            stmt = select(Pattern)
            if repo:
                stmt = stmt.where(Pattern.repo.in_(["", repo]))
            candidates = s.scalars(stmt).all()

        scored: list[ScoredPattern] = []
        for p in candidates:
            overlap = len(query_tokens & _tokens(f"{p.trigger} {p.context} {p.action_taken}"))
            if overlap == 0:
                continue
            score = (overlap / len(query_tokens)) * (0.5 + 0.5 * p.confidence_score)
            scored.append(ScoredPattern(pattern=p, score=round(score, 4)))
        scored.sort(key=lambda sp: sp.score, reverse=True)
        return scored[:limit]

    # -- agent runs --------------------------------------------------------

    def create_run(self, task: str, source: str = "api") -> AgentRun:
        run = AgentRun(task=task, source=source)
        with self.session() as s:
            s.add(run)
            s.commit()
        return run

    def finish_run(self, run_id: int, *, status: str, result: str, iterations: int) -> None:
        with self.session() as s:
            run = s.get(AgentRun, run_id)
            if run is None:
                return
            run.status = status
            run.result = result
            run.iterations = iterations
            run.finished_at = datetime.now(timezone.utc)
            s.commit()

    def list_runs(self, limit: int = 50) -> list[AgentRun]:
        with self.session() as s:
            stmt = select(AgentRun).order_by(AgentRun.id.desc()).limit(limit)
            return list(s.scalars(stmt).all())
