"""Tests for canonical EffectIdentity v1 behavior."""

import re
from datetime import datetime, timezone

import pytest

from effectrecon import EffectIdentity, IDENTITY_SCHEMA


def make_identity(
    parameters: dict[str, object],
    *,
    operation: str = "send",
    target: str = "queue:alpha",
    idempotency_key: str = "req-1",
) -> EffectIdentity:
    return EffectIdentity(
        operation=operation,
        target=target,
        parameters=parameters,
        idempotency_key=idempotency_key,
    )


def test_simple_golden_vector() -> None:
    identity = make_identity({})

    assert identity.canonical_json == (
        '{"idempotency_key":"req-1","operation":"send",'
        '"parameters":{},"schema":"effectrecon.identity.v1",'
        '"target":"queue:alpha"}'
    )
    assert identity.fingerprint == (
        "er1:sha256:e61ae9f64a44d1b9a9cb257f44b6bd628c95eeec9135249be7cb728d1a360778"
    )


def test_nested_golden_vector() -> None:
    identity = make_identity(
        {"z": [True, None, 3], "a": {"é": "café"}},
        operation="publish",
        target="topic",
        idempotency_key="job-7",
    )

    assert identity.canonical_json == (
        '{"idempotency_key":"job-7","operation":"publish",'
        '"parameters":{"a":{"é":"café"},"z":[true,null,3]},'
        '"schema":"effectrecon.identity.v1","target":"topic"}'
    )
    assert identity.fingerprint == (
        "er1:sha256:85513fb257feceaf54dcdcf1a1cad2570ee410136cbebb755b7eff15e0c5a889"
    )


def test_unicode_golden_vector_normalizes_fields_keys_and_values() -> None:
    identity = make_identity(
        {"e\u0301": "A\u030a"},
        operation="cafe\u0301",
        target="obj",
        idempotency_key="k",
    )

    assert identity.canonical_json == (
        '{"idempotency_key":"k","operation":"café",'
        '"parameters":{"é":"Å"},"schema":"effectrecon.identity.v1",'
        '"target":"obj"}'
    )
    assert identity.fingerprint == (
        "er1:sha256:32e949b2abc20a03d88d71f98e3e7a6598b48237a5f993c22bc5f0719fd4d02e"
    )


def test_mapping_key_order_is_canonical_at_every_level() -> None:
    first = make_identity({"b": 2, "a": {"y": 4, "x": 3}})
    second = make_identity({"a": {"x": 3, "y": 4}, "b": 2})

    assert first.canonical_json == (
        '{"idempotency_key":"req-1","operation":"send",'
        '"parameters":{"a":{"x":3,"y":4},"b":2},'
        '"schema":"effectrecon.identity.v1","target":"queue:alpha"}'
    )
    assert first.fingerprint == (
        "er1:sha256:7a4d74be17bfb8e55b229c369574ea5648c225aa5ca19406eb36976158739724"
    )
    assert first.canonical_json == second.canonical_json
    assert first.fingerprint == second.fingerprint


def test_reordered_input_mappings_produce_identical_identity() -> None:
    first = make_identity({"beta": [1, {"b": 2, "a": 3}], "alpha": True})
    second = make_identity({"alpha": True, "beta": [1, {"a": 3, "b": 2}]})

    assert first.canonical_json == second.canonical_json
    assert first.fingerprint == second.fingerprint


def test_different_semantic_identities_have_different_canonical_input() -> None:
    first = make_identity({"count": 1})
    second = make_identity({"count": 2})

    assert first.canonical_json != second.canonical_json
    assert first.fingerprint != second.fingerprint


