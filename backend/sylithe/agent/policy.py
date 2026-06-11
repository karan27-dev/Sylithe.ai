"""Policy engine — the spec's "WHAT YOU NEVER DO" list, enforced in code.

Every skill invocation passes through ``PolicyEngine.check`` before dispatch.
The model cannot bypass these rules: they run in the execution layer, not the
prompt. Secret redaction runs on every value that leaves the system.
"""

import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class Risk(str, Enum):
    SAFE = "safe"                # read-only: status, diff, log, query memory
    REVERSIBLE = "reversible"    # writes that are easy to undo: branch, stage deploy
    DESTRUCTIVE = "destructive"  # prod deploy, storage deletion, force push


class Verdict(str, Enum):
    ALLOW = "allow"
    REQUIRE_CONFIRMATION = "require_confirmation"
    DENY = "deny"


@dataclass
class PolicyDecision:
    verdict: Verdict
    reason: str
    rule: str = ""


# Patterns for secrets that must never appear in logs or model output.
_SECRET_PATTERNS = [
    re.compile(r"AKIA[0-9A-Z]{16}"),                                # AWS access key id
    re.compile(r"ghp_[A-Za-z0-9]{36,}"),                            # GitHub PAT
    re.compile(r"github_pat_[A-Za-z0-9_]{22,}"),                    # GitHub fine-grained PAT
    re.compile(r"sk-[A-Za-z0-9\-_]{20,}"),                          # generic API secret key
    re.compile(r"xox[bporas]-[A-Za-z0-9\-]{10,}"),                  # Slack tokens
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]*?-----END [A-Z ]*PRIVATE KEY-----"),
    re.compile(r"(?i)(password|passwd|secret|token)\s*[:=]\s*['\"]?[^\s'\"]{8,}"),
]

# Git invocations that rewrite shared history or destroy work.
_FORBIDDEN_GIT = re.compile(
    r"(push\s+.*--force(?!-with-lease)|push\s+-f\b|reset\s+--hard\s+origin|clean\s+-[a-z]*f|branch\s+-D\s+(main|master)\b)"
)


def redact_secrets(text: str) -> str:
    for pattern in _SECRET_PATTERNS:
        text = pattern.sub("[REDACTED]", text)
    return text


@dataclass
class PolicyEngine:
    """Stateless rule evaluation; confirmations are granted per-run by a human."""

    confirmed_actions: set[str] = field(default_factory=set)

    def check(self, skill_name: str, risk: Risk, args: dict[str, Any],
              context: dict[str, Any] | None = None) -> PolicyDecision:
        context = context or {}

        # Rule: never run forbidden git operations, confirmed or not.
        if skill_name == "run_git":
            cmd = str(args.get("args", ""))
            if _FORBIDDEN_GIT.search(cmd):
                return PolicyDecision(
                    Verdict.DENY,
                    "History-destroying git operations are never permitted.",
                    rule="no-destructive-git",
                )

        # Rule: never merge to a protected branch without passing tests.
        if skill_name == "merge_branch":
            target = str(args.get("target", ""))
            if target in ("main", "master") and not context.get("tests_passed"):
                return PolicyDecision(
                    Verdict.DENY,
                    "Merging to a protected branch requires a passing test run in this session.",
                    rule="tests-before-merge",
                )

        # Rule: never deploy to production without a rollback plan.
        if skill_name == "deploy" and str(args.get("environment", "")) == "production":
            if not args.get("rollback_plan"):
                return PolicyDecision(
                    Verdict.DENY,
                    "Production deploys require an explicit rollback plan.",
                    rule="rollback-required",
                )

        # Rule: destructive actions always require human confirmation.
        if risk is Risk.DESTRUCTIVE:
            if skill_name in self.confirmed_actions:
                return PolicyDecision(
                    Verdict.ALLOW, "Destructive action pre-confirmed by operator.",
                    rule="confirmed-destructive",
                )
            return PolicyDecision(
                Verdict.REQUIRE_CONFIRMATION,
                f"'{skill_name}' is destructive and needs operator confirmation.",
                rule="confirm-destructive",
            )

        return PolicyDecision(Verdict.ALLOW, "Safe or reversible action.", rule="default-allow")
