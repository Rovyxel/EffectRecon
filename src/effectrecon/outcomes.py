"""Stable semantic outcomes and reasons used by reconciliation models."""

from enum import Enum, unique


@unique
class Outcome(str, Enum):
    """Explicit semantic result of an external side-effect attempt.

    Values are stable lowercase strings suitable for public APIs and
    serialization. ``INDETERMINATE`` does not imply execution or
    non-execution.
    """

    CONFIRMED_EXECUTED = "confirmed_executed"
    CONFIRMED_NOT_EXECUTED = "confirmed_not_executed"
    INDETERMINATE = "indeterminate"


@unique
class ReconciliationReason(str, Enum):
    """Stable reason labels for a later reconciliation result."""

    EXECUTION_CONFIRMED = "execution_confirmed"
    NON_EXECUTION_CONFIRMED = "non_execution_confirmed"
    INSUFFICIENT_EVIDENCE = "insufficient_evidence"
    CONTRADICTORY_EVIDENCE = "contradictory_evidence"
    OBSERVATION_FAILED = "observation_failed"
