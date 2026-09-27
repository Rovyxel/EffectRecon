"""Tests for immutable unknown-outcome and outcome models."""

from datetime import datetime, timedelta, timezone, tzinfo

import pytest

from effectrecon import (
    EffectIdentity,
    Outcome,
    ReconciliationReason,
    UnknownOutcome,
    UnknownReason,
)


def make_identity() -> EffectIdentity:
    return EffectIdentity(
        operation="send",
        target="queue:alpha",
        parameters={"message": "hello"},
        idempotency_key="request-1",
    )


def make_unknown_outcome(
    *,
    dispatched_at: datetime | None = None,
    detected_at: datetime | None = None,
    attempt_id: str = "attempt-1",
    effect: EffectIdentity | None = None,
    reason: UnknownReason = UnknownReason.RESPONSE_LOST,
) -> UnknownOutcome:
    return UnknownOutcome(
        effect=make_identity() if effect is None else effect,
        attempt_id=attempt_id,
        dispatched_at=(
            datetime(2026, 1, 1, 12, tzinfo=timezone.utc)
            if dispatched_at is None
            else dispatched_at
        ),
        detected_at=(
            datetime(2026, 1, 1, 12, 1, tzinfo=timezone.utc)
            if detected_at is None
            else detected_at
        ),
        reason=reason,
    )


def test_unknown_reason_has_exact_members_and_stable_values() -> None:
    assert set(UnknownReason) == {
        UnknownReason.RESPONSE_LOST,
        UnknownReason.CONNECTION_DROPPED,
        UnknownReason.TIMEOUT_AFTER_DISPATCH,
        UnknownReason.CLIENT_CRASHED,
        UnknownReason.PROVIDER_STATUS_UNKNOWN,
        UnknownReason.OTHER,
    }
    assert {member.name: member.value for member in UnknownReason} == {
        "RESPONSE_LOST": "response_lost",
        "CONNECTION_DROPPED": "connection_dropped",
        "TIMEOUT_AFTER_DISPATCH": "timeout_after_dispatch",
        "CLIENT_CRASHED": "client_crashed",
        "PROVIDER_STATUS_UNKNOWN": "provider_status_unknown",
        "OTHER": "other",
    }


def test_outcome_has_exact_members_and_stable_values() -> None:
    assert set(Outcome) == {
        Outcome.CONFIRMED_EXECUTED,
        Outcome.CONFIRMED_NOT_EXECUTED,
        Outcome.INDETERMINATE,
    }
    assert {member.name: member.value for member in Outcome} == {
        "CONFIRMED_EXECUTED": "confirmed_executed",
        "CONFIRMED_NOT_EXECUTED": "confirmed_not_executed",
        "INDETERMINATE": "indeterminate",
    }


def test_reconciliation_reason_has_exact_members_and_stable_values() -> None:
    assert set(ReconciliationReason) == {
        ReconciliationReason.EXECUTION_CONFIRMED,
        ReconciliationReason.NON_EXECUTION_CONFIRMED,
        ReconciliationReason.INSUFFICIENT_EVIDENCE,
        ReconciliationReason.CONTRADICTORY_EVIDENCE,
        ReconciliationReason.OBSERVATION_FAILED,
    }
    assert {member.name: member.value for member in ReconciliationReason} == {
        "EXECUTION_CONFIRMED": "execution_confirmed",
        "NON_EXECUTION_CONFIRMED": "non_execution_confirmed",
        "INSUFFICIENT_EVIDENCE": "insufficient_evidence",
        "CONTRADICTORY_EVIDENCE": "contradictory_evidence",
        "OBSERVATION_FAILED": "observation_failed",
    }


def test_unknown_outcome_is_data_and_retains_its_fields() -> None:
    effect = make_identity()
    dispatched = datetime(2026, 1, 1, 12, tzinfo=timezone.utc)
    detected = datetime(2026, 1, 1, 12, 1, tzinfo=timezone.utc)

    outcome = make_unknown_outcome(
        effect=effect,
        attempt_id="attempt-42",
        dispatched_at=dispatched,
        detected_at=detected,
        reason=UnknownReason.TIMEOUT_AFTER_DISPATCH,
    )

    assert isinstance(outcome, UnknownOutcome)
    assert outcome.effect is effect
    assert outcome.attempt_id == "attempt-42"
    assert outcome.dispatched_at is dispatched
    assert outcome.detected_at is detected
    assert outcome.reason is UnknownReason.TIMEOUT_AFTER_DISPATCH


