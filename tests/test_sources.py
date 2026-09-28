"""Test-only structural async sources for the EvidenceSource contract."""

from collections.abc import Sequence
from datetime import datetime, timezone
from inspect import iscoroutinefunction

import pytest

import effectrecon
from effectrecon import (
    EffectIdentity,
    Evidence,
    EvidenceClaim,
    EvidenceSource,
    ObservationFailure,
    UnknownOutcome,
    UnknownReason,
)

OBSERVED_AT = datetime(2026, 1, 1, 12, tzinfo=timezone.utc)


def make_unknown_outcome() -> UnknownOutcome:
    effect = EffectIdentity(
        operation="send",
        target="queue:alpha",
        parameters={"message": "hello"},
        idempotency_key="request-1",
    )
    return UnknownOutcome(
        effect=effect,
        attempt_id="attempt-1",
        dispatched_at=OBSERVED_AT,
        detected_at=OBSERVED_AT,
        reason=UnknownReason.RESPONSE_LOST,
    )


def make_evidence(
    unknown: UnknownOutcome,
    *,
    source: str,
    claim: EvidenceClaim,
    remote_resource_id: str | None,
    binding: dict[str, object],
) -> Evidence:
    return Evidence(
        evidence_id=f"{source}-evidence-1",
        source=source,
        claim=claim,
        effect_fingerprint=unknown.effect.fingerprint,
        observed_at=OBSERVED_AT,
        remote_resource_id=remote_resource_id,
        binding=binding,
        metadata={},
    )


class ExecutedFakeSource:
    """A fixed test fixture with a provider-validated executed observation."""

    name = "executed-test-source"

    def __init__(self) -> None:
        self.received_unknown: UnknownOutcome | None = None

    async def observe(self, unknown: UnknownOutcome) -> Sequence[Evidence]:
        self.received_unknown = unknown
        return (
            make_evidence(
                unknown,
                source=self.name,
                claim=EvidenceClaim.EXECUTED,
                remote_resource_id="message-42",
                binding={
                    "attempt_id": unknown.attempt_id,
                    "idempotency_key": unknown.effect.idempotency_key,
                    "operation": unknown.effect.operation,
                    "target": unknown.effect.target,
                },
            ),
        )


class PredeterminedNotExecutedFakeSource:
    """A test fixture with an authoritative provider rejection fixed in advance."""

    name = "authoritative-not-executed-test-source"

    def __init__(self) -> None:
        self.received_unknown: UnknownOutcome | None = None

    async def observe(self, unknown: UnknownOutcome) -> Sequence[Evidence]:
        self.received_unknown = unknown
        return (
            make_evidence(
                unknown,
                source=self.name,
                claim=EvidenceClaim.NOT_EXECUTED,
                remote_resource_id=None,
                binding={
                    "attempt_id": unknown.attempt_id,
                    "provider_status": "rejected_before_execution",
                },
            ),
        )


class InconclusiveFakeSource:
    """A test fixture whose observation does not establish either outcome."""

    name = "inconclusive-test-source"

    def __init__(self) -> None:
        self.received_unknown: UnknownOutcome | None = None

    async def observe(self, unknown: UnknownOutcome) -> Sequence[Evidence]:
        self.received_unknown = unknown
        return (
            make_evidence(
                unknown,
                source=self.name,
                claim=EvidenceClaim.INCONCLUSIVE,
                remote_resource_id=None,
                binding={"attempt_id": unknown.attempt_id, "observation": "ambiguous"},
            ),
        )


class ExpectedObservationFailure(Exception):
    """Deterministic test-only failure raised by the failure source."""


class FailingFakeSource:
    """A test fixture that fails observation without returning evidence."""

    name = "failing-test-source"

    def __init__(self) -> None:
        self.received_unknown: UnknownOutcome | None = None

    async def observe(self, unknown: UnknownOutcome) -> Sequence[Evidence]:
        self.received_unknown = unknown
        raise ExpectedObservationFailure("observation unavailable in test fixture")


def accepts_evidence_source(source: EvidenceSource) -> EvidenceSource:
    """Static typing fixture: unrelated classes fit by shape alone."""
    return source


def test_public_protocol_import_and_structural_typing_fixture() -> None:
    sources: tuple[EvidenceSource, ...] = (
        ExecutedFakeSource(),
        PredeterminedNotExecutedFakeSource(),
        InconclusiveFakeSource(),
        FailingFakeSource(),
    )

    assert effectrecon.EvidenceSource is EvidenceSource
    assert "EvidenceSource" in effectrecon.__all__
    assert all(accepts_evidence_source(source) is source for source in sources)
    assert all(source.__class__.__bases__ == (object,) for source in sources)


@pytest.mark.asyncio
async def test_executed_fake_is_async_and_returns_bound_executed_evidence() -> None:
    unknown = make_unknown_outcome()
    source = ExecutedFakeSource()

    assert iscoroutinefunction(source.observe)
    result = await source.observe(unknown)

    assert isinstance(result, Sequence)
    assert source.name == "executed-test-source"
    assert source.received_unknown is unknown
    assert len(result) == 1
    evidence = result[0]
    assert evidence.source == source.name
    assert evidence.claim is EvidenceClaim.EXECUTED
    assert evidence.effect_fingerprint == unknown.effect.fingerprint
    assert evidence.binding["idempotency_key"] == unknown.effect.idempotency_key
    evidence.require_effect(unknown.effect)


@pytest.mark.asyncio
async def test_not_executed_fake_uses_predetermined_authoritative_fact() -> None:
    unknown = make_unknown_outcome()
    source = PredeterminedNotExecutedFakeSource()

    result = await source.observe(unknown)

    assert source.received_unknown is unknown
    assert len(result) == 1
    evidence = result[0]
    assert evidence.claim is EvidenceClaim.NOT_EXECUTED
    assert evidence.binding["provider_status"] == "rejected_before_execution"
    assert "search_results" not in evidence.binding
    assert evidence.effect_fingerprint == unknown.effect.fingerprint
    evidence.require_effect(unknown.effect)


@pytest.mark.asyncio
async def test_inconclusive_fake_remains_neither_decisive_claim() -> None:
    unknown = make_unknown_outcome()
    source = InconclusiveFakeSource()

    result = await source.observe(unknown)

    assert source.received_unknown is unknown
    assert len(result) == 1
    evidence = result[0]
    assert evidence.claim is EvidenceClaim.INCONCLUSIVE
    assert evidence.effect_fingerprint == unknown.effect.fingerprint
    evidence.require_effect(unknown.effect)


@pytest.mark.asyncio
async def test_failure_fake_raises_without_evidence_or_normalization() -> None:
    unknown = make_unknown_outcome()
    source = FailingFakeSource()

    with pytest.raises(ExpectedObservationFailure) as captured:
        await source.observe(unknown)

    assert source.received_unknown is unknown
    assert not isinstance(captured.value, Evidence)
    assert not isinstance(captured.value, ObservationFailure)
    assert not hasattr(captured.value, "claim")
