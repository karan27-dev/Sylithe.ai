def test_record_and_query_pattern(memory):
    memory.record_pattern(
        trigger="OOMKilled pod payment-service",
        context="k8s prod cluster, memory limit 256Mi",
        action_taken="raised memory limit to 512Mi and added HPA",
        outcome="success",
        confidence_score=0.9,
    )
    memory.record_pattern(
        trigger="flaky test in checkout suite",
        context="ci",
        action_taken="quarantined test",
        outcome="partial",
        confidence_score=0.4,
    )

    results = memory.query_patterns("pod OOMKilled in payment service")
    assert results
    assert results[0].pattern.action_taken.startswith("raised memory limit")
    assert results[0].score > 0


def test_query_no_match_returns_empty(memory):
    assert memory.query_patterns("completely unrelated quantum blockchain") == []


def test_higher_confidence_ranks_first(memory):
    memory.record_pattern(trigger="redis connection timeout", context="",
                          action_taken="restart pool", outcome="partial", confidence_score=0.2)
    memory.record_pattern(trigger="redis connection timeout", context="",
                          action_taken="raise pool size + timeout", outcome="success",
                          confidence_score=0.95)
    results = memory.query_patterns("redis connection timeout")
    assert results[0].pattern.confidence_score == 0.95


def test_run_lifecycle(memory):
    run = memory.create_run("check conflicts", source="webhook:push")
    memory.finish_run(run.id, status="completed", result="all clear", iterations=3)
    runs = memory.list_runs()
    assert runs[0].id == run.id
    assert runs[0].status == "completed"
    assert runs[0].iterations == 3
    assert runs[0].finished_at is not None
