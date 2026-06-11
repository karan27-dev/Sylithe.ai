import hashlib
import hmac
import json
from pathlib import Path

from fastapi import APIRouter, BackgroundTasks, Header, HTTPException, Request
from pydantic import BaseModel, Field

from sylithe import __version__
from sylithe.api.deps import AppState
from sylithe.devops.proposals import apply_proposal, reject_proposal

router = APIRouter()


def _state(request: Request) -> AppState:
    return request.app.state.sylithe


@router.get("/healthz")
def healthz(request: Request) -> dict:
    state = _state(request)
    return {
        "status": "ok",
        "version": __version__,
        "model": state.settings.sylithe_model,
        "skills": state.registry.names(),
        "audit_chain_valid": state.audit.verify_chain(),
    }


class RunRequest(BaseModel):
    task: str = Field(min_length=1, max_length=20000)
    confirmed_actions: list[str] = Field(default_factory=list,
                                         description="Destructive skills the operator pre-approves for this run")


@router.post("/api/runs")
def create_run(body: RunRequest, request: Request, background: BackgroundTasks) -> dict:
    state = _state(request)
    if not state.settings.deepseek_api_key:
        raise HTTPException(503, "DEEPSEEK_API_KEY is not configured")
    background.add_task(
        state.runner().run, body.task,
        confirmed_actions=set(body.confirmed_actions),
        repo_context=state.repo().as_prompt_block(),
    )
    return {"status": "dispatched"}


# ── workspace ──────────────────────────────────────────────────────────────

@router.get("/api/workspace")
def get_workspace(request: Request) -> dict:
    state = _state(request)
    repo = state.repo()
    return {
        "path": str(Path(state.settings.workspace_root).resolve()),
        "watch_enabled": state.watch_enabled,
        "repo": {
            "is_git": repo.is_git, "name": repo.repo_name, "branch": repo.branch,
            "remote": repo.remote_url, "last_commit": repo.last_commit,
            "dirty": repo.is_dirty, "ahead": repo.ahead, "behind": repo.behind,
        },
    }


class WorkspaceRequest(BaseModel):
    path: str = Field(min_length=1)


@router.post("/api/workspace")
def open_workspace(body: WorkspaceRequest, request: Request) -> dict:
    target = Path(body.path).expanduser()
    if not target.is_dir():
        raise HTTPException(400, f"'{body.path}' is not a directory")
    _state(request).set_workspace(str(target.resolve()))
    return get_workspace(request)


@router.get("/api/fs")
def browse_dirs(request: Request, path: str = "~") -> dict:
    """Directory browser for the folder picker (directories only)."""
    target = Path(path).expanduser()
    home = Path.home()
    try:
        target = target.resolve()
    except OSError:
        raise HTTPException(400, "Bad path")
    if not target.is_dir():
        raise HTTPException(404, "Not a directory")
    if not (target == home or target.is_relative_to(home)):
        raise HTTPException(403, "Browsing is limited to your home directory")
    dirs = sorted(
        p.name for p in target.iterdir()
        if p.is_dir() and not p.name.startswith(".")
    )
    return {
        "path": str(target),
        "parent": str(target.parent) if target != home else None,
        "dirs": dirs,
        "is_git": (target / ".git").exists(),
    }


@router.post("/api/watch")
def toggle_watch(request: Request, body: dict) -> dict:
    state = _state(request)
    state.set_watch(bool(body.get("enabled")))
    return {"watch_enabled": state.watch_enabled}


# ── proposals (the y/n flow) ───────────────────────────────────────────────

@router.get("/api/proposals")
def list_proposals(request: Request, status: str = "") -> list[dict]:
    return [
        {
            "id": p.id, "run_id": p.run_id, "file_path": p.file_path,
            "description": p.description, "status": p.status,
            "original": p.original_content, "proposed": p.proposed_content,
            "created_at": p.created_at,
        }
        for p in _state(request).memory.list_proposals(status=status or None)
    ]


@router.post("/api/proposals/{proposal_id}/approve")
def approve_proposal(proposal_id: int, request: Request) -> dict:
    state = _state(request)
    ok, message = apply_proposal(state.memory, Path(state.settings.workspace_root),
                                 proposal_id)
    state.audit.record("proposal_resolved", proposal_id=proposal_id,
                       decision="approve", ok=ok, detail=message)
    if not ok:
        raise HTTPException(409, message)
    return {"ok": True, "message": message}


@router.post("/api/proposals/{proposal_id}/reject")
def reject_proposal_route(proposal_id: int, request: Request) -> dict:
    state = _state(request)
    ok, message = reject_proposal(state.memory, proposal_id)
    state.audit.record("proposal_resolved", proposal_id=proposal_id,
                       decision="reject", ok=ok, detail=message)
    if not ok:
        raise HTTPException(409, message)
    return {"ok": True, "message": message}


# ── run activity + integrations ────────────────────────────────────────────

@router.get("/api/runs/{run_id}/events")
def run_events(run_id: int, request: Request) -> list[dict]:
    """Skill-level activity for one run, straight from the audit log."""
    state = _state(request)
    path = Path(state.settings.audit_log_path)
    if not path.exists():
        return []
    events = []
    with path.open() as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            entry = json.loads(line)
            if entry.get("run_id") == run_id:
                events.append({k: entry.get(k) for k in
                               ("ts", "event", "skill", "args", "verdict", "ok", "status")})
    return events


