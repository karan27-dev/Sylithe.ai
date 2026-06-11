"""Sylithe skill registry.

A *skill* is a typed, policy-gated capability the agent can invoke. Skills are
Sylithe-native only — registered here with a JSON-schema signature that is
exported in OpenAI function-calling format for the DeepSeek API.
"""

import inspect
import json
from dataclasses import dataclass, field
from typing import Any, Callable

from sylithe.agent.policy import PolicyDecision, PolicyEngine, Risk, Verdict, redact_secrets


@dataclass
class Skill:
    name: str
    description: str
    parameters: dict[str, Any]
    func: Callable[..., Any]
    risk: Risk


@dataclass
class SkillResult:
    ok: bool
    output: str
    decision: PolicyDecision


@dataclass
class SkillRegistry:
    _skills: dict[str, Skill] = field(default_factory=dict)

    def register(self, *, name: str, description: str, parameters: dict[str, Any],
                 risk: Risk = Risk.SAFE) -> Callable:
        def decorator(func: Callable) -> Callable:
            if name in self._skills:
                raise ValueError(f"Skill '{name}' already registered")
            self._skills[name] = Skill(name, description, parameters, func, risk)
            return func
        return decorator

    def get(self, name: str) -> Skill | None:
        return self._skills.get(name)

    def names(self) -> list[str]:
        return sorted(self._skills)

    def to_openai_tools(self) -> list[dict[str, Any]]:
        return [
            {
                "type": "function",
                "function": {
                    "name": s.name,
                    "description": s.description,
                    "parameters": s.parameters,
                },
            }
            for s in self._skills.values()
        ]

    def execute(self, name: str, args: dict[str, Any], policy: PolicyEngine,
                context: dict[str, Any] | None = None) -> SkillResult:
        skill = self._skills.get(name)
        if skill is None:
            decision = PolicyDecision(Verdict.DENY, f"Unknown skill '{name}'.", rule="unknown-skill")
            return SkillResult(False, decision.reason, decision)

        decision = policy.check(name, skill.risk, args, context)
        if decision.verdict is not Verdict.ALLOW:
            return SkillResult(False, decision.reason, decision)

        try:
            sig = inspect.signature(skill.func)
            accepted = {k: v for k, v in args.items() if k in sig.parameters
                        and not k.startswith("_")}
            # The run's shared context is injected via the reserved `_context`
            # parameter so it can never collide with a model-supplied argument.
            if "_context" in sig.parameters:
                accepted["_context"] = context if context is not None else {}
            output = skill.func(**accepted)
            if not isinstance(output, str):
                output = json.dumps(output, default=str)
            return SkillResult(True, redact_secrets(output), decision)
        except Exception as exc:  # never silently swallow errors — report them
            return SkillResult(False, redact_secrets(f"{type(exc).__name__}: {exc}"), decision)
