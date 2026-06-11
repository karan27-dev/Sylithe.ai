"""The Sylithe agent loop.

DeepSeek (OpenAI-compatible chat completions + function calling) drives the
reasoning; every tool call is policy-checked, executed through the skill
registry, audited, and fed back. The loop ends when the model replies without
tool calls or the iteration budget runs out.
"""

import json
from dataclasses import dataclass, field
from typing import Any, Callable, Protocol

from openai import OpenAI

from sylithe.agent.policy import PolicyEngine, Verdict, redact_secrets
from sylithe.agent.prompt import SYSTEM_PROMPT
from sylithe.audit import AuditLog
from sylithe.config import Settings
from sylithe.memory.store import MemoryStore
from sylithe.skills.registry import SkillRegistry


class ChatClient(Protocol):
    """The slice of the OpenAI SDK the loop uses — injectable for tests."""

    def create(self, **kwargs: Any) -> Any: ...


def deepseek_client(settings: Settings) -> ChatClient:
    # The SDK rejects an empty key at construction; use a placeholder so the
    # server can boot unconfigured. API routes refuse to start runs (503)
    # until DEEPSEEK_API_KEY is actually set.
    client = OpenAI(
        api_key=settings.deepseek_api_key or "unconfigured",
        base_url=settings.deepseek_base_url,
        timeout=settings.request_timeout_seconds,
    )
    return client.chat.completions


@dataclass
class AgentResult:
    status: str  # completed | needs_confirmation | max_iterations | error
    summary: str
    iterations: int
    pending_confirmations: list[str] = field(default_factory=list)
    run_id: int = 0


@dataclass
class AgentRunner:
    settings: Settings
    registry: SkillRegistry
    memory: MemoryStore
    audit: AuditLog
    chat: ChatClient
    policy: PolicyEngine = field(default_factory=PolicyEngine)
    # Optional hooks for interactive frontends (CLI): live event stream and
    # a synchronous yes/no prompt for destructive-skill confirmations.
    on_event: Callable[[str, dict[str, Any]], None] | None = None
    confirm: Callable[[str, dict[str, Any]], bool] | None = None

    def _emit(self, kind: str, **data: Any) -> None:
        if self.on_event is not None:
            self.on_event(kind, data)

    def run(self, task: str, *, source: str = "api",
            confirmed_actions: set[str] | None = None,
            repo_context: str = "") -> AgentResult:
        self.policy.confirmed_actions = confirmed_actions or set()
        run_row = self.memory.create_run(task, source=source)
        self.audit.record("agent_run_started", run_id=run_row.id, task=task, source=source)

        # Prepend repo context so the agent knows WHICH repo it is operating on
        # from the very first token — it never has to guess or waste iterations
        # running 'git remote -v' just to orient itself.
        first_message = f"{repo_context}\n\nTASK: {task}" if repo_context else task

        messages: list[dict[str, Any]] = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": first_message},
        ]
        context: dict[str, Any] = {"run_id": run_row.id}
        pending: list[str] = []
        fail_counts: dict[tuple[str, str], int] = {}
        iterations = 0

        try:
            for iterations in range(1, self.settings.max_agent_iterations + 1):
                response = self.chat.create(
                    model=self.settings.sylithe_model,
                    messages=messages,
                    tools=self.registry.to_openai_tools(),
                )
                message = response.choices[0].message
                tool_calls = message.tool_calls or []

                if not tool_calls:
                    summary = redact_secrets(message.content or "")
                    status = "needs_confirmation" if pending else "completed"
                    self.memory.finish_run(run_row.id, status=status, result=summary,
                                           iterations=iterations)
                    self.audit.record("agent_run_finished", run_id=run_row.id,
                                      status=status, iterations=iterations)
                    return AgentResult(status, summary, iterations, pending, run_row.id)

                messages.append({
                    "role": "assistant",
                    "content": message.content,
                    "tool_calls": [
                        {"id": tc.id, "type": "function",
                         "function": {"name": tc.function.name,
                                      "arguments": tc.function.arguments}}
                        for tc in tool_calls
                    ],
                })

                for tc in tool_calls:
                    name = tc.function.name
                    try:
                        args = json.loads(tc.function.arguments or "{}")
                    except json.JSONDecodeError:
                        args = {}
                    self._emit("skill_start", skill=name, args=args)
                    result = self.registry.execute(name, args, self.policy, context)
                    declined_interactively = False
                    if (result.decision.verdict is Verdict.REQUIRE_CONFIRMATION
                            and self.confirm is not None):
                        if self.confirm(name, args):
                            self.policy.confirmed_actions.add(name)
                            self.audit.record("operator_confirmed", run_id=run_row.id,
                                              skill=name)
                            result = self.registry.execute(name, args, self.policy, context)
                        else:
                            declined_interactively = True
                            self.audit.record("operator_declined", run_id=run_row.id,
                                              skill=name)
                    self._emit("skill_end", skill=name, ok=result.ok,
                               verdict=result.decision.verdict,
                               error="" if result.ok else result.output[:300])
                    self.audit.record(
                        "skill_executed", run_id=run_row.id, skill=name,
                        args=redact_secrets(json.dumps(args, default=str)),
                        verdict=result.decision.verdict, rule=result.decision.rule,
                        ok=result.ok,
                    )
                    output = result.output
                    # Three identical failures means retrying is pointless; tell
                    # the model to stop instead of letting it burn the budget.
                    call_key = (name, json.dumps(args, sort_keys=True, default=str))
                    if result.ok:
                        fail_counts.pop(call_key, None)
                    else:
                        fail_counts[call_key] = fail_counts.get(call_key, 0) + 1
                        if fail_counts[call_key] >= 3:
                            output += ("\n\nThis exact call has failed 3 times. Do NOT "
                                       "retry it. Work around it or finish now with a "
                                       "summary of what you completed and what is blocked.")
                    if declined_interactively:
                        output = ("Operator declined this action. Do not retry it; "
                                  "explain what you would have done instead.")
                    elif result.decision.verdict is Verdict.REQUIRE_CONFIRMATION:
                        pending.append(name)
                    messages.append({
                        "role": "tool",
                        "tool_call_id": tc.id,
                        "content": output,
                    })

            summary = "Iteration budget exhausted before the task completed."
            self.memory.finish_run(run_row.id, status="max_iterations", result=summary,
                                   iterations=iterations)
            self.audit.record("agent_run_finished", run_id=run_row.id,
                              status="max_iterations", iterations=iterations)
            return AgentResult("max_iterations", summary, iterations, pending, run_row.id)

        except Exception as exc:
            summary = redact_secrets(f"{type(exc).__name__}: {exc}")
            self.memory.finish_run(run_row.id, status="error", result=summary,
                                   iterations=iterations)
            self.audit.record("agent_run_finished", run_id=run_row.id,
                              status="error", error=summary, iterations=iterations)
            return AgentResult("error", summary, iterations, pending, run_row.id)
