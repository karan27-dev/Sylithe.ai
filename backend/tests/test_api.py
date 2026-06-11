import hashlib
import hmac
import json

from fastapi.testclient import TestClient

from sylithe.main import create_app
from tests.conftest import FakeChat, FakeMessage


def _client(make_state, script=None):
    state = make_state(FakeChat(script or [FakeMessage(content="done")]))
    return TestClient(create_app(state)), state


def test_healthz(make_state):
    client, _ = _client(make_state)
    res = client.get("/healthz")
    assert res.status_code == 200
    body = res.json()
    assert body["status"] == "ok"
    assert "detect_conflicts" in body["skills"]
    assert body["audit_chain_valid"] is True


def test_create_and_list_runs(make_state):
    client, _ = _client(make_state)
    res = client.post("/api/runs", json={"task": "say done"})
    assert res.status_code == 200
    assert res.json()["status"] == "dispatched"
    # TestClient executes background tasks before returning, so the run exists
    runs = client.get("/api/runs").json()
    assert runs[0]["task"] == "say done"
    assert runs[0]["status"] == "completed"


def test_workspace_info_and_switch(make_state, tmp_path):
    client, _ = _client(make_state)
    info = client.get("/api/workspace").json()
    assert "path" in info and "repo" in info

    new_ws = tmp_path / "other-project"
    new_ws.mkdir()
    res = client.post("/api/workspace", json={"path": str(new_ws)})
    assert res.status_code == 200
    assert res.json()["path"] == str(new_ws.resolve())

    res = client.post("/api/workspace", json={"path": "/definitely/not/a/dir"})
    assert res.status_code == 400


def test_fs_browse_restricted_to_home(make_state):
    client, _ = _client(make_state)
    res = client.get("/api/fs", params={"path": "/etc"})
    assert res.status_code == 403
    res = client.get("/api/fs", params={"path": "~"})
    assert res.status_code == 200
    assert "dirs" in res.json()


def test_integrations_listed(make_state):
    client, _ = _client(make_state)
    items = client.get("/api/integrations").json()
    names = {i["name"] for i in items}
    assert {"Sylithe agent", "Docker", "Kubernetes (kubectl)", "Terraform"} <= names
    core = next(i for i in items if i["name"] == "Sylithe agent")
    assert core["status"] == "connected"  # test settings set a key
    # local toolchain detection: every tool is either usable or honestly absent
    for item in items:
        assert item["status"] in ("connected", "not installed", "setup needed",
                                  "no repo", "soon")


def test_incident_dispatches_agent(make_state):
    client, state = _client(make_state)
    res = client.post("/api/incidents", json={
        "service": "payments",
        "error": "TypeError: cannot read properties of undefined at checkout.js:42",
    })
    assert res.status_code == 200
    assert res.json()["status"] == "triage_dispatched"
    # background task ran on TestClient exit context; run recorded
    runs = state.memory.list_runs()
    assert any(r.source == "incident" for r in runs)


def _sign(secret: str, body: bytes) -> str:
    return "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


def test_webhook_rejects_bad_signature(make_state):
    client, _ = _client(make_state)
    res = client.post("/webhooks/github", content=b"{}",
                      headers={"X-Hub-Signature-256": "sha256=deadbeef",
                               "X-GitHub-Event": "push"})
    assert res.status_code == 401


def test_webhook_accepts_valid_signature(make_state):
    client, _ = _client(make_state)
    body = json.dumps({"zen": "Keep it logically awesome."}).encode()
    res = client.post("/webhooks/github", content=body,
                      headers={"X-Hub-Signature-256": _sign("hook-secret", body),
                               "X-GitHub-Event": "ping",
                               "Content-Type": "application/json"})
    assert res.status_code == 200
    assert res.json()["status"] == "pong"


def test_webhook_push_dispatches_agent(make_state):
    client, state = _client(make_state)
    body = json.dumps({"ref": "refs/heads/main",
                       "repository": {"full_name": "acme/shop"}}).encode()
    res = client.post("/webhooks/github", content=body,
                      headers={"X-Hub-Signature-256": _sign("hook-secret", body),
                               "X-GitHub-Event": "push",
                               "Content-Type": "application/json"})
    assert res.status_code == 200
    assert res.json()["status"] == "agent_dispatched"
    assert any(r.source == "webhook:push" for r in state.memory.list_runs())
