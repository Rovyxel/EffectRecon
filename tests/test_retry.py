"""Tests for synchronous, caller-facing retry policy decisions."""

import pytest

from effectrecon import (
    ConservativeRetryPolicy,
    Outcome,
    RetryDecision,
    RetryPolicy,
)

EXPECTED_DECISIONS = {
    Outcome.CONFIRMED_EXECUTED: RetryDecision.DO_NOT_RETRY,
    Outcome.CONFIRMED_NOT_EXECUTED: RetryDecision.REPLAN_REQUIRED,
    Outcome.INDETERMINATE: RetryDecision.MANUAL_REVIEW,
}


def test_retry_decision_has_exact_members_and_stable_values() -> None:
    assert {member.name: member.value for member in RetryDecision} == {
        "SAFE_TO_RETRY": "safe_to_retry",
        "DO_NOT_RETRY": "do_not_retry",
        "REPLAN_REQUIRED": "replan_required",
        "MANUAL_REVIEW": "manual_review",
    }


def test_expected_mapping_covers_the_complete_outcome_enum() -> None:
    assert set(EXPECTED_DECISIONS) == set(Outcome)


@pytest.mark.parametrize("outcome", tuple(Outcome))
def test_conservative_policy_maps_every_outcome(outcome: Outcome) -> None:
    assert ConservativeRetryPolicy().decide(outcome) is EXPECTED_DECISIONS[outcome]


@pytest.mark.parametrize("outcome", tuple(Outcome))
def test_conservative_policy_never_emits_safe_to_retry(outcome: Outcome) -> None:
    assert ConservativeRetryPolicy().decide(outcome) is not RetryDecision.SAFE_TO_RETRY


def test_conservative_policy_rejects_non_outcome_values() -> None:
    with pytest.raises(TypeError, match="outcome must be an Outcome"):
        ConservativeRetryPolicy().decide("confirmed_not_executed")  # type: ignore[arg-type]


class CustomRetryPolicy:
    """Unrelated structural policy with caller-selected behavior."""

    def decide(self, outcome: Outcome) -> RetryDecision:
        if outcome is Outcome.CONFIRMED_NOT_EXECUTED:
            return RetryDecision.SAFE_TO_RETRY
        return RetryDecision.MANUAL_REVIEW


def accept_retry_policy(policy: RetryPolicy) -> RetryPolicy:
    """Static typing fixture for structural protocol conformance."""
    return policy


def test_custom_policy_is_structural_and_its_mapping_is_used() -> None:
    custom = CustomRetryPolicy()
    policy: RetryPolicy = accept_retry_policy(custom)

    assert CustomRetryPolicy.__bases__ == (object,)
    assert policy is custom
    assert policy.decide(Outcome.CONFIRMED_NOT_EXECUTED) is RetryDecision.SAFE_TO_RETRY
    assert policy.decide(Outcome.CONFIRMED_EXECUTED) is RetryDecision.MANUAL_REVIEW
