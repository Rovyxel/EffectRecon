"""Downstream-style tests for offline ambiguous-outcome fixtures."""

from dataclasses import FrozenInstanceError
from datetime import datetime, timezone
from inspect import iscoroutinefunction

import pytest

import effectrecon
import effectrecon.testing as testing
from effectrecon import (
    ConservativeRetryPolicy,
    EffectIdentity,
    Evidence,
    EvidenceClaim,
    EvidenceSource,
    ObservationFailure,
    Outcome,
    Reconciler,
    ReconciliationReason,
    ReconciliationResult,
    RetryDecision,
    UnknownReason,
)
from effectrecon.testing import (
    AmbiguousOutcomeScenario,
    DeterministicEvidenceSource,
    ScenarioFixture,
    build_scenario,
)

EXPECTED_RESULTS = {
    AmbiguousOutcomeScenario.DISCONNECT_DURING_DISPATCH: (
        Outcome.INDETERMINATE,
        ReconciliationReason.INSUFFICIENT_EVIDENCE,
        (EvidenceClaim.INCONCLUSIVE,),
    ),
    AmbiguousOutcomeScenario.EXECUTED_RESPONSE_LOST: (
        Outcome.CONFIRMED_EXECUTED,
        ReconciliationReason.EXECUTION_CONFIRMED,
        (EvidenceClaim.EXECUTED,),
    ),
    AmbiguousOutcomeScenario.NOT_EXECUTED_RESPONSE_LOST: (
        Outcome.CONFIRMED_NOT_EXECUTED,
        ReconciliationReason.NON_EXECUTION_CONFIRMED,
        (EvidenceClaim.NOT_EXECUTED,),
    ),
    AmbiguousOutcomeScenario.OBSERVATION_UNAVAILABLE: (
        Outcome.INDETERMINATE,
        ReconciliationReason.INSUFFICIENT_EVIDENCE,
        (),
    ),
    AmbiguousOutcomeScenario.CONTRADICTORY_EVIDENCE: (
        Outcome.INDETERMINATE,
        ReconciliationReason.CONTRADICTORY_EVIDENCE,
        (EvidenceClaim.EXECUTED, EvidenceClaim.NOT_EXECUTED),
    ),
}


def make_effect() -> EffectIdentity:
    return EffectIdentity("send", "queue:test", {"message": "hello"}, "request-1")


def semantic_result(
    result: ReconciliationResult,
) -> tuple[
    EffectIdentity,
    Outcome,
    ReconciliationReason,
    tuple[Evidence, ...],
    tuple[ObservationFailure, ...],
]:
    """Exclude only Reconciler's runtime start/completion timestamps."""
    return (
        result.effect,
        result.outcome,
        result.reason,
        result.evidence,
        result.observation_failures,
    )


def accepts_evidence_source(source: EvidenceSource) -> EvidenceSource:
    """Mypy checks the concrete fake against the protocol without inheritance."""
    return source


def test_exact_scenario_enum_and_public_namespace() -> None:
    assert {
        name: member.value
        for name, member in AmbiguousOutcomeScenario.__members__.items()
    } == {
        "BEFORE_DISPATCH_FAILURE": "before_dispatch_failure",
        "DISCONNECT_DURING_DISPATCH": "disconnect_during_dispatch",
        "EXECUTED_RESPONSE_LOST": "executed_response_lost",
        "NOT_EXECUTED_RESPONSE_LOST": "not_executed_response_lost",
        "OBSERVATION_UNAVAILABLE": "observation_unavailable",
        "CONTRADICTORY_EVIDENCE": "contradictory_evidence",
    }
    assert all(isinstance(member, str) for member in AmbiguousOutcomeScenario)
    assert set(testing.__all__) == {
        "AmbiguousOutcomeScenario",
        "ScenarioFixture",
        "DeterministicEvidenceSource",
        "build_scenario",
    }
    assert set(testing.__all__).isdisjoint(effectrecon.__all__)
    assert all(not hasattr(effectrecon, name) for name in testing.__all__)


def test_before_dispatch_has_no_unknown_or_observation() -> None:
    effect = make_effect()
    fixture = build_scenario(AmbiguousOutcomeScenario.BEFORE_DISPATCH_FAILURE, effect)

    assert fixture.effect is effect
    assert fixture.dispatched is False
    assert fixture.unknown is None
    assert fixture.sources == ()


