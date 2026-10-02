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
| No decisive claim | `INDETERMINATE` / `INSUFFICIENT_EVIDENCE` |

`INCONCLUSIVE` evidence is retained but is not decisive. Contradictory claims
remain indeterminate regardless of source count or order. Reconciliation
reports facts only; it does not authorize a retry. Observation failures remain
available in `observation_failures` and do not imply non-execution, including
when the reconciliation reason is `INSUFFICIENT_EVIDENCE`.

Before aggregation, Reconciler rejects mismatched effects and requires each
Evidence's `source` to match the observing source's registered name. Malformed
source output or an unexpected claim type raises `TypeError`; effect or source
mismatches raise `ValueError`. Duplicate `(source, evidence_id)` identities
within one reconciliation fail closed with `ValueError`, even when their claims
agree. The same textual `evidence_id` from different sources is distinct, and
identities may be reused in separate calls. Invalid evidence is never silently
rewritten or excluded. Contradictory decisive claims remain `INDETERMINATE`:
there is no majority or probabilistic resolution, and timestamps or auxiliary
metadata cannot choose a winner. Source failure never means non-execution.

## Retry decisions

`RetryPolicy` maps a factual `Outcome` to a caller-facing `RetryDecision`.
It is separate from `Reconciler`, whose results remain factual. The default
`ConservativeRetryPolicy` mapping is:

| Outcome | ConservativeRetryPolicy |
| --- | --- |
| `CONFIRMED_EXECUTED` | `DO_NOT_RETRY` |
| `CONFIRMED_NOT_EXECUTED` | `REPLAN_REQUIRED` |
| `INDETERMINATE` | `MANUAL_REVIEW` |

`CONFIRMED_NOT_EXECUTED` does not automatically mean retry: caller code may
need to re-read state or re-check preconditions before another attempt is safe.
`SAFE_TO_RETRY` is available for custom policy decisions, but
`ConservativeRetryPolicy` never emits it. EffectRecon does not execute or
schedule retries; caller code remains responsible for any action after
receiving a decision.

## Testing ambiguous outcomes

`effectrecon.testing` provides reusable unit-test fixtures with no network,
real sleep, live API, or provider SDK. These helpers do not dispatch effects
or implement a separate reconciler. `build_scenario(scenario, effect)` requires
an `AmbiguousOutcomeScenario` and an `EffectIdentity`, without string coercion.

| Scenario | Fixture / reconciliation result |
| --- | --- |
| `BEFORE_DISPATCH_FAILURE` | Not dispatched; no `UnknownOutcome` or sources; do not call Reconciler |
| `DISCONNECT_DURING_DISPATCH` | `CONNECTION_DROPPED`; inconclusive evidence; `INDETERMINATE` / `INSUFFICIENT_EVIDENCE` |
| `EXECUTED_RESPONSE_LOST` | `RESPONSE_LOST`; `CONFIRMED_EXECUTED` / `EXECUTION_CONFIRMED` |
| `NOT_EXECUTED_RESPONSE_LOST` | `RESPONSE_LOST`; `CONFIRMED_NOT_EXECUTED` / `NON_EXECUTION_CONFIRMED`; no retry authorization |
| `OBSERVATION_UNAVAILABLE` | Empty evidence, one observation failure; `INDETERMINATE` / `INSUFFICIENT_EVIDENCE` |
| `CONTRADICTORY_EVIDENCE` | Both decisive claims; `INDETERMINATE` / `CONTRADICTORY_EVIDENCE` |

`ScenarioFixture` is immutable and slotted, exposing `scenario`, `effect`,
`dispatched`, `unknown`, and a tuple of `sources`. All five post-dispatch
fixtures retain the exact supplied effect and use fixed aware UTC timestamps,
deterministic attempt/evidence IDs, and effect-bound evidence. Repeated
construction and observation preserve semantic content and order; Reconciler's
runtime start/completion timestamps are outside that guarantee.

`DeterministicEvidenceSource(name, evidence=(), *, observation_unavailable=False)`
captures an evidence sequence immutably and implements the async `EvidenceSource`
protocol without inheritance. Its immediate `observe(unknown)` returns fixed
evidence for the bound effect. The keyword-only `observation_unavailable=True`
mode instead raises a fixed local `RuntimeError`; the ordinary Reconciler
retains its normal `ObservationFailure`. No external state is read or changed.

```python
import asyncio

from effectrecon import EffectIdentity, Outcome, Reconciler
from effectrecon.testing import AmbiguousOutcomeScenario, build_scenario


async def check_ambiguous_effect():
    effect = EffectIdentity("send", "queue:test", {"message": "hello"}, "request-1")
    scenario = build_scenario(AmbiguousOutcomeScenario.EXECUTED_RESPONSE_LOST, effect)
    if scenario.unknown is not None:
        result = await Reconciler(scenario.sources).reconcile(scenario.unknown)
        assert result.outcome is Outcome.CONFIRMED_EXECUTED


asyncio.run(check_ambiguous_effect())
```
