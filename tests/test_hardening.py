"""Fail-closed reconciliation regressions for untrusted source evidence."""

import asyncio
from collections.abc import Sequence
from datetime import datetime, timedelta, timezone
from typing import cast

import pytest

import effectrecon.reconciler as reconciler_module
from effectrecon import (
    EffectIdentity,
    Evidence,
    EvidenceClaim,
    EvidenceSource,
    ObservationFailure,
    Outcome,
    Reconciler,
    ReconciliationReason,
    UnknownOutcome,
    UnknownReason,
)

OBSERVED_AT = datetime(2026, 1, 1, tzinfo=timezone.utc)


def make_unknown() -> UnknownOutcome:
    return UnknownOutcome(
        effect=EffectIdentity("send", "queue:test", {}, "request-1"),
        attempt_id="attempt-1",
        dispatched_at=OBSERVED_AT,
        detected_at=OBSERVED_AT,
        reason=UnknownReason.RESPONSE_LOST,
    )


def make_evidence(
    unknown: UnknownOutcome,
    *,
    source: str = "source-a",
    evidence_id: str = "42",
    claim: EvidenceClaim = EvidenceClaim.EXECUTED,
    observed_at: datetime = OBSERVED_AT,
    metadata: dict[str, object] | None = None,
    effect: EffectIdentity | None = None,
) -> Evidence:
    return Evidence(
        evidence_id=evidence_id,
        source=source,
        claim=claim,
        effect_fingerprint=(unknown.effect if effect is None else effect).fingerprint,
        observed_at=observed_at,
        remote_resource_id=None,
        binding={"attempt_id": unknown.attempt_id},
        metadata={} if metadata is None else metadata,
    )


class OutputSource:
    """Async structural source permitting deliberately invalid test output."""

    def __init__(self, name: str, output: object) -> None:
        self.name = name
        self.output = output

    async def observe(self, unknown: UnknownOutcome) -> Sequence[Evidence]:
        del unknown
        if isinstance(self.output, Exception):
            raise self.output
        # Deliberately cross the typed protocol boundary to exercise validation.
        return cast(Sequence[Evidence], self.output)


