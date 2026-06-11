# Sylithe AI — Architecture

```
GitHub webhook ──┐                       ┌─> DeepSeek API (deepseek-chat)
/api/incidents ──┼─> FastAPI ─> AgentRunner ─┤
/api/runs ───────┘        │          │       └─> Skill registry (Sylithe-native)
Dashboard (/) <───────────┘          │                │
                                     │           PolicyEngine (hard rules)
                                     v                │
                          MemoryStore (patterns, runs)│
                                     v                v
                          AuditLog (hash-chained JSONL)
```

## Decisions

**Single model, DeepSeek.** The original spec proposed DeepSeek base + a second LLM as a
"supervisor" layer. That doubles latency and cost on every step. Instead, safety checks are
deterministic code (`agent/policy.py`) that run before every skill dispatch — cheaper, faster,
and not bypassable by prompt injection. `deepseek-chat` is the default for cost;
`deepseek-reasoner` can be set via `SYLITHE_MODEL` for harder diagnosis tasks.

**Skills are Sylithe-native only.** Every capability the model can invoke is registered in
`skills/registry.py` with a JSON schema, a risk tier (safe / reversible / destructive), and is
exported to DeepSeek in OpenAI function-calling format. Filesystem skills are sandboxed to
`WORKSPACE_ROOT`; path escapes raise.

**Policy before prompt.** The "never do" list lives in the execution layer:
- `rollback-required`: production deploy without rollback plan -> DENY
- `tests-before-merge`: merge to main/master without a passing test run this session -> DENY
- `no-destructive-git`: force-push, hard reset to origin, `clean -f` -> DENY always
- `confirm-destructive`: any destructive-tier skill -> REQUIRE_CONFIRMATION unless the
  operator passed it in `confirmed_actions` for that run
- secret redaction on every skill output and final summary

**Memory as a contract.** `MemoryStore.query_patterns` is keyword-overlap scoring weighted by
confidence today; the interface is stable so a vector DB (Qdrant) can replace the
implementation without touching the agent loop.

**Audit is hash-chained.** Each JSONL entry's hash covers the previous entry. `/healthz`
recomputes the chain; a broken chain is surfaced on the dashboard in red.

## Agent loop lifecycle

1. Task arrives (API call, incident report, or webhook event).
2. `AgentRunner.run` creates a run row + audit entry, sends system prompt + task to DeepSeek
   with the skill schemas.
3. Each tool call: policy check -> execute -> redact -> audit -> feed result back.
4. Loop ends when the model answers without tool calls, or `MAX_AGENT_ITERATIONS` is hit.
5. Run row is finalized with status: `completed | needs_confirmation | max_iterations | error`.
   `needs_confirmation` lists the destructive skills awaiting operator approval; re-run the
   task with `confirmed_actions: ["deploy"]` to approve.

## Token-cost controls

- Skill outputs are truncated (20k chars for command output, 50k for file reads).
- Tests use a scripted fake client; CI never calls DeepSeek.
- `MAX_AGENT_ITERATIONS` bounds the worst-case spend per run.
