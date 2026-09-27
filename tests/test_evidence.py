"""Tests for the immutable evidence and observation-failure contracts."""

from datetime import datetime, timedelta, timezone, tzinfo

import pytest

from effectrecon import (
    EffectIdentity,
    Evidence,
    EvidenceClaim,
    ObservationFailure,
)

FINGERPRINT = "er1:sha256:" + "a" * 64


def make_identity(*, target: str = "queue:alpha") -> EffectIdentity:
    return EffectIdentity(
        operation="send",
        target=target,
        parameters={"message": "hello"},
        idempotency_key="request-1",
    )


def make_evidence(
    *,
    evidence_id: str = "evidence-1",
    source: str = "queue-reader",
    claim: EvidenceClaim = EvidenceClaim.EXECUTED,
    effect_fingerprint: str = FINGERPRINT,
    observed_at: datetime | None = None,
    remote_resource_id: str | None = "message-42",
    binding: dict[str, object] | None = None,
    metadata: dict[str, object] | None = None,
) -> Evidence:
    return Evidence(
        evidence_id=evidence_id,
        source=source,
        claim=claim,
        effect_fingerprint=effect_fingerprint,
        observed_at=(
            datetime(2026, 1, 1, 12, tzinfo=timezone.utc)
            if observed_at is None
            else observed_at
        ),
        remote_resource_id=remote_resource_id,
        binding={"idempotency_key": "request-1"} if binding is None else binding,
        metadata={"region": "test"} if metadata is None else metadata,
    )


def test_evidence_claim_has_exact_members_and_stable_values() -> None:
    assert set(EvidenceClaim) == {
        EvidenceClaim.EXECUTED,
        EvidenceClaim.NOT_EXECUTED,
        EvidenceClaim.INCONCLUSIVE,
    }
    assert {member.name: member.value for member in EvidenceClaim} == {
        "EXECUTED": "executed",
        "NOT_EXECUTED": "not_executed",
        "INCONCLUSIVE": "inconclusive",
    }


def test_evidence_retains_fields_and_aware_utc_timestamp() -> None:
    timestamp = datetime(2026, 1, 1, 12, tzinfo=timezone.utc)
    binding = {"idempotency_key": "request-1"}
    metadata = {"region": "test"}

    evidence = make_evidence(
        evidence_id="evidence-42",
        source="queue-reader",
        claim=EvidenceClaim.NOT_EXECUTED,
        effect_fingerprint=FINGERPRINT,
        observed_at=timestamp,
        remote_resource_id=None,
        binding=binding,
        metadata=metadata,
    )

    assert evidence.evidence_id == "evidence-42"
    assert evidence.source == "queue-reader"
    assert evidence.claim is EvidenceClaim.NOT_EXECUTED
    assert evidence.effect_fingerprint == FINGERPRINT
    assert evidence.observed_at is timestamp
    assert evidence.remote_resource_id is None
    assert evidence.binding["idempotency_key"] == "request-1"
    assert evidence.metadata["region"] == "test"


def test_aware_non_utc_timestamp_is_preserved() -> None:
    timestamp = datetime(
        2026, 1, 1, 15, 30, tzinfo=timezone(timedelta(hours=3, minutes=30))
    )

    evidence = make_evidence(observed_at=timestamp)

    assert evidence.observed_at is timestamp
    assert evidence.observed_at.utcoffset() == timedelta(hours=3, minutes=30)


def test_naive_timestamp_is_rejected() -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        make_evidence(observed_at=datetime(2026, 1, 1, 12))


class NoOffset(tzinfo):
    """A tzinfo that cannot provide a usable UTC offset."""

    def utcoffset(self, dt: datetime | None) -> timedelta | None:
        return None

    def dst(self, dt: datetime | None) -> timedelta | None:
        return None

    def tzname(self, dt: datetime | None) -> str | None:
        return "no-offset"


def test_tzinfo_without_usable_offset_is_rejected() -> None:
    timestamp = datetime(2026, 1, 1, 12, tzinfo=NoOffset())

    with pytest.raises(ValueError, match="usable UTC offset"):
        make_evidence(observed_at=timestamp)


def test_non_datetime_timestamp_is_rejected() -> None:
    with pytest.raises(TypeError, match="observed_at must be a datetime"):
        make_evidence(observed_at="2026-01-01T12:00:00Z")  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "fingerprint",
    [
        "er1:sha256:" + "A" * 64,
        "er2:sha256:" + "a" * 64,
        "er1:sha256:" + "a" * 63,
        "er1:sha256:" + "a" * 65,
        "er1:sha256:" + "g" * 64,
    ],
)
def test_malformed_fingerprints_are_rejected(fingerprint: str) -> None:
    with pytest.raises(ValueError, match="effect_fingerprint must match"):
        make_evidence(effect_fingerprint=fingerprint)


def test_non_string_fingerprint_is_rejected_as_wrong_type() -> None:
    with pytest.raises(TypeError, match="effect_fingerprint must be a string"):
        make_evidence(effect_fingerprint=42)  # type: ignore[arg-type]


def test_effect_identity_fingerprint_is_accepted_and_matches() -> None:
    effect = make_identity()
    evidence = make_evidence(effect_fingerprint=effect.fingerprint)

    assert evidence.effect_fingerprint == effect.fingerprint
    evidence.require_effect(effect)
    evidence.require_effect(effect.fingerprint)


def test_mismatched_target_is_explicit_and_does_not_change_claim() -> None:
    intended_effect = make_identity(target="queue:alpha")
    other_effect = make_identity(target="queue:beta")
    evidence = make_evidence(
        claim=EvidenceClaim.EXECUTED,
        effect_fingerprint=intended_effect.fingerprint,
    )

    with pytest.raises(ValueError, match="does not match the expected effect"):
        evidence.require_effect(other_effect)

    assert evidence.claim is EvidenceClaim.EXECUTED


