"""Tests for deterministic, concurrent evidence reconciliation."""

import asyncio
from collections.abc import Sequence
from datetime import datetime, timezone
from typing import TypeAlias

import pytest

from effectrecon import (
    EffectIdentity,
    Evidence,
    EvidenceClaim,
    ObservationFailure,
    Outcome,
    Reconciler,
    ReconciliationReason,
    ReconciliationResult,
    UnknownOutcome,
    UnknownReason,
)

OBSERVED_AT = datetime(2026, 1, 1, 12, tzinfo=timezone.utc)
SourceSpec: TypeAlias = tuple[EvidenceClaim, ...] | None


def make_identity(*, target: str = "queue:alpha") -> EffectIdentity:
    return EffectIdentity(
        operation="send",
        target=target,
        parameters={"message": "hello"},
        idempotency_key="request-1",
    )


def make_unknown(*, effect: EffectIdentity | None = None) -> UnknownOutcome:
    return UnknownOutcome(
        effect=make_identity() if effect is None else effect,
        attempt_id="attempt-1",
        dispatched_at=OBSERVED_AT,
        detected_at=OBSERVED_AT,
        reason=UnknownReason.RESPONSE_LOST,
    )


def make_evidence(
    unknown: UnknownOutcome,
    *,
    evidence_id: str,
    source: str,
    claim: EvidenceClaim,
    effect: EffectIdentity | None = None,
) -> Evidence:
    return Evidence(
        evidence_id=evidence_id,
        source=source,
        claim=claim,
        effect_fingerprint=(unknown.effect if effect is None else effect).fingerprint,
        observed_at=OBSERVED_AT,
        remote_resource_id=None,
        binding={"attempt_id": unknown.attempt_id},
        metadata={},
    )


class FixedSource:
    """A small async source returning fixed evidence or raising an error."""

    def __init__(self, name: str, result: Sequence[Evidence] | Exception) -> None:
        self.name = name
        self.result = result

    async def observe(self, unknown: UnknownOutcome) -> Sequence[Evidence]:
        del unknown
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


def make_sources(
    unknown: UnknownOutcome, specifications: Sequence[SourceSpec]
) -> list[FixedSource]:
    sources: list[FixedSource] = []
    for index, claims in enumerate(specifications):
        name = f"source-{index}"
        if claims is None:
            sources.append(FixedSource(name, RuntimeError("remote read unavailable")))
            continue
        items = tuple(
            make_evidence(
                unknown,
                evidence_id=f"{name}-{evidence_index}",
                source=name,
                claim=claim,
            )
            for evidence_index, claim in enumerate(claims)
        )
        sources.append(FixedSource(name, items))
    return sources


