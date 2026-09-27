"""Immutable canonical identity for an intended external side effect."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from hashlib import sha256
from typing import ClassVar, Final, cast

from effectrecon._canonical import JSONValue, canonical_json, normalize_json_value

IDENTITY_SCHEMA: Final[str] = "effectrecon.identity.v1"


@dataclass(frozen=True, slots=True, init=False)
class EffectIdentity:
    """Describe one intended effect with stable canonical bytes and fingerprint.

    Parameters returned by :attr:`parameters` are detached copies. Mutating a
    returned value or the mapping supplied to the constructor cannot change
    this identity. Authentication material must never be included in an
    identity: callers must keep API keys, access or authentication tokens,
    session cookies, passwords, credentials, and all other secrets outside it.
    This class does not attempt to detect secrets.
    """

    schema: ClassVar[str] = IDENTITY_SCHEMA

    operation: str
    target: str
    idempotency_key: str
    _parameters_json: str = field(repr=False)
    _canonical_json: str = field(repr=False)
    _fingerprint: str = field(repr=False)

    def __init__(
        self,
        operation: str,
        target: str,
        parameters: dict[str, object],
        idempotency_key: str,
    ) -> None:
        normalized_operation = _normalize_identity_string(operation, "operation")
        normalized_target = _normalize_identity_string(target, "target")
        normalized_key = _normalize_identity_string(
            idempotency_key, "idempotency_key"
        )
        if type(parameters) is not dict:
            raise TypeError("parameters must be a dict with string keys")

        normalized_parameters = normalize_json_value(parameters, path="parameters")
        if type(normalized_parameters) is not dict:
            raise TypeError("parameters must be a dict with string keys")

        document: dict[str, JSONValue] = {
            "schema": IDENTITY_SCHEMA,
            "operation": normalized_operation,
            "target": normalized_target,
            "parameters": normalized_parameters,
            "idempotency_key": normalized_key,
        }
        serialized = canonical_json(document)
        parameters_serialized = canonical_json(normalized_parameters)
        fingerprint = f"er1:sha256:{sha256(serialized.encode('utf-8')).hexdigest()}"

        object.__setattr__(self, "operation", normalized_operation)
        object.__setattr__(self, "target", normalized_target)
        object.__setattr__(self, "idempotency_key", normalized_key)
        object.__setattr__(self, "_parameters_json", parameters_serialized)
        object.__setattr__(self, "_canonical_json", serialized)
        object.__setattr__(self, "_fingerprint", fingerprint)

    @property
    def parameters(self) -> dict[str, JSONValue]:
        """Return a detached copy of the normalized parameter mapping."""
        return cast(dict[str, JSONValue], json.loads(self._parameters_json))

    @property
    def canonical_json(self) -> str:
        """Return the exact canonical identity document as compact JSON."""
        return self._canonical_json

    @property
    def fingerprint(self) -> str:
        """Return the versioned SHA-256 fingerprint of :attr:`canonical_json`."""
        return self._fingerprint

    def to_dict(self) -> dict[str, JSONValue]:
        """Return a detached JSON-like copy of the canonical identity document."""
        return cast(dict[str, JSONValue], json.loads(self._canonical_json))


def _normalize_identity_string(value: str, name: str) -> str:
    normalized = normalize_json_value(value, path=name)
    if type(normalized) is not str:
        raise TypeError(f"{name} must be a string")
    return normalized
