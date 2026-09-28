"""Concurrent, deterministic reconciliation of read-only evidence."""

from __future__ import annotations

import asyncio
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from inspect import isawaitable

from effectrecon.evidence import Evidence, EvidenceClaim, ObservationFailure
from effectrecon.identity import EffectIdentity
from effectrecon.outcomes import Outcome, ReconciliationReason
from effectrecon.sources import EvidenceSource
from effectrecon.unknown import UnknownOutcome

_OBSERVATION_ERROR_CODE = "observation_failed"
_OBSERVATION_ERROR_MESSAGE = "Evidence source observation failed."


@dataclass(frozen=True, slots=True, init=False)
class ReconciliationResult:
    """Immutable factual result of reconciling one unknown external effect."""

    effect: EffectIdentity
    outcome: Outcome
    reason: ReconciliationReason
    evidence: tuple[Evidence, ...]
    observation_failures: tuple[ObservationFailure, ...]
    started_at: datetime
    completed_at: datetime

    def __init__(
        self,
        effect: EffectIdentity,
        outcome: Outcome,
        reason: ReconciliationReason,
        evidence: Sequence[Evidence],
        observation_failures: Sequence[ObservationFailure],
        started_at: datetime,
        completed_at: datetime,
    ) -> None:
        if not isinstance(effect, EffectIdentity):
            raise TypeError("effect must be an EffectIdentity")
        if not isinstance(outcome, Outcome):
            raise TypeError("outcome must be an Outcome")
        if not isinstance(reason, ReconciliationReason):
            raise TypeError("reason must be a ReconciliationReason")
        if not isinstance(evidence, Sequence):
            raise TypeError("evidence must be a sequence")
        if not isinstance(observation_failures, Sequence):
            raise TypeError("observation_failures must be a sequence")
        if any(not isinstance(item, Evidence) for item in evidence):
            raise TypeError("evidence must contain only Evidence instances")
        if any(
            not isinstance(item, ObservationFailure) for item in observation_failures
        ):
            raise TypeError(
                "observation_failures must contain only ObservationFailure instances"
            )
        _require_aware_datetime(started_at, "started_at")
        _require_aware_datetime(completed_at, "completed_at")
        if completed_at < started_at:
            raise ValueError("completed_at must not precede started_at")

        object.__setattr__(self, "effect", effect)
        object.__setattr__(self, "outcome", outcome)
        object.__setattr__(self, "reason", reason)
        object.__setattr__(self, "evidence", tuple(evidence))
        object.__setattr__(self, "observation_failures", tuple(observation_failures))
        object.__setattr__(self, "started_at", started_at)
        object.__setattr__(self, "completed_at", completed_at)


class Reconciler:
    """Observe registered evidence sources concurrently and combine their claims.

    Sources are captured in registration order. Their observations run
    concurrently, while output evidence and failures retain that registration
    order. Reconciliation reports facts only and does not authorize retries.
    """

    __slots__ = ("_sources",)

    def __init__(self, sources: Sequence[EvidenceSource]) -> None:
        captured_sources = tuple(sources)
        registered: list[tuple[EvidenceSource, str]] = []
        for source in captured_sources:
            name = source.name
            if not isinstance(name, str):
                raise TypeError("EvidenceSource.name must be a string")
            if not callable(source.observe):
                raise TypeError("EvidenceSource.observe must be callable")
            registered.append((source, name))
        self._sources = tuple(registered)

    async def reconcile(self, unknown: UnknownOutcome) -> ReconciliationResult:
        """Collect and reconcile evidence for ``unknown`` without retrying it."""
        if not isinstance(unknown, UnknownOutcome):
            raise TypeError("unknown must be an UnknownOutcome")

        started_at = datetime.now(timezone.utc)
        observations = await self._observe_all(unknown)

        evidence: list[Evidence] = []
        failures: list[ObservationFailure] = []
        for (source, source_name), observation in zip(
            self._sources, observations, strict=True
        ):
            if isinstance(observation, ObservationFailure):
                failures.append(observation)
                continue
            if not isinstance(observation, Sequence):
                raise TypeError(
                    f"EvidenceSource {source_name!r} must return a sequence of Evidence"
                )

            source_evidence = tuple(observation)
            for item in source_evidence:
                if not isinstance(item, Evidence):
                    raise TypeError(
                        f"EvidenceSource {source_name!r} returned a non-Evidence item"
                    )
                item.require_effect(unknown.effect)
            evidence.extend(source_evidence)

        outcome, reason = _aggregate(evidence)
        completed_at = max(datetime.now(timezone.utc), started_at)
        return ReconciliationResult(
            effect=unknown.effect,
            outcome=outcome,
            reason=reason,
            evidence=evidence,
            observation_failures=failures,
            started_at=started_at,
            completed_at=completed_at,
        )

    async def _observe_all(
        self, unknown: UnknownOutcome
    ) -> list[Sequence[Evidence] | ObservationFailure]:
        tasks = [
            asyncio.create_task(_observe_source(source, name, unknown))
            for source, name in self._sources
        ]
        try:
            return list(await asyncio.gather(*tasks))
        except BaseException:
            for task in tasks:
                if not task.done():
                    task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            raise


async def _observe_source(
    source: EvidenceSource, source_name: str, unknown: UnknownOutcome
) -> Sequence[Evidence] | ObservationFailure:
    """Catch only ordinary exceptions raised while invoking one source."""
    try:
        pending_observation = source.observe(unknown)
    except Exception:
        return _observation_failure(source_name)
    if not isawaitable(pending_observation):
        raise TypeError(f"EvidenceSource {source_name!r}.observe must be async")
    try:
        return await pending_observation
    except Exception:
        return _observation_failure(source_name)


def _observation_failure(source_name: str) -> ObservationFailure:
    return ObservationFailure(
        source=source_name,
        code=_OBSERVATION_ERROR_CODE,
        message=_OBSERVATION_ERROR_MESSAGE,
    )


def _aggregate(evidence: Sequence[Evidence]) -> tuple[Outcome, ReconciliationReason]:
    has_executed = any(item.claim is EvidenceClaim.EXECUTED for item in evidence)
    has_not_executed = any(
        item.claim is EvidenceClaim.NOT_EXECUTED for item in evidence
    )

    if has_executed and has_not_executed:
        return Outcome.INDETERMINATE, ReconciliationReason.CONTRADICTORY_EVIDENCE
    if has_executed:
        return Outcome.CONFIRMED_EXECUTED, ReconciliationReason.EXECUTION_CONFIRMED
    if has_not_executed:
        return (
            Outcome.CONFIRMED_NOT_EXECUTED,
            ReconciliationReason.NON_EXECUTION_CONFIRMED,
        )
    return Outcome.INDETERMINATE, ReconciliationReason.INSUFFICIENT_EVIDENCE


def _require_aware_datetime(value: object, name: str) -> None:
    if not isinstance(value, datetime):
        raise TypeError(f"{name} must be a datetime")
    try:
        offset = value.utcoffset()
    except Exception as exc:
        raise ValueError(f"{name} must have a usable timezone offset") from exc
    if offset is None:
        raise ValueError(f"{name} must be timezone-aware with a usable UTC offset")