@pytest.mark.parametrize(
    (
        "specifications",
        "expected_outcome",
        "expected_reason",
        "evidence_count",
        "failure_count",
    ),
    [
        (
            [(EvidenceClaim.EXECUTED,)],
            Outcome.CONFIRMED_EXECUTED,
            ReconciliationReason.EXECUTION_CONFIRMED,
            1,
            0,
        ),
        (
            [(EvidenceClaim.NOT_EXECUTED,)],
            Outcome.CONFIRMED_NOT_EXECUTED,
            ReconciliationReason.NON_EXECUTION_CONFIRMED,
            1,
            0,
        ),
        (
            [(EvidenceClaim.EXECUTED,), (EvidenceClaim.NOT_EXECUTED,)],
            Outcome.INDETERMINATE,
            ReconciliationReason.CONTRADICTORY_EVIDENCE,
            2,
            0,
        ),
        (
            [(EvidenceClaim.INCONCLUSIVE,)],
            Outcome.INDETERMINATE,
            ReconciliationReason.INSUFFICIENT_EVIDENCE,
            1,
            0,
        ),
        (
            [(EvidenceClaim.NOT_EXECUTED, EvidenceClaim.INCONCLUSIVE)],
            Outcome.CONFIRMED_NOT_EXECUTED,
            ReconciliationReason.NON_EXECUTION_CONFIRMED,
            2,
            0,
        ),
        ([], Outcome.INDETERMINATE, ReconciliationReason.INSUFFICIENT_EVIDENCE, 0, 0),
        ([None], Outcome.INDETERMINATE, ReconciliationReason.OBSERVATION_FAILED, 0, 1),
        (
            [(EvidenceClaim.INCONCLUSIVE,), None],
            Outcome.INDETERMINATE,
            ReconciliationReason.OBSERVATION_FAILED,
            1,
            1,
        ),
        (
            [(EvidenceClaim.EXECUTED,), None],
            Outcome.CONFIRMED_EXECUTED,
            ReconciliationReason.EXECUTION_CONFIRMED,
            1,
            1,
        ),
        (
            [(EvidenceClaim.NOT_EXECUTED,), None],
            Outcome.CONFIRMED_NOT_EXECUTED,
            ReconciliationReason.NON_EXECUTION_CONFIRMED,
            1,
            1,
        ),
        (
            [(EvidenceClaim.EXECUTED,), (EvidenceClaim.NOT_EXECUTED,), None],
            Outcome.INDETERMINATE,
            ReconciliationReason.CONTRADICTORY_EVIDENCE,
            2,
            1,
        ),
    ],
)
@pytest.mark.asyncio
async def test_reconciliation_truth_table(
    specifications: list[SourceSpec],
    expected_outcome: Outcome,
    expected_reason: ReconciliationReason,
    evidence_count: int,
    failure_count: int,
) -> None:
    unknown = make_unknown()
    result = await Reconciler(make_sources(unknown, specifications)).reconcile(unknown)

    assert result.outcome is expected_outcome
    assert result.reason is expected_reason
    assert len(result.evidence) == evidence_count
    assert len(result.observation_failures) == failure_count


@pytest.mark.asyncio
async def test_unequal_contradictory_evidence_has_no_majority_resolution() -> None:
    unknown = make_unknown()
    specifications: list[SourceSpec] = [
        (EvidenceClaim.EXECUTED,),
        (EvidenceClaim.NOT_EXECUTED,),
        (EvidenceClaim.NOT_EXECUTED,),
        (EvidenceClaim.NOT_EXECUTED,),
    ]

    result = await Reconciler(make_sources(unknown, specifications)).reconcile(unknown)

    assert result.outcome is Outcome.INDETERMINATE
    assert result.reason is ReconciliationReason.CONTRADICTORY_EVIDENCE


class CoordinatedSource:
    """A source whose observation is released explicitly by the test."""

    def __init__(
        self,
        name: str,
        *,
        source_count: int,
        started_names: list[str],
        all_started: asyncio.Event,
        release: asyncio.Event,
        finished: asyncio.Event,
        completion_order: list[str],
        evidence: Sequence[Evidence] = (),
        should_fail: bool = False,
    ) -> None:
        self.name = name
        self.source_count = source_count
        self.started_names = started_names
        self.all_started = all_started
        self.release = release
        self.finished = finished
        self.completion_order = completion_order
        self.evidence = evidence
        self.should_fail = should_fail

    async def observe(self, unknown: UnknownOutcome) -> Sequence[Evidence]:
        del unknown
        self.started_names.append(self.name)
        if len(self.started_names) == self.source_count:
            self.all_started.set()
        try:
            await self.release.wait()
            if self.should_fail:
                raise RuntimeError("source read failed")
            return self.evidence
        finally:
            self.completion_order.append(self.name)
            self.finished.set()


