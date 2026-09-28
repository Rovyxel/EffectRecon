"""Provider-independent contracts for read-only evidence observation."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol

from effectrecon.evidence import Evidence
from effectrecon.unknown import UnknownOutcome


class EvidenceSource(Protocol):
    """An async, provider-independent source of evidence about an unknown attempt.

    Implementations observe external state only. They must not perform or retry
    the original side effect, mutate remote state, or create, update, or delete
    provider resources while observing. Provider-specific semantics must bind
    observed state to the intended effect before emitting decisive evidence.

    An empty search or list result alone is not generally proof of
    non-execution; an implementation may emit ``INCONCLUSIVE`` when the
    observation is not decisive. Observation failures are not evidence and must
    not be converted to ``NOT_EXECUTED``. This protocol cannot mechanically
    enforce the behavior of third-party implementations.
    """

    @property
    def name(self) -> str:
        """Return a stable source name used to identify evidence and diagnostics."""

    async def observe(self, unknown: UnknownOutcome) -> Sequence[Evidence]:
        """Read remote state for ``unknown`` without replaying its side effect."""
