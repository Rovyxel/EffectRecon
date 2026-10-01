"""Caller-facing policy decisions for reconciled outcomes."""

from enum import Enum, unique
from typing import Protocol

from effectrecon.outcomes import Outcome


@unique
class RetryDecision(str, Enum):
    """Stable caller-facing decisions about what to do after reconciliation."""

    SAFE_TO_RETRY = "safe_to_retry"
    DO_NOT_RETRY = "do_not_retry"
    REPLAN_REQUIRED = "replan_required"
    MANUAL_REVIEW = "manual_review"


class RetryPolicy(Protocol):
    """Structural synchronous policy mapping an outcome to a decision."""

    def decide(self, outcome: Outcome) -> RetryDecision:
        """Return a caller-facing decision without performing any action."""
        ...


class ConservativeRetryPolicy:
    """Stateless default policy that never declares an outcome safe to retry."""

    __slots__ = ()

    def decide(self, outcome: Outcome) -> RetryDecision:
        """Map a factual outcome to a conservative caller-facing decision."""
        if not isinstance(outcome, Outcome):
            raise TypeError("outcome must be an Outcome")

        decisions = {
            Outcome.CONFIRMED_EXECUTED: RetryDecision.DO_NOT_RETRY,
            Outcome.CONFIRMED_NOT_EXECUTED: RetryDecision.REPLAN_REQUIRED,
            Outcome.INDETERMINATE: RetryDecision.MANUAL_REVIEW,
        }
        return decisions[outcome]
