# Sylithe AI — Autonomous DevOps Agent

Sylithe watches your repos and services, diagnoses every error ("this is the issue, here's why"),
fixes what is safe to fix, and escalates the rest with a concrete plan. Powered by the
**DeepSeek API** (`deepseek-chat`, function calling) for low token cost, with all hard safety
rules enforced **in code**, not in the prompt.

## What it does

- **Error triage** — `POST /api/incidents` with a stack trace or log excerpt from any service;
  the agent queries its pattern memory, identifies the root cause, applies safe fixes, and
  records what worked.
- **Merge-conflict resolution** — classifies each conflict (formatting / dependency / test /
  logic), auto-resolves what is safe, escalates the rest, runs tests after.
- **GitHub webhooks** — push and pull-request events dispatch agent runs automatically
  (HMAC-verified).
- **Pattern memory** — every resolution is stored as `{trigger, context, action_taken,
  outcome, confidence_score}` and queried before acting on the next similar event.
- **Hash-chained audit log** — every skill execution and policy verdict is recorded;
  tampering is detectable (`audit_chain_valid` on `/healthz`).

## Safety model (enforced by the policy engine, not the model)

| Rule | Enforcement |
|------|-------------|
| No production deploy without a rollback plan | `DENY` |
| No merge to main/master without passing tests this session | `DENY` |
| No force-push / history-destroying git | `DENY`, even if confirmed |
| Destructive actions need operator confirmation | `REQUIRE_CONFIRMATION` |
| No secrets in any output | regex redaction on every skill result |

## Quick start — terminal (recommended)

Install once, then run it inside **any project folder** — the current directory becomes
the agent's workspace automatically:

```bash
pip install ./backend          # or: pipx install ./backend

cd /path/to/your/project
sylithe                        # interactive session
sylithe "run the tests and explain any failures"     # one-shot
sylithe -y "resolve merge conflicts and run tests"   # auto-approve destructive skills
```

First run asks for your DeepSeek API key and saves it to `~/.sylithe/config.env`
(never inside your project). Pattern memory and the audit log also live in `~/.sylithe`,
so the agent gets smarter across all your projects. Destructive actions prompt
`Allow? [y/N]` right in the terminal.

## Quick start — server + dashboard

```bash
cp .env.example .env          # add your DEEPSEEK_API_KEY
cd backend
python3 -m venv .venv && .venv/bin/pip install -e ".[dev]"
.venv/bin/pytest              # 30 tests, no tokens spent
.venv/bin/uvicorn --factory sylithe.main:create_app --reload
```

Open http://localhost:8000 for the operator dashboard, or:

```bash
curl -X POST localhost:8000/api/runs -H 'Content-Type: application/json' \
  -d '{"task": "Scan the workspace for merge conflicts, resolve what is safe, run tests."}'

curl -X POST localhost:8000/api/incidents -H 'Content-Type: application/json' \
  -d '{"service": "payments", "error": "TypeError: cannot read properties of undefined at checkout.js:42"}'
```

### Docker

```bash
SYLITHE_WORKSPACE=/path/to/your/repo docker compose up --build
```

## Layout

```
backend/sylithe/
  agent/    loop.py (DeepSeek agent loop) · policy.py (hard rules) · prompt.py
  skills/   registry.py · builtin.py (Sylithe-native skills only)
  devops/   conflicts.py (parse / classify / resolve)
  memory/   store.py (pattern memory + run history)
  api/      routes.py (runs, incidents, webhooks) · dashboard.py
  audit.py  hash-chained audit log
```

Tests use a scripted fake client — **the suite never spends DeepSeek tokens**. The real
client (`sylithe.agent.loop.deepseek_client`) is used at runtime.

## Roadmap

- Vector retrieval (Qdrant) behind the existing `MemoryStore.query_patterns` interface
- Kubernetes / Prometheus skills (OOMKilled right-sizing, alert correlation)
- React frontend replacing the built-in dashboard
- GitHub App integration (check runs, PR comments)