@pytest.mark.asyncio
async def test_observations_overlap_and_evidence_order_ignores_completion_order() -> (
    None
):
    unknown = make_unknown()
    started_names: list[str] = []
    completion_order: list[str] = []
    all_started = asyncio.Event()
    releases = {name: asyncio.Event() for name in ("A", "B", "C")}
    finished = {name: asyncio.Event() for name in ("A", "B", "C")}
    source_a_evidence = (
        make_evidence(
            unknown,
            evidence_id="A1",
            source="A",
            claim=EvidenceClaim.INCONCLUSIVE,
        ),
        make_evidence(
            unknown,
            evidence_id="A2",
            source="A",
            claim=EvidenceClaim.EXECUTED,
        ),
    )
    source_b_evidence = (
        make_evidence(
            unknown,
            evidence_id="B1",
            source="B",
            claim=EvidenceClaim.INCONCLUSIVE,
        ),
    )
    source_c_evidence = (
        make_evidence(
            unknown,
            evidence_id="C1",
            source="C",
            claim=EvidenceClaim.INCONCLUSIVE,
        ),
    )
    sources = [
        CoordinatedSource(
            name,
            source_count=3,
            started_names=started_names,
            all_started=all_started,
            release=releases[name],
            finished=finished[name],
            completion_order=completion_order,
            evidence=evidence,
        )
        for name, evidence in zip(
            ("A", "B", "C"),
            (source_a_evidence, source_b_evidence, source_c_evidence),
            strict=True,
        )
    ]
    task = asyncio.create_task(Reconciler(sources).reconcile(unknown))

    await asyncio.wait_for(all_started.wait(), timeout=2)
    for name in ("C", "A", "B"):
        releases[name].set()
        await asyncio.wait_for(finished[name].wait(), timeout=2)
    result = await task

    assert started_names == ["A", "B", "C"]
    assert completion_order == ["C", "A", "B"]
    assert result.evidence == source_a_evidence + source_b_evidence + source_c_evidence
    assert result.evidence[0] is source_a_evidence[0]
    assert result.evidence[1] is source_a_evidence[1]
    assert result.outcome is Outcome.CONFIRMED_EXECUTED


@pytest.mark.asyncio
async def test_multiple_failures_follow_registration_order_not_completion_order() -> (
    None
):
    unknown = make_unknown()
    started_names: list[str] = []
    completion_order: list[str] = []
    all_started = asyncio.Event()
    releases = {name: asyncio.Event() for name in ("A", "B", "C")}
    finished = {name: asyncio.Event() for name in ("A", "B", "C")}
    sources = [
        CoordinatedSource(
            name,
            source_count=3,
            started_names=started_names,
            all_started=all_started,
            release=releases[name],
            finished=finished[name],
            completion_order=completion_order,
            should_fail=True,
        )
        for name in ("A", "B", "C")
    ]
    task = asyncio.create_task(Reconciler(sources).reconcile(unknown))

    await asyncio.wait_for(all_started.wait(), timeout=2)
    for name in ("C", "A", "B"):
        releases[name].set()
        await asyncio.wait_for(finished[name].wait(), timeout=2)
    result = await task

    assert completion_order == ["C", "A", "B"]
    assert [failure.source for failure in result.observation_failures] == [
        "A",
        "B",
        "C",
    ]
    assert all(
        failure.code == "observation_failed" for failure in result.observation_failures
    )
    assert result.outcome is Outcome.INDETERMINATE
    assert result.reason is ReconciliationReason.OBSERVATION_FAILED


@pytest.mark.asyncio
async def test_caller_source_list_mutation_does_not_change_registration_order() -> None:
    unknown = make_unknown()
    source_a_evidence = make_evidence(
        unknown,
        evidence_id="A",
        source="A",
        claim=EvidenceClaim.EXECUTED,
    )
    source_b_evidence = make_evidence(
        unknown,
        evidence_id="B",
        source="B",
        claim=EvidenceClaim.INCONCLUSIVE,
    )
    sources = [
        FixedSource("A", (source_a_evidence,)),
        FixedSource("B", (source_b_evidence,)),
    ]
    reconciler = Reconciler(sources)
    sources.reverse()

    result = await reconciler.reconcile(unknown)

    assert [item.evidence_id for item in result.evidence] == ["A", "B"]


@pytest.mark.asyncio
async def test_wrong_effect_evidence_fails_explicitly() -> None:
    unknown = make_unknown()
    other_effect = make_identity(target="queue:beta")
    evidence = make_evidence(
        unknown,
        evidence_id="wrong-effect",
        source="source",
        claim=EvidenceClaim.EXECUTED,
        effect=other_effect,
    )

    with pytest.raises(ValueError, match="does not match the expected effect"):
        await Reconciler([FixedSource("source", (evidence,))]).reconcile(unknown)