@router.get("/api/integrations")
def integrations(request: Request) -> list[dict]:
    state = _state(request)
    return [
        {"name": "DeepSeek", "kind": "llm",
         "status": "connected" if state.settings.deepseek_api_key else "setup needed"},
        {"name": "GitHub", "kind": "source",
         "status": "connected" if state.settings.github_webhook_secret else "setup needed"},
        {"name": "Git (local)", "kind": "source",
         "status": "connected" if state.repo().is_git else "no repo"},
        {"name": "GitLab", "kind": "source", "status": "soon"},
        {"name": "Docker", "kind": "containers", "status": "soon"},
        {"name": "Kubernetes", "kind": "orchestration", "status": "soon"},
        {"name": "Prometheus", "kind": "observability", "status": "soon"},
        {"name": "Grafana", "kind": "observability", "status": "soon"},
        {"name": "Sentry", "kind": "errors", "status": "soon"},
        {"name": "PagerDuty", "kind": "incidents", "status": "soon"},
        {"name": "Slack", "kind": "notifications", "status": "soon"},
    ]


@router.get("/api/runs")
def list_runs(request: Request) -> list[dict]:
    return [
        {
            "id": r.id, "task": r.task[:300], "status": r.status,
            "iterations": r.iterations, "source": r.source,
            "created_at": r.created_at, "finished_at": r.finished_at,
            "result": r.result[:1000],
        }
        for r in _state(request).memory.list_runs()
    ]


@router.get("/api/patterns")
def search_patterns(q: str, request: Request, repo: str = "") -> list[dict]:
    return [
        {
            "trigger": sp.pattern.trigger, "action_taken": sp.pattern.action_taken,
            "outcome": sp.pattern.outcome, "confidence": sp.pattern.confidence_score,
            "score": sp.score,
        }
        for sp in _state(request).memory.query_patterns(q, repo=repo)
    ]


class IncidentReport(BaseModel):
    """Any service can POST an error here; the agent diagnoses and proposes/applies a fix."""

    service: str = Field(min_length=1, max_length=200)
    error: str = Field(min_length=1, max_length=50000,
                       description="Error message, stack trace, or log excerpt")
    environment: str = "production"
    metadata: dict = Field(default_factory=dict)


@router.post("/api/incidents")
def report_incident(body: IncidentReport, request: Request,
                    background: BackgroundTasks) -> dict:
    state = _state(request)
    state.audit.record("incident_reported", service=body.service,
                       environment=body.environment)
    if not state.settings.deepseek_api_key:
        raise HTTPException(503, "DEEPSEEK_API_KEY is not configured")

    task = (
        f"INCIDENT in service '{body.service}' ({body.environment}).\n"
        f"Error report:\n{body.error}\n\n"
        f"Metadata: {json.dumps(body.metadata, default=str)}\n\n"
        "Triage this: query_memory for similar past incidents first. Identify the root "
        "cause, state clearly what the issue is, and if the fix is safe and reversible "
        "(config in workspace, code patch, re-run tests) apply it; otherwise produce a "
        "concrete fix plan with risk level and rollback steps. Record the pattern when done."
    )
    background.add_task(state.runner().run, task, source="incident")
    return {"status": "triage_dispatched", "service": body.service}


def _verify_github_signature(secret: str, body: bytes, signature: str | None) -> bool:
    if not secret or not signature or not signature.startswith("sha256="):
        return False
    expected = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(signature.removeprefix("sha256="), expected)


@router.post("/webhooks/github")
async def github_webhook(
    request: Request,
    background: BackgroundTasks,
    x_hub_signature_256: str | None = Header(default=None),
    x_github_event: str = Header(default="ping"),
) -> dict:
    state = _state(request)
    body = await request.body()
    if not _verify_github_signature(state.settings.github_webhook_secret, body,
                                    x_hub_signature_256):
        state.audit.record("webhook_rejected", reason="bad_signature",
                           github_event=x_github_event)
        raise HTTPException(401, "Invalid webhook signature")

    payload = json.loads(body or b"{}")
    state.audit.record("webhook_received", github_event=x_github_event,
                       repo=payload.get("repository", {}).get("full_name", ""))

    if x_github_event == "ping":
        return {"status": "pong"}

    task = _task_from_event(x_github_event, payload)
    if task is None:
        return {"status": "ignored", "event": x_github_event}
    if not state.settings.deepseek_api_key:
        return {"status": "queued_unconfigured",
                "detail": "Event recorded; DEEPSEEK_API_KEY not set so no agent run started."}

    background.add_task(state.runner().run, task, source=f"webhook:{x_github_event}")
    return {"status": "agent_dispatched", "event": x_github_event}


def _task_from_event(event: str, payload: dict) -> str | None:
    repo = payload.get("repository", {}).get("full_name", "unknown/repo")
    if event == "push":
        ref = payload.get("ref", "")
        return (f"A push landed on {repo} ({ref}). Inspect the workspace with run_git "
                f"('status', 'log --oneline -5'), run the test suite, and report findings.")
    if event == "pull_request" and payload.get("action") in ("opened", "synchronize", "reopened"):
        number = payload.get("number")
        return (f"Pull request #{number} on {repo} was updated. Check the workspace for merge "
                f"conflicts with detect_conflicts, resolve what is safely auto-resolvable, "
                f"run tests, and summarize.")
    return None
