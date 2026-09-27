"""Models for side-effect attempts whose remote outcome is not yet known."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum, unique

from effectrecon.identity import EffectIdentity


@unique
class UnknownReason(str, Enum):
    """Stable, serializable reasons why an attempt's outcome is unknown.

    The string values are part of the public API and are suitable for
    serialization. They are explicit so their stability does not depend on
    enum declaration order or Python implementation details.
    """

    RESPONSE_LOST = "response_lost"
    CONNECTION_DROPPED = "connection_dropped"
    TIMEOUT_AFTER_DISPATCH = "timeout_after_dispatch"
    CLIENT_CRASHED = "client_crashed"
    PROVIDER_STATUS_UNKNOWN = "provider_status_unknown"
    OTHER = "other"


@dataclass(frozen=True, slots=True)
class UnknownOutcome:
    """Represent normal post-dispatch ambiguity as immutable domain data.

    A known failure before dispatch is not an unknown outcome. This model does
    not authorize retries or imply that the effect executed or did not execute.
    ``attempt_id`` is retained as supplied and is not generated or normalized.
    """

    effect: EffectIdentity
    attempt_id: str
    dispatched_at: datetime
    detected_at: datetime
    reason: UnknownReason

    def __post_init__(self) -> None:
        if not isinstance(self.effect, EffectIdentity):
            raise TypeError("effect must be an EffectIdentity")
        if not isinstance(self.attempt_id, str):
            raise TypeError("attempt_id must be a string")
        if not isinstance(self.reason, UnknownReason):
            raise TypeError("reason must be an UnknownReason")

        _require_aware_datetime(self.dispatched_at, "dispatched_at")
        _require_aware_datetime(self.detected_at, "detected_at")

        dispatched_utc = self.dispatched_at.astimezone(timezone.utc)
        detected_utc = self.detected_at.astimezone(timezone.utc)
        if detected_utc < dispatched_utc:
            raise ValueError("detected_at must not precede dispatched_at")


def _require_aware_datetime(value: object, name: str) -> None:
    if not isinstance(value, datetime):
        raise TypeError(f"{name} must be a datetime")
    if value.utcoffset() is None:
        raise ValueError(f"{name} must be timezone-aware with a usable UTC offset")