@pytest.mark.parametrize(
    ("invalid", "error", "message"),
    [
        ("wrong-effect", ValueError, "does not match the expected effect"),
        ("wrong-source", ValueError, "does not match the observing EvidenceSource"),
        ("non-evidence", TypeError, "non-Evidence item"),
        ("non-sequence", TypeError, "must return a sequence"),
        ("unexpected-claim", TypeError, "claim must be an EvidenceClaim"),
        ("duplicate-identical", ValueError, "Duplicate Evidence identity"),
        ("duplicate-conflicting", ValueError, "Duplicate Evidence identity"),
    ],
)
@pytest.mark.parametrize("invalid_first", (False, True))
@pytest.mark.asyncio
async def test_invalid_evidence_fails_before_aggregation_without_silent_exclusion(
    invalid: str,
    error: type[Exception],
    message: str,
    invalid_first: bool,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    unknown = make_unknown()
    item = make_evidence(unknown)
    output: object = (item,)
    if invalid == "wrong-effect":
        item = make_evidence(
            unknown, effect=EffectIdentity("send", "queue:other", {}, "request-2")
        )
        output = (item,)
    elif invalid == "wrong-source":
        item = make_evidence(unknown, source="source-b")
        output = (item,)
    elif invalid == "non-evidence":
        output = (item, "not evidence")
    elif invalid == "non-sequence":
        output = 42
    elif invalid == "unexpected-claim":
        object.__setattr__(item, "claim", "executed")
    elif invalid == "duplicate-identical":
        output = (item, item)
    elif invalid == "duplicate-conflicting":
        output = (item, make_evidence(unknown, claim=EvidenceClaim.NOT_EXECUTED))

    def aggregation_must_not_run(
        evidence: Sequence[Evidence],
    ) -> tuple[Outcome, ReconciliationReason]:
        pytest.fail("Invalid evidence reached aggregation")

    monkeypatch.setattr(reconciler_module, "_aggregate", aggregation_must_not_run)
    sources: list[EvidenceSource] = [
        OutputSource("source-a", output),
        OutputSource("source-valid", (make_evidence(unknown, source="source-valid"),)),
    ]
    if not invalid_first:
        sources.reverse()
    with pytest.raises(error, match=message):
        await Reconciler(sources).reconcile(unknown)
    assert item.source == ("source-b" if invalid == "wrong-source" else "source-a")
    if invalid == "wrong-effect":
        assert item.effect_fingerprint != unknown.effect.fingerprint


@pytest.mark.parametrize(
    "claim", ("executed", "not_executed", "inconclusive", None, 42)
)
@pytest.mark.asyncio
async def test_forged_claim_is_explicit_type_error(claim: object) -> None:
    unknown = make_unknown()
    item = make_evidence(unknown)
    object.__setattr__(item, "claim", claim)
    with pytest.raises(TypeError, match="claim must be an EvidenceClaim"):
        await Reconciler([OutputSource("source-a", (item,))]).reconcile(unknown)


@pytest.mark.asyncio
async def test_returned_observation_failure_is_not_a_caught_observation_exception() -> (
    None
):
    returned = ObservationFailure("source-a", "invalid-return", "Not an exception")
    with pytest.raises(TypeError, match="must return a sequence of Evidence"):
        await Reconciler([OutputSource("source-a", returned)]).reconcile(make_unknown())


@pytest.mark.parametrize("claim", tuple(EvidenceClaim))
@pytest.mark.parametrize("different_metadata", (False, True))
@pytest.mark.parametrize("include_inconclusive", (False, True))
@pytest.mark.asyncio
async def test_duplicate_identity_rejected_despite_claim_or_metadata(
    claim: EvidenceClaim, different_metadata: bool, include_inconclusive: bool
) -> None:
    unknown = make_unknown()
    first = make_evidence(unknown, claim=claim)
    second = make_evidence(
        unknown, claim=claim, metadata={"copy": True} if different_metadata else {}
    )
    items = [first, second]
    if include_inconclusive:
        items.insert(
            1,
            make_evidence(
                unknown, evidence_id="unrelated", claim=EvidenceClaim.INCONCLUSIVE
            ),
        )
    with pytest.raises(ValueError, match="Duplicate Evidence identity"):
        await Reconciler([OutputSource("source-a", items)]).reconcile(unknown)


@pytest.mark.asyncio
async def test_duplicate_identity_across_same_named_source_registrations() -> None:
    unknown = make_unknown()
    item = make_evidence(unknown)
    with pytest.raises(ValueError, match="Duplicate Evidence identity"):
        await Reconciler(
            [OutputSource("source-a", (item,)), OutputSource("source-a", (item,))]
        ).reconcile(unknown)


@pytest.mark.parametrize(
    ("second_claim", "outcome", "reason"),
    [
        (
            EvidenceClaim.EXECUTED,
            Outcome.CONFIRMED_EXECUTED,
            ReconciliationReason.EXECUTION_CONFIRMED,
        ),
        (
            EvidenceClaim.NOT_EXECUTED,
            Outcome.INDETERMINATE,
            ReconciliationReason.CONTRADICTORY_EVIDENCE,
        ),
    ],
)
@pytest.mark.asyncio
async def test_same_textual_id_from_different_sources_is_distinct(
    second_claim: EvidenceClaim, outcome: Outcome, reason: ReconciliationReason
) -> None:
    unknown = make_unknown()
    first = make_evidence(unknown, evidence_id="shared")
    second = make_evidence(
        unknown, source="source-b", evidence_id="shared", claim=second_claim
    )
    result = await Reconciler(
        [OutputSource("source-a", (first,)), OutputSource("source-b", (second,))]
    ).reconcile(unknown)
    assert result.evidence == (first, second)
    assert result.outcome is outcome
    assert result.reason is reason
    assert result.observation_failures == ()


@pytest.mark.parametrize(
    "majority", (EvidenceClaim.EXECUTED, EvidenceClaim.NOT_EXECUTED)
)
@pytest.mark.parametrize("single_source", (False, True))
@pytest.mark.asyncio
async def test_unique_contradiction_has_no_majority_resolution(
    majority: EvidenceClaim, single_source: bool
) -> None:
    unknown = make_unknown()
    minority = (
        EvidenceClaim.NOT_EXECUTED
        if majority is EvidenceClaim.EXECUTED
        else EvidenceClaim.EXECUTED
    )
    items = tuple(
        make_evidence(
            unknown,
            source="source-a" if single_source else f"source-{index}",
            evidence_id=str(index),
            claim=claim,
        )
        for index, claim in enumerate((majority, majority, majority, minority))
    )
    sources = (
        [OutputSource("source-a", items)]
        if single_source
        else [OutputSource(item.source, (item,)) for item in items]
    )
    for order in (sources, list(reversed(sources))):
        result = await Reconciler(order).reconcile(unknown)
        assert result.outcome is Outcome.INDETERMINATE
        assert result.reason is ReconciliationReason.CONTRADICTORY_EVIDENCE
        assert sum(item.claim is majority for item in result.evidence) == 3
        assert len(result.evidence) == 4


@pytest.mark.parametrize(
    ("claim", "outcome", "reason"),
    [
        (None, Outcome.INDETERMINATE, ReconciliationReason.INSUFFICIENT_EVIDENCE),
        (
            EvidenceClaim.INCONCLUSIVE,
            Outcome.INDETERMINATE,
            ReconciliationReason.INSUFFICIENT_EVIDENCE,
        ),
        (
            EvidenceClaim.EXECUTED,
            Outcome.CONFIRMED_EXECUTED,
            ReconciliationReason.EXECUTION_CONFIRMED,
        ),
        (
            EvidenceClaim.NOT_EXECUTED,
            Outcome.CONFIRMED_NOT_EXECUTED,
            ReconciliationReason.NON_EXECUTION_CONFIRMED,
        ),
    ],
)
@pytest.mark.parametrize("failure_first", (False, True))
@pytest.mark.parametrize("error", (RuntimeError, ValueError, TypeError))
@pytest.mark.asyncio
async def test_source_failure_is_retained_redacted_and_does_not_decide(
    claim: EvidenceClaim | None,
    outcome: Outcome,
    reason: ReconciliationReason,
    failure_first: bool,
    error: type[Exception],
) -> None:
    unknown = make_unknown()
    failure = OutputSource("unavailable", error("private-diagnostic-marker"))
    items = () if claim is None else (make_evidence(unknown, claim=claim),)
    sources: list[EvidenceSource] = [OutputSource("source-a", items), failure]
    if failure_first:
        sources.reverse()
    result = await Reconciler(sources).reconcile(unknown)
    assert result.outcome is outcome
    assert result.reason is reason
    assert result.evidence == items
    assert result.observation_failures == (
        ObservationFailure(
            "unavailable", "observation_failed", "Evidence source observation failed."
        ),
    )
    assert "private-diagnostic-marker" not in repr(result)


@pytest.mark.parametrize("case", ("inconclusive", "empty", "zero-sources"))
@pytest.mark.asyncio
async def test_missing_decisive_evidence_never_implies_non_execution(case: str) -> None:
    unknown = make_unknown()
    items = (
        (make_evidence(unknown, claim=EvidenceClaim.INCONCLUSIVE),)
        if case == "inconclusive"
        else ()
    )
    sources = [] if case == "zero-sources" else [OutputSource("source-a", items)]
    result = await Reconciler(sources).reconcile(unknown)
    assert result.outcome is Outcome.INDETERMINATE
    assert result.reason is ReconciliationReason.INSUFFICIENT_EVIDENCE
    assert result.evidence == items
    assert result.observation_failures == ()


@pytest.mark.parametrize(
    "metadata",
    [
        {"executed": True, "status": "success", "confidence": 0.99},
        {"not_executed": True, "claim": "not_executed", "confidence": 0.99},
    ],
)
@pytest.mark.asyncio
async def test_auxiliary_metadata_is_not_proof(metadata: dict[str, object]) -> None:
    unknown = make_unknown()
    item = make_evidence(unknown, claim=EvidenceClaim.INCONCLUSIVE, metadata=metadata)
    result = await Reconciler([OutputSource("source-a", (item,))]).reconcile(unknown)
    assert result.outcome is Outcome.INDETERMINATE
    assert result.reason is ReconciliationReason.INSUFFICIENT_EVIDENCE
    assert result.evidence == (item,)
    assert item.claim is EvidenceClaim.INCONCLUSIVE
    assert item.metadata == metadata


@pytest.mark.parametrize("newer", (EvidenceClaim.EXECUTED, EvidenceClaim.NOT_EXECUTED))
@pytest.mark.asyncio
async def test_recency_and_metadata_do_not_break_contradiction(
    newer: EvidenceClaim,
) -> None:
    unknown = make_unknown()
    items = tuple(
        make_evidence(
            unknown,
            evidence_id=claim.value,
            claim=claim,
            observed_at=OBSERVED_AT + timedelta(days=1 if claim is newer else 0),
            metadata={"status": "success", "confidence": 1 if claim is newer else 0},
        )
        for claim in (EvidenceClaim.EXECUTED, EvidenceClaim.NOT_EXECUTED)
    )
    for order in (items, tuple(reversed(items))):
        result = await Reconciler([OutputSource("source-a", order)]).reconcile(unknown)
        assert result.outcome is Outcome.INDETERMINATE
        assert result.reason is ReconciliationReason.CONTRADICTORY_EVIDENCE
        assert result.evidence == order


@pytest.mark.asyncio
async def test_duplicate_state_is_local_to_each_reconciliation() -> None:
    unknown = make_unknown()
    item = make_evidence(unknown)
    source = OutputSource("source-a", (item,))
    reconciler = Reconciler([source])
    for _ in range(2):
        result = await reconciler.reconcile(unknown)
        assert result.outcome is Outcome.CONFIRMED_EXECUTED
        assert result.reason is ReconciliationReason.EXECUTION_CONFIRMED
        assert result.evidence == (item,)
    source.output = (item, item)
    with pytest.raises(ValueError, match="Duplicate Evidence identity"):
        await reconciler.reconcile(unknown)
    source.output = (item,)
    result = await reconciler.reconcile(unknown)
    assert result.outcome is Outcome.CONFIRMED_EXECUTED


@pytest.mark.asyncio
async def test_child_cancellation_propagates_and_cleans_up_sibling() -> None:
    unknown = make_unknown()
    started = asyncio.Event()
    finished = asyncio.Event()
    never_release = asyncio.Event()

    class WaitingSource:
        name = "waiting"

        async def observe(self, unknown: UnknownOutcome) -> Sequence[Evidence]:
            started.set()
            try:
                await never_release.wait()
                return ()
            finally:
                finished.set()

    class CancellingSource:
        name = "cancelling"

        async def observe(self, unknown: UnknownOutcome) -> Sequence[Evidence]:
            await started.wait()
            raise asyncio.CancelledError

    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(
            Reconciler([WaitingSource(), CancellingSource()]).reconcile(unknown),
            timeout=2,
        )
    assert finished.is_set()


@pytest.mark.parametrize("reverse_registration", (False, True))
@pytest.mark.parametrize("reverse_completion", (False, True))
@pytest.mark.asyncio
async def test_contradiction_is_independent_of_observation_completion_order(
    reverse_registration: bool, reverse_completion: bool
) -> None:
    unknown = make_unknown()
    all_started = asyncio.Event()
    started_names: list[str] = []
    completed_names: list[str] = []
    releases = {name: asyncio.Event() for name in ("source-a", "source-b")}
    finished = {name: asyncio.Event() for name in releases}

    class ControlledSource:
        def __init__(self, name: str, claim: EvidenceClaim) -> None:
            self.name = name
            self.evidence = make_evidence(unknown, source=name, claim=claim)

        async def observe(self, unknown: UnknownOutcome) -> Sequence[Evidence]:
            started_names.append(self.name)
            if len(started_names) == 2:
                all_started.set()
            await releases[self.name].wait()
            completed_names.append(self.name)
            finished[self.name].set()
            return (self.evidence,)

    sources = [
        ControlledSource("source-a", EvidenceClaim.EXECUTED),
        ControlledSource("source-b", EvidenceClaim.NOT_EXECUTED),
    ]
    if reverse_registration:
        sources.reverse()
    completion_order = list(releases)
    if reverse_completion:
        completion_order.reverse()
    task = asyncio.create_task(Reconciler(sources).reconcile(unknown))
    try:
        await asyncio.wait_for(all_started.wait(), timeout=2)
        for name in completion_order:
            releases[name].set()
            await asyncio.wait_for(finished[name].wait(), timeout=2)
        result = await task
    finally:
        if not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
    assert completed_names == completion_order
    assert tuple(item.source for item in result.evidence) == tuple(
        source.name for source in sources
    )
    assert result.outcome is Outcome.INDETERMINATE
    assert result.reason is ReconciliationReason.CONTRADICTORY_EVIDENCE
