"""Agent-loop tests run against a scripted client — zero DeepSeek tokens spent.

The real DeepSeek connection (sylithe.agent.loop.deepseek_client) is exercised
in production; here we verify the loop's mechanics: tool dispatch, policy
gating, audit, and run bookkeeping.
"""

import json

from tests.conftest import FakeChat, FakeMessage, FakeToolCall


def test_simple_completion(make_state):
    chat = FakeChat([FakeMessage(content="All clear: no conflicts found.")])
    state = make_state(chat)
    result = state.runner().run("check for conflicts")

    assert result.status == "completed"
    assert result.summary == "All clear: no conflicts found."
    assert result.iterations == 1
    runs = state.memory.list_runs()
    assert runs[0].status == "completed"


def test_tool_call_roundtrip(make_state, workspace):
    (workspace / "app.py").write_text("print('hello')\n")
    chat = FakeChat([
        FakeMessage(tool_calls=[FakeToolCall("1", "read_file", '{"path": "app.py"}')]),
        FakeMessage(content="The file prints hello."),
    ])
    state = make_state(chat)
    result = state.runner().run("read app.py")

    assert result.status == "completed"
    # second request must contain the tool result message
    tool_msgs = [m for m in chat.calls[1]["messages"] if m["role"] == "tool"]
    assert tool_msgs and "hello" in tool_msgs[0]["content"]


def test_destructive_skill_blocked_without_confirmation(make_state):
    chat = FakeChat([
        FakeMessage(tool_calls=[FakeToolCall(
            "1", "deploy",
            json.dumps({"service": "api", "environment": "staging"}))]),
        FakeMessage(content="Deploy requires operator confirmation."),
    ])
    state = make_state(chat)
    result = state.runner().run("deploy api to staging")

    assert result.status == "needs_confirmation"
    assert result.pending_confirmations == ["deploy"]


def test_destructive_skill_runs_when_confirmed(make_state):
    chat = FakeChat([
        FakeMessage(tool_calls=[FakeToolCall(
            "1", "deploy",
            json.dumps({"service": "api", "environment": "staging"}))]),
        FakeMessage(content="Staging deploy requested."),
    ])
    state = make_state(chat)
    result = state.runner().run("deploy api to staging", confirmed_actions={"deploy"})

    assert result.status == "completed"
    tool_msgs = [m for m in chat.calls[1]["messages"] if m["role"] == "tool"]
    assert "deploy_requested" in tool_msgs[0]["content"]


def test_interactive_confirm_yes_executes(make_state):
    chat = FakeChat([
        FakeMessage(tool_calls=[FakeToolCall(
            "1", "deploy",
            json.dumps({"service": "api", "environment": "staging"}))]),
        FakeMessage(content="Deployed to staging."),
    ])
    state = make_state(chat)
    runner = state.runner()
    asked = []
    runner.confirm = lambda skill, args: (asked.append(skill), True)[1]
    result = runner.run("deploy api to staging")

    assert asked == ["deploy"]
    assert result.status == "completed"
    assert result.pending_confirmations == []
    tool_msgs = [m for m in chat.calls[1]["messages"] if m["role"] == "tool"]
    assert "deploy_requested" in tool_msgs[0]["content"]


def test_interactive_confirm_no_tells_model_declined(make_state):
    chat = FakeChat([
        FakeMessage(tool_calls=[FakeToolCall(
            "1", "deploy",
            json.dumps({"service": "api", "environment": "staging"}))]),
        FakeMessage(content="Understood, not deploying."),
    ])
    state = make_state(chat)
    runner = state.runner()
    runner.confirm = lambda skill, args: False
    result = runner.run("deploy api to staging")

    assert result.status == "completed"  # declined is resolved, not pending
    tool_msgs = [m for m in chat.calls[1]["messages"] if m["role"] == "tool"]
    assert "Operator declined" in tool_msgs[0]["content"]


def test_unknown_skill_reported_not_crashed(make_state):
    chat = FakeChat([
        FakeMessage(tool_calls=[FakeToolCall("1", "launch_rockets", "{}")]),
        FakeMessage(content="That skill does not exist."),
    ])
    state = make_state(chat)
    result = state.runner().run("do something weird")
    assert result.status == "completed"


def test_repeated_identical_failure_tells_model_to_stop(make_state):
    bad_call = FakeToolCall("1", "read_file", '{"path": "/tmp/nope.txt"}')
    chat = FakeChat([
        FakeMessage(tool_calls=[bad_call]),
        FakeMessage(tool_calls=[bad_call]),
        FakeMessage(tool_calls=[bad_call]),
        FakeMessage(content="Blocked: that file is outside the workspace."),
    ])
    state = make_state(chat)
    result = state.runner().run("read /tmp/nope.txt")

    assert result.status == "completed"
    third_tool_msg = [m for m in chat.calls[3]["messages"] if m["role"] == "tool"][-1]
    assert "Do NOT retry" in third_tool_msg["content"]


def test_audit_chain_remains_valid(make_state, workspace):
    (workspace / "x.txt").write_text("data")
    chat = FakeChat([
        FakeMessage(tool_calls=[FakeToolCall("1", "read_file", '{"path": "x.txt"}')]),
        FakeMessage(content="done"),
    ])
    state = make_state(chat)
    state.runner().run("read x")
    assert state.audit.verify_chain()