def test_non_utc_aware_timestamps_are_preserved() -> None:
    dispatched = datetime(
        2026, 1, 1, 15, 30, tzinfo=timezone(timedelta(hours=5, minutes=30))
    )
    detected = datetime(2026, 1, 1, 10, 1, tzinfo=timezone.utc)

    outcome = make_unknown_outcome(
        dispatched_at=dispatched,
        detected_at=detected,
    )

    assert outcome.dispatched_at is dispatched
    assert outcome.detected_at is detected


def test_equal_instants_with_different_offsets_are_allowed() -> None:
    dispatched = datetime(
        2026,
        1,
        1,
        15,
        30,
        tzinfo=timezone(timedelta(hours=5, minutes=30)),
    )
    detected = datetime(2026, 1, 1, 10, tzinfo=timezone.utc)

    outcome = make_unknown_outcome(
        dispatched_at=dispatched,
        detected_at=detected,
    )

    assert outcome.dispatched_at == dispatched
    assert outcome.detected_at == detected


def test_naive_dispatched_at_is_rejected() -> None:
    with pytest.raises(ValueError, match="dispatched_at must be timezone-aware"):
        make_unknown_outcome(dispatched_at=datetime(2026, 1, 1, 12))


def test_naive_detected_at_is_rejected() -> None:
    with pytest.raises(ValueError, match="detected_at must be timezone-aware"):
        make_unknown_outcome(detected_at=datetime(2026, 1, 1, 12, 1))


class NoOffset(tzinfo):
    """tzinfo implementation that cannot provide a usable UTC offset."""

    def utcoffset(self, dt: datetime | None) -> timedelta | None:
        return None

    def dst(self, dt: datetime | None) -> timedelta | None:
        return None

    def tzname(self, dt: datetime | None) -> str | None:
        return "no-offset"


def test_tzinfo_without_utc_offset_is_rejected() -> None:
    timestamp = datetime(2026, 1, 1, 12, tzinfo=NoOffset())

    with pytest.raises(ValueError, match="usable UTC offset"):
        make_unknown_outcome(dispatched_at=timestamp)


def test_non_datetime_timestamps_are_rejected() -> None:
    invalid_timestamp = "2026-01-01T12:00:00Z"

    with pytest.raises(TypeError, match="dispatched_at must be a datetime"):
        make_unknown_outcome(
            dispatched_at=invalid_timestamp,  # type: ignore[arg-type]
        )
    with pytest.raises(TypeError, match="detected_at must be a datetime"):
        make_unknown_outcome(
            detected_at=invalid_timestamp,  # type: ignore[arg-type]
        )


def test_detected_at_before_dispatched_at_is_rejected_across_offsets() -> None:
    dispatched = datetime(2026, 1, 1, 12, tzinfo=timezone(timedelta(hours=2)))
    detected = datetime(2026, 1, 1, 9, 59, tzinfo=timezone.utc)

    with pytest.raises(ValueError, match="must not precede"):
        make_unknown_outcome(dispatched_at=dispatched, detected_at=detected)


def test_equal_timestamps_are_allowed() -> None:
    timestamp = datetime(2026, 1, 1, 12, tzinfo=timezone.utc)

    outcome = make_unknown_outcome(dispatched_at=timestamp, detected_at=timestamp)

    assert outcome.dispatched_at is outcome.detected_at


def test_model_rejects_invalid_field_types() -> None:
    timestamp = datetime(2026, 1, 1, 12, tzinfo=timezone.utc)

    with pytest.raises(TypeError, match="effect must be an EffectIdentity"):
        UnknownOutcome(
            effect="effect",  # type: ignore[arg-type]
            attempt_id="attempt-1",
            dispatched_at=timestamp,
            detected_at=timestamp,
            reason=UnknownReason.OTHER,
        )
    with pytest.raises(TypeError, match="attempt_id must be a string"):
        make_unknown_outcome(attempt_id=1)  # type: ignore[arg-type]
    with pytest.raises(TypeError, match="reason must be an UnknownReason"):
        make_unknown_outcome(reason="response_lost")  # type: ignore[arg-type]


def test_model_is_immutable_and_has_value_equality_and_repr() -> None:
    outcome = make_unknown_outcome()
    equal_outcome = make_unknown_outcome()

    assert outcome == equal_outcome
    assert repr(outcome).startswith("UnknownOutcome(")
    assert not hasattr(outcome, "__dict__")
    with pytest.raises(AttributeError):
        outcome.attempt_id = "attempt-2"  # type: ignore[misc]


def test_attempt_id_is_retained_without_normalization() -> None:
    attempt_id = " attempt/α "

    outcome = make_unknown_outcome(attempt_id=attempt_id)

    assert outcome.attempt_id is attempt_id
