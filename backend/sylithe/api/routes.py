import hashlib
import hmac
import json

from fastapi import APIRouter, BackgroundTasks, Header, HTTPException, Request
from pydantic import BaseModel, Field

from sylithe import __version__
from sylithe.api.deps import AppState

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
def create_run(body: RunRequest, request: Request) -> dict:
    state = _state(request)
    if not state.settings.deepseek_api_key:
        raise HTTPException(503, "DEEPSEEK_API_KEY is not configured")
    result = state.runner().run(body.task, confirmed_actions=set(body.confirmed_actions))
    return {
        "status": result.status,
        "summary": result.summary,
        "iterations": result.iterations,
        "pending_confirmations": result.pending_confirmations,
    }


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
