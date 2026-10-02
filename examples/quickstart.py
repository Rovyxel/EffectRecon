"""Offline example: a lost response, a read-only receipt, and a retry decision."""

import asyncio
from datetime import datetime, timezone

from effectrecon import (
    ConservativeRetryPolicy,
    EffectIdentity,
    Evidence,
    EvidenceClaim,
    EvidenceSource,
    Outcome,
    Reconciler,
    ReconciliationReason,
    ReconciliationResult,
    RetryDecision,
    UnknownOutcome,
    UnknownReason,
)


class ExampleReceiptSource:
    """Predetermined local receipt for illustration, not a provider adapter."""

    name = "example-receipts"

    def __init__(self, effect: EffectIdentity) -> None:
        self._receipt = Evidence(
            evidence_id="example-receipt-1",
            source=self.name,
            claim=EvidenceClaim.EXECUTED,
            effect_fingerprint=effect.fingerprint,
            observed_at=datetime(2026, 1, 1, 0, 0, 2, tzinfo=timezone.utc),
            remote_resource_id="example-message-1",
            binding={"effect_fingerprint": effect.fingerprint},
            metadata={"example": True},
        )

    async def observe(self, unknown: UnknownOutcome) -> tuple[Evidence, ...]:
        # Read the fixed receipt without sending, retrying, or changing anything.
        self._receipt.require_effect(unknown.effect)
        return (self._receipt,)


async def main() -> tuple[ReconciliationResult, RetryDecision]:
    effect = EffectIdentity("send", "queue:example", {"message": "hello"}, "example-1")

    # Assume caller code dispatched this effect and then lost the response.
    # This example performs no dispatch: it represents that uncertainty as data.
    unknown = UnknownOutcome(
        effect=effect,
        attempt_id="example-attempt-1",
        dispatched_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        detected_at=datetime(2026, 1, 1, 0, 0, 1, tzinfo=timezone.utc),
        reason=UnknownReason.RESPONSE_LOST,
    )
    source: EvidenceSource = ExampleReceiptSource(effect)  # Structural protocol.
    result: ReconciliationResult = await Reconciler([source]).reconcile(unknown)
    decision: RetryDecision = ConservativeRetryPolicy().decide(result.outcome)

    assert result.effect is effect
    assert result.outcome is Outcome.CONFIRMED_EXECUTED
    assert result.reason is ReconciliationReason.EXECUTION_CONFIRMED
    assert decision is RetryDecision.DO_NOT_RETRY
    assert result.observation_failures == ()
    # Decisions are data; neither reconciliation nor policy executes a retry.
    return result, decision


if __name__ == "__main__":
    result, decision = asyncio.run(main())
    print(f"{result.outcome.value} / {result.reason.value} -> {decision.value}")