@pytest.mark.parametrize("scenario", tuple(AmbiguousOutcomeScenario))
@pytest.mark.asyncio
async def test_repeated_construction_and_downstream_semantics(
    scenario: AmbiguousOutcomeScenario,
) -> None:
    effect = make_effect()
    fixtures = (build_scenario(scenario, effect), build_scenario(scenario, effect))
    assert fixtures[0] == fixtures[1]
    previous: ReconciliationResult | None = None

    for fixture in fixtures:
        assert fixture.scenario is scenario
        assert fixture.effect is effect
        if fixture.unknown is None:
            assert scenario is AmbiguousOutcomeScenario.BEFORE_DISPATCH_FAILURE
            assert fixture.dispatched is False
            assert fixture.sources == ()
            continue

        assert fixture.dispatched is True
        assert fixture.unknown.effect is effect
        assert fixture.unknown.attempt_id == (
            f"testing:{scenario.value}:{effect.fingerprint}:attempt-1"
        )
        assert fixture.unknown.dispatched_at == datetime(
            2026, 1, 1, tzinfo=timezone.utc
        )
        assert fixture.unknown.detected_at == datetime(
            2026, 1, 1, 0, 0, 1, tzinfo=timezone.utc
        )
        expected_reason = (
            UnknownReason.CONNECTION_DROPPED
            if scenario is AmbiguousOutcomeScenario.DISCONNECT_DURING_DISPATCH
            else UnknownReason.RESPONSE_LOST
        )
        assert fixture.unknown.reason is expected_reason
        expected_outcome, expected_reconciliation_reason, expected_claims = (
            EXPECTED_RESULTS[scenario]
        )
        reconciler = Reconciler(fixture.sources)
        for _ in range(2):
            result = await reconciler.reconcile(fixture.unknown)
            assert result.effect is effect
            assert result.outcome is expected_outcome
            assert result.reason is expected_reconciliation_reason
            assert tuple(item.claim for item in result.evidence) == expected_claims
            if scenario is AmbiguousOutcomeScenario.OBSERVATION_UNAVAILABLE:
                assert result.evidence == ()
                assert result.observation_failures == (
                    ObservationFailure(
                        source="testing:observation_unavailable",
                        code="observation_failed",
                        message="Evidence source observation failed.",
                    ),
                )
            else:
                assert result.observation_failures == ()
                assert tuple(item.source for item in result.evidence) == tuple(
                    source.name for source in fixture.sources
                )
            for item in result.evidence:
                item.require_effect(effect)
                assert item.effect_fingerprint == effect.fingerprint
                assert item.evidence_id == (
                    f"{item.source}:{effect.fingerprint}:evidence-1"
                )
                assert item.observed_at == datetime(
                    2026, 1, 1, 0, 0, 2, tzinfo=timezone.utc
                )
                assert item.binding == {
                    "effect_fingerprint": effect.fingerprint,
                    "attempt_id": fixture.unknown.attempt_id,
                }
                assert item.metadata == {"scenario": scenario.value, "fixture": True}
            if previous is not None:
                assert semantic_result(result) == semantic_result(previous)
            previous = result


@pytest.mark.asyncio
async def test_fake_source_is_structural_and_captures_caller_list() -> None:
    fixture = build_scenario(
        AmbiguousOutcomeScenario.EXECUTED_RESPONSE_LOST, make_effect()
    )
    assert fixture.unknown is not None
    original = await fixture.sources[0].observe(fixture.unknown)
    caller_list = list(original)
    source = DeterministicEvidenceSource(original[0].source, caller_list)
    protocol_source: EvidenceSource = accepts_evidence_source(source)
    caller_list.clear()

    assert protocol_source is source
    assert EvidenceSource not in DeterministicEvidenceSource.__mro__
    assert iscoroutinefunction(source.observe)
    for _ in range(2):
        assert await protocol_source.observe(fixture.unknown) == original
    assert isinstance(await source.observe(fixture.unknown), tuple)
    with pytest.raises(FrozenInstanceError):
        setattr(source, "name", "changed")
    assert not hasattr(source, "__dict__")


