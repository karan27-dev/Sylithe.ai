import json
from pathlib import Path

from sylithe.agent.policy import PolicyEngine
from sylithe.devops.proposals import apply_proposal, reject_proposal


def _propose(registry, memory, workspace, content="x = 2\n", run_id=7):
    (workspace / "app.py").write_text("x = 1\n")
    result = registry.execute("propose_change", {
        "path": "app.py",
        "description": "Fix the value of x",
        "new_content": content,
    }, PolicyEngine(), {"run_id": run_id})
    assert result.ok, result.output
    return json.loads(result.output)["proposal_id"]


def test_propose_change_records_without_writing(registry, memory, workspace):
    pid = _propose(registry, memory, workspace)
    # File untouched — proposals never write directly
    assert (workspace / "app.py").read_text() == "x = 1\n"
    proposal = memory.get_proposal(pid)
    assert proposal.status == "pending"
    assert proposal.run_id == 7
    assert proposal.original_content == "x = 1\n"
    assert proposal.proposed_content == "x = 2\n"


def test_apply_proposal_writes_file(registry, memory, workspace):
    pid = _propose(registry, memory, workspace)
    ok, message = apply_proposal(memory, workspace, pid)
    assert ok, message
    assert (workspace / "app.py").read_text() == "x = 2\n"
    assert memory.get_proposal(pid).status == "applied"


def test_apply_is_idempotent(registry, memory, workspace):
    pid = _propose(registry, memory, workspace)
    apply_proposal(memory, workspace, pid)
    ok, message = apply_proposal(memory, workspace, pid)
    assert not ok
    assert "already applied" in message


def test_stale_proposal_not_applied(registry, memory, workspace):
    pid = _propose(registry, memory, workspace)
    # Someone edits the file after the proposal was made
    (workspace / "app.py").write_text("x = 999  # manual edit\n")
    ok, message = apply_proposal(memory, workspace, pid)
    assert not ok
    assert "stale" in message
    assert (workspace / "app.py").read_text() == "x = 999  # manual edit\n"
    assert memory.get_proposal(pid).status == "stale"


def test_reject_proposal(registry, memory, workspace):
    pid = _propose(registry, memory, workspace)
    ok, message = reject_proposal(memory, pid)
    assert ok
    assert (workspace / "app.py").read_text() == "x = 1\n"
    assert memory.get_proposal(pid).status == "rejected"


def test_proposal_api_roundtrip(make_state, registry, memory, workspace):
    from fastapi.testclient import TestClient
    from sylithe.main import create_app
    from tests.conftest import FakeChat, FakeMessage

    pid = _propose(registry, memory, workspace)
    state = make_state(FakeChat([FakeMessage(content="done")]))
    client = TestClient(create_app(state))

    pending = client.get("/api/proposals", params={"status": "pending"}).json()
    assert any(p["id"] == pid for p in pending)

    res = client.post(f"/api/proposals/{pid}/approve")
    assert res.status_code == 200
    assert (workspace / "app.py").read_text() == "x = 2\n"

    # double-approve conflicts
    assert client.post(f"/api/proposals/{pid}/approve").status_code == 409