def test_invalid_expected_fingerprint_is_rejected() -> None:
    evidence = make_evidence()

    with pytest.raises(ValueError, match="expected fingerprint must match"):
        evidence.require_effect("not-a-fingerprint")


def test_binding_and_metadata_stay_separate_and_metadata_is_not_a_claim() -> None:
    evidence = make_evidence(
        claim=EvidenceClaim.INCONCLUSIVE,
        binding={"validated_key": "request-1"},
        metadata={"claim": "executed", "validated_key": "auxiliary-only"},
    )

    assert evidence.binding["validated_key"] == "request-1"
    assert evidence.metadata["claim"] == "executed"
    assert evidence.metadata["validated_key"] == "auxiliary-only"
    assert evidence.claim is EvidenceClaim.INCONCLUSIVE


def test_input_mapping_and_nested_mutations_do_not_change_evidence() -> None:
    nested = {"ids": ["original", {"count": 1}]}
    binding = {"validated": nested}
    metadata = {"provider": {"tags": ["original"]}}

    evidence = make_evidence(binding=binding, metadata=metadata)
    nested["ids"][0] = "changed"
    nested["ids"][1]["count"] = 2
    nested["ids"].append("later")
    binding["added"] = True
    metadata["provider"]["tags"].append("changed")

    stored_nested = evidence.binding["validated"]
    assert stored_nested["ids"][0] == "original"  # type: ignore[index]
    assert stored_nested["ids"][1]["count"] == 1  # type: ignore[index]
    assert len(stored_nested["ids"]) == 2  # type: ignore[index]
    assert "added" not in evidence.binding
    assert evidence.metadata["provider"]["tags"] == ("original",)  # type: ignore[index]


def test_public_mapping_accessors_and_nested_values_are_immutable() -> None:
    evidence = make_evidence(
        binding={"details": {"ids": ["id-1"]}},
        metadata={"labels": ["seen"]},
    )

    with pytest.raises(TypeError):
        evidence.binding["new"] = "value"  # type: ignore[index]
    with pytest.raises(TypeError):
        evidence.metadata["new"] = "value"  # type: ignore[index]
    with pytest.raises(TypeError):
        evidence.binding["details"]["new"] = "value"  # type: ignore[index]
    with pytest.raises(AttributeError):
        evidence.metadata["labels"].append("changed")  # type: ignore[union-attr]

    assert evidence.binding["details"]["ids"] == ("id-1",)  # type: ignore[index]
    assert evidence.metadata["labels"] == ("seen",)


def test_binding_and_metadata_require_string_keyed_mappings() -> None:
    with pytest.raises(TypeError, match="binding must be a mapping"):
        make_evidence(binding=["invalid"])  # type: ignore[arg-type]
    with pytest.raises(TypeError, match="string mapping keys"):
        make_evidence(binding={1: "invalid"})  # type: ignore[dict-item]
    with pytest.raises(TypeError, match="unsupported value type"):
        make_evidence(metadata={"unsupported": object()})


def test_cyclic_mapping_data_is_rejected() -> None:
    cyclic: dict[str, object] = {}
    cyclic["self"] = cyclic

    with pytest.raises(ValueError, match="cyclic"):
        make_evidence(binding=cyclic)


def test_evidence_is_frozen_and_slot_based() -> None:
    evidence = make_evidence()

    assert not hasattr(evidence, "__dict__")
    with pytest.raises(AttributeError):
        evidence.evidence_id = "evidence-2"  # type: ignore[misc]


def test_observation_failure_is_immutable_data_separate_from_evidence() -> None:
    failure = ObservationFailure(
        source="queue-reader",
        code="provider_unavailable",
        message="The read-only observation endpoint was unavailable.",
    )

    assert failure.source == "queue-reader"
    assert failure.code == "provider_unavailable"
    assert failure.message == "The read-only observation endpoint was unavailable."
    assert not isinstance(failure, Evidence)
    assert not hasattr(failure, "__dict__")
    with pytest.raises(AttributeError):
        failure.code = "not_executed"  # type: ignore[misc]


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("source", 1, "source must be a string"),
        ("code", None, "code must be a string"),
        ("message", 1, "message must be a string"),
    ],
)
def test_observation_failure_validates_field_types(
    field: str, value: object, message: str
) -> None:
    fields: dict[str, object] = {
        "source": "queue-reader",
        "code": "provider_error",
        "message": "Observation failed.",
    }
    fields[field] = value

    with pytest.raises(TypeError, match=message):
        ObservationFailure(**fields)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("evidence_id", 1, "evidence_id must be a string"),
        ("source", None, "source must be a string"),
        ("claim", "executed", "claim must be an EvidenceClaim"),
        ("remote_resource_id", 1, "remote_resource_id must be a string or None"),
    ],
)
def test_evidence_rejects_structurally_wrong_field_types(
    field: str, value: object, message: str
) -> None:
    fields: dict[str, object] = {
        "evidence_id": "evidence-1",
        "source": "queue-reader",
        "claim": EvidenceClaim.EXECUTED,
        "effect_fingerprint": FINGERPRINT,
        "observed_at": datetime(2026, 1, 1, 12, tzinfo=timezone.utc),
        "remote_resource_id": "message-42",
        "binding": {},
        "metadata": {},
    }
    fields[field] = value

    with pytest.raises(TypeError, match=message):
        Evidence(**fields)  # type: ignore[arg-type]