@pytest.mark.asyncio
async def test_unavailable_source_raises_fixed_local_exception_repeatedly() -> None:
    fixture = build_scenario(
        AmbiguousOutcomeScenario.OBSERVATION_UNAVAILABLE, make_effect()
    )
    assert fixture.unknown is not None
    for _ in range(2):
        with pytest.raises(RuntimeError) as captured:
            await fixture.sources[0].observe(fixture.unknown)
        assert str(captured.value) == (
            "Observation unavailable in deterministic test fixture."
        )


@pytest.mark.parametrize(
    "majority", (EvidenceClaim.EXECUTED, EvidenceClaim.NOT_EXECUTED)
)
@pytest.mark.asyncio
async def test_contradiction_stays_indeterminate_with_unequal_source_counts(
    majority: EvidenceClaim,
) -> None:
    fixture = build_scenario(
        AmbiguousOutcomeScenario.CONTRADICTORY_EVIDENCE, make_effect()
    )
    assert fixture.unknown is not None
    sources = list(fixture.sources)
    for index in range(2):
        name = f"additional-test-source-{index}"
        sources.append(
            DeterministicEvidenceSource(
                name,
                (
                    Evidence(
                        evidence_id=f"additional-test-evidence-{index}",
                        source=name,
                        claim=majority,
                        effect_fingerprint=fixture.effect.fingerprint,
                        observed_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
                        remote_resource_id=None,
                        binding={"effect_fingerprint": fixture.effect.fingerprint},
                        metadata={"fixture": True},
                    ),
                ),
            )
        )
    for ordered_sources in (sources, list(reversed(sources))):
        result = await Reconciler(ordered_sources).reconcile(fixture.unknown)
        assert result.outcome is Outcome.INDETERMINATE
        assert result.reason is ReconciliationReason.CONTRADICTORY_EVIDENCE
        assert sum(item.claim is majority for item in result.evidence) == 3


@pytest.mark.asyncio
async def test_non_execution_does_not_authorize_retry() -> None:
    fixture = build_scenario(
        AmbiguousOutcomeScenario.NOT_EXECUTED_RESPONSE_LOST, make_effect()
    )
    assert fixture.unknown is not None
    result = await Reconciler(fixture.sources).reconcile(fixture.unknown)
    assert (
        ConservativeRetryPolicy().decide(result.outcome)
        is RetryDecision.REPLAN_REQUIRED
    )


@pytest.mark.parametrize("scenario", tuple(AmbiguousOutcomeScenario))
def test_fixture_is_immutable_and_slotted(scenario: AmbiguousOutcomeScenario) -> None:
    fixture: ScenarioFixture = build_scenario(scenario, make_effect())
    with pytest.raises(FrozenInstanceError):
        setattr(fixture, "dispatched", not fixture.dispatched)
    assert not hasattr(fixture, "__dict__")


@pytest.mark.parametrize("invalid", ("executed_response_lost", None, 1, object()))
def test_build_rejects_non_enum_scenarios(invalid: object) -> None:
    with pytest.raises(TypeError, match="scenario must be an AmbiguousOutcomeScenario"):
        build_scenario(invalid, make_effect())  # type: ignore[arg-type]


@pytest.mark.parametrize("invalid", ("effect", None, {}, object()))
def test_build_rejects_non_identity_effects(invalid: object) -> None:
    with pytest.raises(TypeError, match="effect must be an EffectIdentity"):
        build_scenario(
            AmbiguousOutcomeScenario.EXECUTED_RESPONSE_LOST,
            invalid,  # type: ignore[arg-type]
        )


@pytest.mark.asyncio
async def test_fixed_source_does_not_rebind_evidence_to_another_effect() -> None:
    first = build_scenario(
        AmbiguousOutcomeScenario.EXECUTED_RESPONSE_LOST, make_effect()
    )
    other = build_scenario(
        AmbiguousOutcomeScenario.EXECUTED_RESPONSE_LOST,
        EffectIdentity("send", "queue:other", {}, "request-2"),
    )
    assert other.unknown is not None
    with pytest.raises(ValueError, match="does not match"):
        await first.sources[0].observe(other.unknown)
