"""Validation and canonical serialization for supported JSON-like values."""

from __future__ import annotations

import json
import unicodedata
from typing import TypeAlias

JSONValue: TypeAlias = (
    str | int | bool | None | list["JSONValue"] | dict[str, "JSONValue"]
)


def normalize_string(value: str, *, path: str) -> str:
    """Return a string in Unicode NFC form."""
    if type(value) is not str:
        raise TypeError(f"{path} must be a string")
    return unicodedata.normalize("NFC", value)


def normalize_json_value(value: object, *, path: str = "$") -> JSONValue:
    """Validate and copy one supported value, normalizing every string."""
    if value is None:
        return None
    if type(value) is bool:
        return value
    if type(value) is int:
        return value
    if type(value) is str:
        return normalize_string(value, path=path)
    if type(value) is list:
        return [
            normalize_json_value(item, path=f"{path}[{index}]")
            for index, item in enumerate(value)
        ]
    if type(value) is dict:
        normalized: dict[str, JSONValue] = {}
        for key, item in value.items():
            if type(key) is not str:
                raise TypeError(f"{path} contains a non-string dictionary key")
            normalized_key = normalize_string(key, path=f"{path} key")
            if normalized_key in normalized:
                raise ValueError(
                    f"{path} contains dictionary keys that collide after "
                    "NFC normalization"
                )
            normalized[normalized_key] = normalize_json_value(
                item, path=f"{path}[{key!r}]"
            )
        return normalized

    raise TypeError(
        f"{path} contains unsupported value type {type(value).__name__}; "
        "supported values are null, bool, int, str, list, and dict[str, ...]"
    )


def canonical_json(value: object) -> str:
    """Serialize a supported JSON-like value deterministically."""
    normalized = normalize_json_value(value)
    return json.dumps(
        normalized,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    )
