import json

from sylithe.agent.policy import PolicyEngine


def test_record_pattern_with_string_confidence_and_dict_context(registry, memory):
    """Regression: model-supplied loose types must not break the learning step."""
    result = registry.execute("record_pattern", {
        "trigger": "billing bug",
        "context": {"file": "billing.py", "line": 2},
        "action_taken": "fix tax multiplier",
        "outcome": "success",
        "confidence_score": "0.8",
    }, PolicyEngine(), {})
    assert result.ok, result.output
    assert memory.query_patterns("billing bug")[0].pattern.confidence_score == 0.8


def test_context_argument_not_clobbered_by_run_context(registry, memory):
    """Regression: the run's internal context dict must not overwrite the
    model's `context` argument to record_pattern."""
    run_context = {"tests_passed": True}
    result = registry.execute("record_pattern", {
        "trigger": "t", "context": "human readable context",
        "action_taken": "a", "outcome": "success",
    }, PolicyEngine(), run_context)
    assert result.ok, result.output
    assert memory.query_patterns("human readable context")[0].pattern.context \
        == "human readable context"


def test_run_tests_sets_tests_passed_in_run_context(registry, workspace):
    run_context: dict = {}
    result = registry.execute("run_tests", {"command": "true"}, PolicyEngine(), run_context)
    assert result.ok
    assert run_context["tests_passed"] is True
    registry.execute("run_tests", {"command": "false"}, PolicyEngine(), run_context)
    assert run_context["tests_passed"] is False


def test_path_escape_blocked(registry):
    result = registry.execute("read_file", {"path": "../../etc/passwd"},
                              PolicyEngine(), {})
    assert not result.ok
    assert "escapes the workspace" in result.output


def test_absolute_path_outside_workspace_blocked_with_guidance(registry):
    result = registry.execute("read_file", {"path": "/tmp/test_geom_out.txt"},
                              PolicyEngine(), {})
    assert not result.ok
    assert "relative to the workspace root" in result.output


def test_absolute_path_inside_workspace_allowed(registry, workspace):
    (workspace / "notes.txt").write_text("hello")
    result = registry.execute("read_file", {"path": str(workspace / "notes.txt")},
                              PolicyEngine(), {})
    assert result.ok
    assert result.output == "hello"


def test_detect_conflicts_finds_conflicted_file(registry, workspace):
    (workspace / "app.py").write_text(
        "x = 1\n<<<<<<< HEAD\ny = 2\n=======\ny = 3\n>>>>>>> feature\n")
    result = registry.execute("detect_conflicts", {"path": "."}, PolicyEngine(), {})
    assert result.ok
    data = json.loads(result.output)
    assert data["count"] == 1
    assert data["conflicted_files"][0]["file"] == "app.py"