@pytest.mark.asyncio
async def test_non_evidence_source_item_fails_explicitly() -> None:
    unknown = make_unknown()
    invalid_items = ("not evidence",)

    with pytest.raises(TypeError, match="non-Evidence item"):
        await Reconciler([FixedSource("source", invalid_items)]).reconcile(unknown)  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_non_sequence_source_output_fails_explicitly() -> None:
    unknown = make_unknown()

    with pytest.raises(TypeError, match="must return a sequence"):
        await Reconciler([FixedSource("source", 42)]).reconcile(unknown)  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_synchronous_observe_implementation_fails_explicitly() -> None:
    unknown = make_unknown()

    class SynchronousSource:
        name = "sync-source"

        def observe(self, unknown: UnknownOutcome) -> Sequence[Evidence]:
            del unknown
            return ()

    with pytest.raises(TypeError, match="observe must be async"):
        await Reconciler([SynchronousSource()]).reconcile(unknown)  # type: ignore[list-item]


@pytest.mark.asyncio
async def test_source_exception_is_safely_preserved_without_exception_details() -> None:
    unknown = make_unknown()
    source = FixedSource("private-source", RuntimeError("credential=do-not-disclose"))

    result = await Reconciler([source]).reconcile(unknown)

    assert result.observation_failures == (
        ObservationFailure(
            source="private-source",
            code="observation_failed",
            message="Evidence source observation failed.",
        ),
    )
    assert "do-not-disclose" not in result.observation_failures[0].message


@pytest.mark.asyncio
async def test_cancellation_propagates_and_cleans_up_child_observations() -> None:
    unknown = make_unknown()
    started_names: list[str] = []
    completion_order: list[str] = []
    all_started = asyncio.Event()
    never_release = asyncio.Event()
    finished = {name: asyncio.Event() for name in ("A", "B")}
    sources = [
        CoordinatedSource(
            name,
            source_count=2,
            started_names=started_names,
            all_started=all_started,
            release=never_release,
            finished=finished[name],
            completion_order=completion_order,
        )
        for name in ("A", "B")
    ]
    task = asyncio.create_task(Reconciler(sources).reconcile(unknown))
    await asyncio.wait_for(all_started.wait(), timeout=2)

    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert all(finished[name].is_set() for name in ("A", "B"))


def test_result_is_immutable_and_copies_collection_inputs() -> None:
    unknown = make_unknown()
    evidence = make_evidence(
        unknown,
        evidence_id="evidence-1",
        source="source",
        claim=EvidenceClaim.INCONCLUSIVE,
    )
    failure = ObservationFailure("source", "observation_failed", "Unavailable.")
    mutable_evidence = [evidence]
    mutable_failures = [failure]
    result = ReconciliationResult(
        effect=unknown.effect,
        outcome=Outcome.INDETERMINATE,
        reason=ReconciliationReason.OBSERVATION_FAILED,
        evidence=mutable_evidence,
        observation_failures=mutable_failures,
        started_at=OBSERVED_AT,
        completed_at=OBSERVED_AT,
    )
    mutable_evidence.clear()
    mutable_failures.clear()

    assert result.effect is unknown.effect
    assert result.evidence == (evidence,)
    assert result.observation_failures == (failure,)
    assert not hasattr(result, "__dict__")
    with pytest.raises(AttributeError):
        result.outcome = Outcome.CONFIRMED_EXECUTED  # type: ignore[misc]
    with pytest.raises(AttributeError):
        result.evidence.append(evidence)  # type: ignore[attr-defined]
    with pytest.raises(AttributeError):
        result.observation_failures.append(failure)  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_result_timestamps_are_aware_and_in_order() -> None:
    unknown = make_unknown()
    result = await Reconciler([]).reconcile(unknown)

    assert result.started_at.tzinfo is not None
    assert result.started_at.utcoffset() is not None
    assert result.completed_at.tzinfo is not None
    assert result.completed_at.utcoffset() is not None
    assert result.completed_at >= result.started_at
    assert result.effect is unknown.effect
    assert result.evidence == ()
    assert result.observation_failures == ()
    assert result.outcome is Outcome.INDETERMINATE
    assert result.reason is ReconciliationReason.INSUFFICIENT_EVIDENCE
