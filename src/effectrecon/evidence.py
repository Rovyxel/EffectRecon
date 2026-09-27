"""Immutable, provider-independent observations about an intended effect."""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from enum import Enum, unique
from types import MappingProxyType
from typing import TypeAlias

from effectrecon.identity import EffectIdentity

_FINGERPRINT_PATTERN = re.compile(r"er1:sha256:[0-9a-f]{64}\Z")

FrozenEvidenceValue: TypeAlias = (
    str
    | int
    | float
    | bool
    | None
    | tuple["FrozenEvidenceValue", ...]
    | Mapping[str, "FrozenEvidenceValue"]
)


@unique
class EvidenceClaim(str, Enum):
    """Provider-validated semantic claim made by one observation."""

    EXECUTED = "executed"
    NOT_EXECUTED = "not_executed"
    INCONCLUSIVE = "inconclusive"


@dataclass(frozen=True, slots=True, init=False)
class Evidence:
    """An immutable observation bound to the fingerprint of one intended effect.

    ``source`` identifies the EvidenceSource that produced this result. That
    source is responsible for provider-specific semantic validation before it
    emits a decisive ``claim``. ``binding`` stores the validated information
    that ties the observed state to this effect; ``metadata`` stores auxiliary
    information and is never interpreted as proof by this model.

    ``remote_resource_id`` is optional because an inconclusive or absence-based
    observation may not identify a concrete remote resource.

    ``binding`` and ``metadata`` accept nested mappings with string keys,
    lists/tuples, and the scalar values ``None``, ``bool``, ``int``, ``float``,
    and ``str``. Mappings are copied to read-only mappings and sequences to
    tuples recursively. Other value types and cyclic structures are rejected.
    This shape preserves common provider data without imposing identity
    canonicalization, Unicode normalization, or serialization semantics.
    """

    evidence_id: str
    source: str
    claim: EvidenceClaim
    effect_fingerprint: str
    observed_at: datetime
    remote_resource_id: str | None
    binding: Mapping[str, FrozenEvidenceValue]
    metadata: Mapping[str, FrozenEvidenceValue]

    def __init__(
        self,
        evidence_id: str,
        source: str,
        claim: EvidenceClaim,
        effect_fingerprint: str,
        observed_at: datetime,
        remote_resource_id: str | None,
        binding: Mapping[str, object],
        metadata: Mapping[str, object],
    ) -> None:
        if not isinstance(evidence_id, str):
            raise TypeError("evidence_id must be a string")
        if not isinstance(source, str):
            raise TypeError("source must be a string")
        if not isinstance(claim, EvidenceClaim):
            raise TypeError("claim must be an EvidenceClaim")
        _validate_fingerprint(effect_fingerprint, "effect_fingerprint")
        _require_aware_datetime(observed_at)
        if remote_resource_id is not None and not isinstance(remote_resource_id, str):
            raise TypeError("remote_resource_id must be a string or None")

        frozen_binding = _freeze_mapping(binding, "binding")
        frozen_metadata = _freeze_mapping(metadata, "metadata")

        object.__setattr__(self, "evidence_id", evidence_id)
        object.__setattr__(self, "source", source)
        object.__setattr__(self, "claim", claim)
        object.__setattr__(self, "effect_fingerprint", effect_fingerprint)
        object.__setattr__(self, "observed_at", observed_at)
        object.__setattr__(self, "remote_resource_id", remote_resource_id)
        object.__setattr__(self, "binding", frozen_binding)
        object.__setattr__(self, "metadata", frozen_metadata)

    def require_effect(self, effect: EffectIdentity | str) -> None:
        """Raise ``ValueError`` unless this evidence targets ``effect``.

        ``effect`` may be the exact :class:`EffectIdentity` or its expected
        versioned fingerprint. This check does not infer or alter a claim.
        """
        if isinstance(effect, EffectIdentity):
            expected_fingerprint = effect.fingerprint
        elif isinstance(effect, str):
            _validate_fingerprint(effect, "expected fingerprint")
            expected_fingerprint = effect
        else:
            raise TypeError("effect must be an EffectIdentity or fingerprint string")

        if self.effect_fingerprint != expected_fingerprint:
            raise ValueError(
                "evidence effect_fingerprint does not match the expected effect"
            )


@dataclass(frozen=True, slots=True)
class ObservationFailure:
    """A failure to observe remote state, represented separately from Evidence."""

    source: str
    code: str
    message: str

    def __post_init__(self) -> None:
        if not isinstance(self.source, str):
            raise TypeError("source must be a string")
        if not isinstance(self.code, str):
            raise TypeError("code must be a string")
        if not isinstance(self.message, str):
            raise TypeError("message must be a string")


def _validate_fingerprint(value: object, name: str) -> None:
    if not isinstance(value, str):
        raise TypeError(f"{name} must be a string")
    if _FINGERPRINT_PATTERN.fullmatch(value) is None:
        raise ValueError(
            f"{name} must match er1:sha256:<64 lowercase hexadecimal characters>"
        )


def _require_aware_datetime(value: object) -> None:
    if not isinstance(value, datetime):
        raise TypeError("observed_at must be a datetime")
    try:
        offset = value.utcoffset()
    except Exception as exc:
        raise ValueError("observed_at must have a usable timezone offset") from exc
    if offset is None:
        raise ValueError("observed_at must be timezone-aware with a usable UTC offset")


def _freeze_mapping(value: object, name: str) -> Mapping[str, FrozenEvidenceValue]:
    if not isinstance(value, Mapping):
        raise TypeError(f"{name} must be a mapping with string keys")
    frozen = _freeze_value(value, path=name, active_ids=set())
    if not isinstance(frozen, Mapping):
        raise TypeError(f"{name} must be a mapping with string keys")
    return frozen


def _freeze_value(
    value: object, *, path: str, active_ids: set[int]
) -> FrozenEvidenceValue:
    if value is None or type(value) in {bool, int, float, str}:
        return value  # type: ignore[return-value]

    if isinstance(value, Mapping):
        object_id = id(value)
        if object_id in active_ids:
            raise ValueError(f"{path} must not contain cyclic mappings or sequences")
        active_ids.add(object_id)
        try:
            frozen_mapping: dict[str, FrozenEvidenceValue] = {}
            for key, item in value.items():
                if not isinstance(key, str):
                    raise TypeError(f"{path} must contain only string mapping keys")
                frozen_mapping[key] = _freeze_value(
                    item, path=f"{path}[{key!r}]", active_ids=active_ids
                )
            return MappingProxyType(frozen_mapping)
        finally:
            active_ids.remove(object_id)

    if isinstance(value, (list, tuple)):
        object_id = id(value)
        if object_id in active_ids:
            raise ValueError(f"{path} must not contain cyclic mappings or sequences")
        active_ids.add(object_id)
        try:
            return tuple(
                _freeze_value(item, path=f"{path}[{index}]", active_ids=active_ids)
                for index, item in enumerate(value)
            )
        finally:
            active_ids.remove(object_id)

    raise TypeError(
        f"{path} contains unsupported value type {type(value).__name__}; "
        "supported values are None, bool, int, float, str, mappings, lists, and tuples"
    )
