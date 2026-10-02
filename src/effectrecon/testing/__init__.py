"""Offline, deterministic fixtures for ambiguous external-effect boundaries."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum, unique

from effectrecon.evidence import Evidence, EvidenceClaim
from effectrecon.identity import EffectIdentity
from effectrecon.unknown import UnknownOutcome, UnknownReason

__all__ = [
    "AmbiguousOutcomeScenario",
    "ScenarioFixture",
    "DeterministicEvidenceSource",
    "build_scenario",
]

_DISPATCHED_AT = datetime(2026, 1, 1, tzinfo=timezone.utc)
_DETECTED_AT = datetime(2026, 1, 1, 0, 0, 1, tzinfo=timezone.utc)
_OBSERVED_AT = datetime(2026, 1, 1, 0, 0, 2, tzinfo=timezone.utc)


@unique
class AmbiguousOutcomeScenario(str, Enum):
    """Stable test scenarios, including a known pre-dispatch control case."""

    BEFORE_DISPATCH_FAILURE = "before_dispatch_failure"
    DISCONNECT_DURING_DISPATCH = "disconnect_during_dispatch"
    EXECUTED_RESPONSE_LOST = "executed_response_lost"
    NOT_EXECUTED_RESPONSE_LOST = "not_executed_response_lost"
    OBSERVATION_UNAVAILABLE = "observation_unavailable"
    CONTRADICTORY_EVIDENCE = "contradictory_evidence"


@dataclass(frozen=True, slots=True, init=False)
class DeterministicEvidenceSource:
    """Structural async fake returning captured evidence immediately.

    Evidence is already immutable; the input sequence is copied to a tuple.
    ``observation_unavailable=True`` raises a fixed local ``RuntimeError``
    instead of returning evidence. No observation performs an external effect.
    """

    name: str
    _evidence: tuple[Evidence, ...]
    _observation_unavailable: bool

    def __init__(
        self,
        name: str,
        evidence: Sequence[Evidence] = (),
        *,
        observation_unavailable: bool = False,
    ) -> None:
        if not isinstance(name, str):
            raise TypeError("name must be a string")
        if not isinstance(evidence, Sequence):
            raise TypeError("evidence must be a sequence of Evidence")
        captured = tuple(evidence)
        if any(not isinstance(item, Evidence) for item in captured):
            raise TypeError("evidence must contain only Evidence instances")
        if type(observation_unavailable) is not bool:
            raise TypeError("observation_unavailable must be a bool")
        if observation_unavailable and captured:
            raise ValueError("unavailable observation cannot also configure evidence")
        object.__setattr__(self, "name", name)
        object.__setattr__(self, "_evidence", captured)
        object.__setattr__(self, "_observation_unavailable", observation_unavailable)

    async def observe(self, unknown: UnknownOutcome) -> tuple[Evidence, ...]:
        """Return fixed evidence for its bound effect, or raise a local failure."""
        if not isinstance(unknown, UnknownOutcome):
            raise TypeError("unknown must be an UnknownOutcome")
        if self._observation_unavailable:
            raise RuntimeError("Observation unavailable in deterministic test fixture.")
        for item in self._evidence:
            item.require_effect(unknown.effect)
        return self._evidence


@dataclass(frozen=True, slots=True)
class ScenarioFixture:
    """Immutable inputs for downstream tests using the ordinary Reconciler.

    The pre-dispatch control has no unknown outcome and no sources. Every
    other scenario retains the exact supplied effect in an unknown outcome.
    """

    scenario: AmbiguousOutcomeScenario
    effect: EffectIdentity
    dispatched: bool
    unknown: UnknownOutcome | None
    sources: tuple[DeterministicEvidenceSource, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.scenario, AmbiguousOutcomeScenario):
            raise TypeError("scenario must be an AmbiguousOutcomeScenario")
        if not isinstance(self.effect, EffectIdentity):
            raise TypeError("effect must be an EffectIdentity")
        if type(self.dispatched) is not bool:
            raise TypeError("dispatched must be a bool")
        captured = tuple(self.sources)
        if any(
            not isinstance(source, DeterministicEvidenceSource) for source in captured
        ):
            raise TypeError("sources must contain only DeterministicEvidenceSource")
        before_dispatch = (
            self.scenario is AmbiguousOutcomeScenario.BEFORE_DISPATCH_FAILURE
        )
        if before_dispatch:
            if self.dispatched or self.unknown is not None or captured:
                raise ValueError(
                    "pre-dispatch failure has no unknown outcome or sources"
                )
        elif not self.dispatched or not isinstance(self.unknown, UnknownOutcome):
            raise ValueError("post-dispatch scenarios require a dispatched unknown")
        elif self.unknown.effect is not self.effect:
            raise ValueError("unknown must retain the exact fixture effect")
        object.__setattr__(self, "sources", captured)


def build_scenario(
    scenario: AmbiguousOutcomeScenario, effect: EffectIdentity
) -> ScenarioFixture:
    """Build a fixed test fixture without dispatching or observing anything.

    Strings are not coerced into enum members or identities. IDs depend only
    on the scenario and effect fingerprint; timestamps are fixed aware UTC.
    """
    if not isinstance(scenario, AmbiguousOutcomeScenario):
        raise TypeError("scenario must be an AmbiguousOutcomeScenario")
    if not isinstance(effect, EffectIdentity):
        raise TypeError("effect must be an EffectIdentity")
    if scenario is AmbiguousOutcomeScenario.BEFORE_DISPATCH_FAILURE:
        return ScenarioFixture(scenario, effect, False, None, ())

    unknown = UnknownOutcome(
        effect=effect,
        attempt_id=f"testing:{scenario.value}:{effect.fingerprint}:attempt-1",
        dispatched_at=_DISPATCHED_AT,
        detected_at=_DETECTED_AT,
        reason=(
            UnknownReason.CONNECTION_DROPPED
            if scenario is AmbiguousOutcomeScenario.DISCONNECT_DURING_DISPATCH
            else UnknownReason.RESPONSE_LOST
        ),
    )
    sources: tuple[DeterministicEvidenceSource, ...]
    if scenario is AmbiguousOutcomeScenario.OBSERVATION_UNAVAILABLE:
        sources = (
            DeterministicEvidenceSource(
                "testing:observation_unavailable", observation_unavailable=True
            ),
        )
    else:
        claims = {
            AmbiguousOutcomeScenario.DISCONNECT_DURING_DISPATCH: (
                EvidenceClaim.INCONCLUSIVE,
            ),
            AmbiguousOutcomeScenario.EXECUTED_RESPONSE_LOST: (EvidenceClaim.EXECUTED,),
            AmbiguousOutcomeScenario.NOT_EXECUTED_RESPONSE_LOST: (
                EvidenceClaim.NOT_EXECUTED,
            ),
            AmbiguousOutcomeScenario.CONTRADICTORY_EVIDENCE: (
                EvidenceClaim.EXECUTED,
                EvidenceClaim.NOT_EXECUTED,
            ),
        }[scenario]
        sources = tuple(_build_source(scenario, unknown, claim) for claim in claims)
    return ScenarioFixture(scenario, effect, True, unknown, sources)


def _build_source(
    scenario: AmbiguousOutcomeScenario, unknown: UnknownOutcome, claim: EvidenceClaim
) -> DeterministicEvidenceSource:
    name = f"testing:{scenario.value}:{claim.value}"
    evidence = Evidence(
        evidence_id=f"{name}:{unknown.effect.fingerprint}:evidence-1",
        source=name,
        claim=claim,
        effect_fingerprint=unknown.effect.fingerprint,
        observed_at=_OBSERVED_AT,
        remote_resource_id=None,
        binding={
            "effect_fingerprint": unknown.effect.fingerprint,
            "attempt_id": unknown.attempt_id,
        },
        metadata={"scenario": scenario.value, "fixture": True},
    )
    return DeterministicEvidenceSource(name, (evidence,))