@pytest.mark.parametrize(
    ("first", "second"),
    [
        ("é", "e\u0301"),
        ("Å", "A\u030a"),
    ],
)
def test_unicode_equivalent_identity_strings_normalize(first: str, second: str) -> None:
    left = make_identity(
        {"value": "e\u0301"},
        operation=first,
        target="cafe\u0301",
        idempotency_key="A\u030a",
    )
    right = make_identity(
        {"value": "é"},
        operation=second,
        target="café",
        idempotency_key="Å",
    )

    assert left.operation == right.operation
    assert left.target == right.target
    assert left.idempotency_key == right.idempotency_key
    assert left.parameters == right.parameters
    assert left.fingerprint == right.fingerprint


def test_unicode_normalization_applies_to_dictionary_keys() -> None:
    identity = make_identity({"e\u0301": "value"})

    assert identity.parameters == {"é": "value"}


def test_nfc_induced_key_collision_is_rejected() -> None:
    with pytest.raises(ValueError, match="collide after NFC"):
        make_identity({"é": 1, "e\u0301": 2})


def test_nested_nfc_induced_key_collision_is_rejected() -> None:
    with pytest.raises(ValueError, match="collide after NFC"):
        make_identity({"nested": {"é": 1, "e\u0301": 2}})


def test_non_string_dictionary_key_is_rejected() -> None:
    with pytest.raises(TypeError, match="non-string dictionary key"):
        make_identity({1: "value"})  # type: ignore[dict-item]


@pytest.mark.parametrize(
    "value",
    [
        object(),
        b"secret",
        datetime(2026, 1, 1, tzinfo=timezone.utc),
        1.25,
        float("nan"),
        float("inf"),
        float("-inf"),
        (1, 2),
    ],
)
def test_unsupported_values_are_rejected(value: object) -> None:
    with pytest.raises(TypeError, match="unsupported value type"):
        make_identity({"value": value})


def test_bool_and_int_are_supported_as_distinct_json_types() -> None:
    boolean = make_identity({"value": True})
    integer = make_identity({"value": 1})

    assert '"value":true' in boolean.canonical_json
    assert '"value":1' in integer.canonical_json
    assert boolean.canonical_json != integer.canonical_json


def test_supported_nested_lists_and_dictionaries_are_preserved() -> None:
    identity = make_identity({"values": [None, False, 0, "text", {"inner": [3]}]})

    assert identity.parameters == {
        "values": [None, False, 0, "text", {"inner": [3]}]
    }


def test_fingerprint_has_required_versioned_lowercase_format() -> None:
    identity = make_identity({"value": 1})

    assert re.fullmatch(r"er1:sha256:[0-9a-f]{64}", identity.fingerprint)
    assert identity.schema == IDENTITY_SCHEMA
    assert IDENTITY_SCHEMA == "effectrecon.identity.v1"


def test_repeated_construction_is_deterministic() -> None:
    first = make_identity({"nested": [1, {"ok": True}]})
    second = make_identity({"nested": [1, {"ok": True}]})

    assert first.fingerprint == (
        "er1:sha256:5f6e1c6c96a6abf5e7214d2d8eb54f5f35f0d968c424213d20e849768b1086fa"
    )
    assert first.canonical_json == second.canonical_json
    assert first.fingerprint == second.fingerprint


def test_input_and_returned_nested_mutations_do_not_change_identity() -> None:
    supplied = {"nested": [{"value": 1}]}
    identity = make_identity(supplied)
    established_json = identity.canonical_json
    established_fingerprint = identity.fingerprint

    supplied["nested"][0]["value"] = 2
    returned = identity.parameters
    returned["nested"][0]["value"] = 3

    assert identity.canonical_json == established_json
    assert identity.fingerprint == established_fingerprint
    assert identity.parameters == {"nested": [{"value": 1}]}


def test_identity_attributes_are_immutable() -> None:
    identity = make_identity({})

    with pytest.raises(AttributeError):
        identity.operation = "delete"  # type: ignore[misc]


def test_to_dict_returns_a_detached_document() -> None:
    identity = make_identity({"nested": [1]})
    document = identity.to_dict()
    document["parameters"]["nested"].append(2)  # type: ignore[index, union-attr]

    assert identity.parameters == {"nested": [1]}
