from sylithe.agent.policy import PolicyEngine, Risk, Verdict, redact_secrets


def test_destructive_requires_confirmation():
    engine = PolicyEngine()
    decision = engine.check("deploy", Risk.DESTRUCTIVE,
                            {"environment": "staging", "service": "api"})
    assert decision.verdict is Verdict.REQUIRE_CONFIRMATION


def test_destructive_allowed_when_confirmed():
    engine = PolicyEngine(confirmed_actions={"deploy"})
    decision = engine.check("deploy", Risk.DESTRUCTIVE,
                            {"environment": "staging", "service": "api"})
    assert decision.verdict is Verdict.ALLOW


def test_production_deploy_without_rollback_denied_even_if_confirmed():
    engine = PolicyEngine(confirmed_actions={"deploy"})
    decision = engine.check("deploy", Risk.DESTRUCTIVE,
                            {"environment": "production", "service": "api"})
    assert decision.verdict is Verdict.DENY
    assert decision.rule == "rollback-required"


def test_force_push_always_denied():
    engine = PolicyEngine(confirmed_actions={"run_git"})
    decision = engine.check("run_git", Risk.REVERSIBLE, {"args": "push --force origin main"})
    assert decision.verdict is Verdict.DENY
    # --force-with-lease is not in the forbidden set
    ok = engine.check("run_git", Risk.REVERSIBLE,
                      {"args": "push --force-with-lease origin feature"})
    assert ok.verdict is Verdict.ALLOW


def test_merge_to_main_requires_passing_tests():
    engine = PolicyEngine()
    denied = engine.check("merge_branch", Risk.REVERSIBLE,
                          {"source": "feat", "target": "main"}, {})
    assert denied.verdict is Verdict.DENY
    allowed = engine.check("merge_branch", Risk.REVERSIBLE,
                           {"source": "feat", "target": "main"}, {"tests_passed": True})
    assert allowed.verdict is Verdict.ALLOW


def test_redaction_covers_common_secret_shapes():
    text = (
        "key AKIAIOSFODNN7EXAMPLE and token ghp_" + "a" * 36 +
        " and password: hunter2hunter2"
    )
    redacted = redact_secrets(text)
    assert "AKIA" not in redacted
    assert "ghp_" not in redacted
    assert "hunter2" not in redacted
    assert "[REDACTED]" in redacted
