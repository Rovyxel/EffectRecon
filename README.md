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
