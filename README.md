# EffectRecon

EffectRecon provides deterministic identity primitives for describing intended
external side effects.

## Effect identity v1

`EffectIdentity` represents an operation, target, parameter mapping, and
idempotency key. It normalizes strings to Unicode NFC and exposes a compact,
sorted JSON document through `canonical_json`. Its `fingerprint` is the SHA-256
digest of that document's UTF-8 encoding, formatted as
`er1:sha256:<64 lowercase hexadecimal characters>`.

Supported parameter values are `None`, booleans, integers, strings, lists, and
dictionaries with string keys, including nested combinations of these values.
Floats, datetimes, bytes, and arbitrary Python objects are rejected. NFC
normalization that makes dictionary keys collide is also rejected. The
`parameters` and `to_dict()` accessors return detached copies, so modifying an
input mapping or returned value cannot change an existing identity.

Do not include API keys, access tokens, authentication tokens, session cookies,
passwords, credentials, or any other secrets in an `EffectIdentity`. The model
does not scan for or redact secrets; callers must keep authentication material
outside the identity.

## Unknown and outcome models

`UnknownOutcome` is immutable data for a dispatched attempt whose remote result
cannot yet be established. It retains the exact `EffectIdentity`, caller-supplied
attempt ID, dispatch and detection timestamps, and an `UnknownReason`. Both
timestamps must be timezone-aware, and detection cannot precede dispatch. A
known failure before dispatch is not an unknown outcome. Unknown does not mean
failure, authorize a retry, or imply that the effect executed or did not execute.

The enum values are public lowercase snake-case strings:

| Enum | Values |
| --- | --- |
| `UnknownReason` | `response_lost`, `connection_dropped`, `timeout_after_dispatch`, `client_crashed`, `provider_status_unknown`, `other` |
| `Outcome` | `confirmed_executed`, `confirmed_not_executed`, `indeterminate` |
| `ReconciliationReason` | `execution_confirmed`, `non_execution_confirmed`, `insufficient_evidence`, `contradictory_evidence`, `observation_failed` |

`Outcome.INDETERMINATE` represents neither confirmed execution nor confirmed
non-execution. Reconciliation reason values are labels only; the package does
not aggregate evidence or make reconciliation decisions.

## Evidence contract

`Evidence` is an immutable, provider-independent observation of one intended
effect. Its `effect_fingerprint` must use the same versioned format as
`EffectIdentity.fingerprint`. Call `evidence.require_effect(effect)` before
accepting evidence for a target; a mismatch raises `ValueError` and does not
change the evidence claim.

The `EvidenceSource` identified by `source` is responsible for validating
provider-specific semantics before producing a decisive `EvidenceClaim`.
`binding` contains the information validated to tie the observed state to the
intended effect. `metadata` is auxiliary and is never treated as proof by the
core model. The two mappings remain separate. `remote_resource_id` may be
`None` when an observation has no concrete resource, including absence-based
or inconclusive observations.

Evidence mappings accept nested mappings with string keys, lists or tuples,
and scalar values of `None`, `bool`, `int`, `float`, or `str`. Construction
copies them recursively; mappings are exposed read-only and sequences as
tuples, so neither input mutation nor accessor mutation can change evidence.
Other nested values and cyclic structures are rejected. No provider-specific
validation or reconciliation aggregation is performed here.

`ObservationFailure` is a separate immutable record of a failed observation
with a source, caller-supplied stable code, and human-readable message. It is
not evidence and does not imply that an effect did or did not occur.

## Evidence sources

`EvidenceSource` is a structural async protocol: an implementation exposes a
stable `name` and an `async observe(unknown)` method returning a sequence of
`Evidence`. Implementations do not need to inherit from EffectRecon or
register themselves. Observation is read-only: a source must not perform or
retry the original side effect or mutate provider state. Only provider-specific
semantic validation can justify a decisive claim. An empty search result alone
is not generally proof of non-execution; `INCONCLUSIVE` is a valid claim.
Observation failure is separate from evidence and must not be turned into
`NOT_EXECUTED`. The protocol documents these requirements but cannot enforce
the behavior of third-party sources.

## Reconciliation

`Reconciler` observes its registered `EvidenceSource` instances concurrently.
It captures source registration order at construction and normalizes results in
that order, preserving each source's evidence order even when observations
complete in a different order. Ordinary observation exceptions are retained
separately as `ObservationFailure`; they do not imply non-execution or discard
decisive evidence from another source. Cancellation propagates to the caller
and stops child observations.

Evidence claims are combined as follows:

| Evidence | Result |
| --- | --- |
| `EXECUTED` only | `CONFIRMED_EXECUTED` / `EXECUTION_CONFIRMED` |
| `NOT_EXECUTED` only | `CONFIRMED_NOT_EXECUTED` / `NON_EXECUTION_CONFIRMED` |
| Both decisive claims | `INDETERMINATE` / `CONTRADICTORY_EVIDENCE` |
| No decisive claim, with an observation failure | `INDETERMINATE` / `OBSERVATION_FAILED` |
| No decisive claim or observation failure | `INDETERMINATE` / `INSUFFICIENT_EVIDENCE` |

`INCONCLUSIVE` evidence is retained but is not decisive. Contradictory claims
remain indeterminate regardless of source count or order. Reconciliation
reports facts only; it does not authorize a retry.
