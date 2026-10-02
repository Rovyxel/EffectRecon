"""Run the documented example and verify its factual and policy results."""

import runpy
from pathlib import Path

import pytest

from effectrecon import (
    EvidenceClaim,
    Outcome,
    ReconciliationReason,
    ReconciliationResult,
    RetryDecision,
)


@pytest.mark.asyncio
async def test_quickstart_reports_execution_without_retry_authorization() -> None:
    example = Path(__file__).resolve().parents[1] / "examples" / "quickstart.py"
    namespace = runpy.run_path(str(example))
    result, decision = await namespace["main"]()

    assert isinstance(result, ReconciliationResult)
    assert result.outcome is Outcome.CONFIRMED_EXECUTED
    assert result.reason is ReconciliationReason.EXECUTION_CONFIRMED
    assert result.observation_failures == ()
    assert len(result.evidence) == 1
    assert result.evidence[0].claim is EvidenceClaim.EXECUTED
    result.evidence[0].require_effect(result.effect)
    assert decision is RetryDecision.DO_NOT_RETRY
