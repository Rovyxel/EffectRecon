# EffectRecon

EffectRecon is a small Python library for reasoning about ambiguous outcomes of
external side effects. It provides deterministic effect identity, read-only
evidence observation contracts, factual reconciliation, conservative retry
decisions, and deterministic test fixtures.

Requires Python 3.11 or newer, with no unconditional runtime dependencies.
Licensed under MPL-2.0. The current version is `0.0.0.dev0`; these docs prepare
the v0.1 surface, not a released or frozen v0.1.0 API.

## The problem

Suppose a client sends a message to a queue and loses the response. The queue
may have accepted the message, or the request may never have reached it.
Transport failure alone cannot establish which happened. Sending the same
message again without checking can duplicate the external effect.

EffectRecon helps caller code describe the intended effect, observe evidence,
and distinguish confirmed execution, confirmed non-execution, and an
indeterminate result. The caller supplies provider integrations and remains
responsible for dispatch and any later action.

## Unknown is not Failure.

**Unknown is not Failure.** A timeout, disconnect, crash, or lost response after
dispatch does not prove non-execution and does not authorize a retry. A known
failure before dispatch is different: it is not an `UnknownOutcome`.

## Quick start

The complete flow is:

**ambiguous dispatch → `UnknownOutcome` → read-only observation →
`ReconciliationResult` → `RetryDecision`**

This runnable example assumes caller code dispatched an effect and lost its
response. It performs no actual dispatch. Its local example source holds a
predetermined receipt for that effect, illustrating
`EXECUTED` → `CONFIRMED_EXECUTED` → `DO_NOT_RETRY` without network, credentials,
an external account, sleep, or randomness. A real adapter must establish
execution using provider semantics rather than fabricate a receipt.

```python
"""Offline example: a lost response, a read-only receipt, and a retry decision."""

import asyncio
from datetime import datetime, timezone

from effectrecon import (
    ConservativeRetryPolicy,
    EffectIdentity,
    Evidence,
    EvidenceClaim,
    EvidenceSource,
    Outcome,
    Reconciler,
    ReconciliationReason,
    ReconciliationResult,
    RetryDecision,
    UnknownOutcome,
    UnknownReason,
)


class ExampleReceiptSource:
    """Predetermined local receipt for illustration, not a provider adapter."""

    name = "example-receipts"

    def __init__(self, effect: EffectIdentity) -> None:
        self._receipt = Evidence(
            evidence_id="example-receipt-1",
            source=self.name,
            claim=EvidenceClaim.EXECUTED,
            effect_fingerprint=effect.fingerprint,
            observed_at=datetime(2026, 1, 1, 0, 0, 2, tzinfo=timezone.utc),
            remote_resource_id="example-message-1",
            binding={"effect_fingerprint": effect.fingerprint},
            metadata={"example": True},
        )

    async def observe(self, unknown: UnknownOutcome) -> tuple[Evidence, ...]:
        # Read the fixed receipt without sending, retrying, or changing anything.
        self._receipt.require_effect(unknown.effect)
        return (self._receipt,)


async def main() -> tuple[ReconciliationResult, RetryDecision]:
    effect = EffectIdentity("send", "queue:example", {"message": "hello"}, "example-1")

    # Assume caller code dispatched this effect and then lost the response.
    # This example performs no dispatch: it represents that uncertainty as data.
    unknown = UnknownOutcome(
        effect=effect,
        attempt_id="example-attempt-1",
        dispatched_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        detected_at=datetime(2026, 1, 1, 0, 0, 1, tzinfo=timezone.utc),
        reason=UnknownReason.RESPONSE_LOST,
    )
    source: EvidenceSource = ExampleReceiptSource(effect)  # Structural protocol.
    result: ReconciliationResult = await Reconciler([source]).reconcile(unknown)
    decision: RetryDecision = ConservativeRetryPolicy().decide(result.outcome)

    assert result.effect is effect
    assert result.outcome is Outcome.CONFIRMED_EXECUTED
    assert result.reason is ReconciliationReason.EXECUTION_CONFIRMED
    assert decision is RetryDecision.DO_NOT_RETRY
    assert result.observation_failures == ()
    # Decisions are data; neither reconciliation nor policy executes a retry.
    return result, decision


if __name__ == "__main__":
    result, decision = asyncio.run(main())
    print(f"{result.outcome.value} / {result.reason.value} -> {decision.value}")
```

Run [examples/quickstart.py](examples/quickstart.py) from the repository root:

```sh
PYTHONPATH=src python examples/quickstart.py
```

Expected output:

```text
confirmed_executed / execution_confirmed -> do_not_retry
```

After installing this checkout with `python -m pip install .`, the example can
also run with `python examples/quickstart.py`, without `PYTHONPATH`.

## Mental model

| Stage | Responsibility |
| --- | --- |
| Intended effect | Caller creates an `EffectIdentity`; it executes nothing |
| Ambiguous dispatch | Caller records post-dispatch uncertainty in `UnknownOutcome` |
| Read-only observation | An `EvidenceSource` adapter validates provider observations and returns `Evidence` |
| Factual reconciliation | `Reconciler.reconcile(unknown)` validates evidence and returns `ReconciliationResult` |
| Caller-facing policy | `RetryPolicy.decide(outcome)` returns `RetryDecision`; caller code chooses any action |

The models, reconciliation, and policy are separate. Neither `Reconciler` nor
`RetryPolicy` performs the original side effect or executes a retry.

## EffectIdentity

`EffectIdentity(operation, target, parameters, idempotency_key)` describes an
intended effect. `operation`, `target`, and `idempotency_key` are strings;
`parameters` is a dictionary with string keys. An idempotency key does not make
a provider honor it or guarantee exactly-once execution.

Supported parameter values are exactly `None`, `bool`, `int`, `str`, `list`,
and `dict` with string keys, recursively. Floats, tuples, datetimes, bytes,
arbitrary objects, and non-string keys are rejected.

Strings, including nested keys and values, are normalized to Unicode NFC.
Keys colliding after normalization are rejected. `canonical_json` exposes
compact, sorted JSON containing the `effectrecon.identity.v1` schema, operation,
target, parameters, and idempotency key. `fingerprint` is the SHA-256 digest of
its UTF-8 bytes in this format:

```text
er1:sha256:<64 lowercase hexadecimal characters>
```

Equivalent normalized input produces the same canonical JSON and fingerprint.
The identity is immutable; `parameters` and `to_dict()` return detached copies.
Keep authentication material outside it; see security below.

## UnknownOutcome

`UnknownOutcome` is immutable post-dispatch uncertainty represented as data,
not an exception. It neither establishes execution nor authorizes retry.

| Field | Meaning |
| --- | --- |
| `effect` | The exact retained `EffectIdentity` instance |
| `attempt_id` | Caller-supplied string, retained without normalization |
| `dispatched_at` | Timezone-aware dispatch timestamp |
| `detected_at` | Timezone-aware timestamp when uncertainty was detected |
| `reason` | An `UnknownReason` member |

`detected_at` cannot precede `dispatched_at`, comparing their UTC instants.
`UnknownReason` members have lowercase string values:

| Member | Value |
| --- | --- |
| `RESPONSE_LOST` | `response_lost` |
| `CONNECTION_DROPPED` | `connection_dropped` |
| `TIMEOUT_AFTER_DISPATCH` | `timeout_after_dispatch` |
| `CLIENT_CRASHED` | `client_crashed` |
| `PROVIDER_STATUS_UNKNOWN` | `provider_status_unknown` |
| `OTHER` | `other` |

## Evidence and EvidenceSource

`Evidence` is an immutable observation about one intended effect.

| Field | Contract |
| --- | --- |
| `evidence_id` | String identifier scoped to `source` |
| `source` | String matching the observing source's registered name |
| `claim` | `EvidenceClaim.EXECUTED`, `NOT_EXECUTED`, or `INCONCLUSIVE` |
| `effect_fingerprint` | Explicit binding to an EffectRecon v1 fingerprint |
| `observed_at` | Timezone-aware observation timestamp |
| `remote_resource_id` | String or `None`, including for inconclusive or absence-based observations |
| `binding` | Mapping containing validated semantic linkage to the effect |
| `metadata` | Auxiliary mapping; never proof on its own |

`EvidenceClaim` values are `executed`, `not_executed`, and `inconclusive`.
Only the first two are decisive. `evidence.require_effect(effect)` accepts an
`EffectIdentity` or a valid expected fingerprint string and raises `ValueError`
on mismatch without altering evidence.

Evidence mappings accept nested mappings with string keys, lists or tuples,
and scalar `None`, `bool`, `int`, `float`, or `str` values. Construction copies
them recursively, exposing read-only mappings and tuples. Cycles and other
value types are rejected. These types differ from identity parameter types;
evidence data is not identity canonicalization.

`EvidenceSource` is a structural async protocol: a stable `name: str` and
`async observe(unknown: UnknownOutcome) -> Sequence[Evidence]`. Implementations
need no inheritance. Pass instances to `Reconciler([...])`; it captures their
names and registration order. Synchronous `observe` is rejected before
invocation, even if it would return an awaitable.

An adapter must observe read-only, without replaying the effect or mutating
provider state. Before making a decisive claim it must validate provider
semantics and the linkage recorded in `binding`. An empty search result alone
is not generally proof of non-execution; use `INCONCLUSIVE` when observation
is not decisive. The protocol documents these obligations but cannot enforce
the behavior of third-party adapters.

## Reconciliation

`Reconciler` observes sources concurrently, validates their evidence, and
aggregates claims deterministically. Its immutable `ReconciliationResult`
contains `effect`, `outcome`, `reason`, an `evidence` tuple, an
`observation_failures` tuple, and aware `started_at` / `completed_at` timestamps.
Output evidence and failures follow source registration order; each source's
evidence order is preserved independently of observation completion order.

`Outcome` is the factual result. `ReconciliationReason` explains why it was
produced. Reconciliation does not make retry-policy decisions.

| Validated decisive claims | Outcome | Reason |
| --- | --- | --- |
| `EXECUTED` only | `CONFIRMED_EXECUTED` | `EXECUTION_CONFIRMED` |
| `NOT_EXECUTED` only | `CONFIRMED_NOT_EXECUTED` | `NON_EXECUTION_CONFIRMED` |
| Both | `INDETERMINATE` | `CONTRADICTORY_EVIDENCE` |
| Neither | `INDETERMINATE` | `INSUFFICIENT_EVIDENCE` |

`INCONCLUSIVE` is retained but non-decisive. Empty evidence, zero sources,
failure-only observations, and inconclusive-plus-failure observations remain
indeterminate. Source failure does not prove non-execution or override valid
decisive evidence from another source.

Contradictions stay indeterminate: neither majority direction, source order,
completion order, timestamps, nor metadata chooses a winner. There is no
probabilistic confidence, weighting, LLM inference, or machine-learning
classification in reconciliation.

Public `Outcome` values are `confirmed_executed`, `confirmed_not_executed`,
and `indeterminate`. `ReconciliationReason` defines `execution_confirmed`,
`non_execution_confirmed`, `insufficient_evidence`, `contradictory_evidence`,
and `observation_failed`. The last is `ReconciliationReason.OBSERVATION_FAILED`;
the current `Reconciler` does not emit it as the result reason, because
observation failures are kept separately.

### Invalid evidence fails explicitly

| Invalid input | Exception |
| --- | --- |
| Non-sequence output, non-`Evidence` item, or unexpected claim type | `TypeError` |
| Evidence source differs from the observing source's registered name | `ValueError` |
| Evidence targets a different effect | `ValueError` |
| Duplicate `(source, evidence_id)` within one reconciliation | `ValueError` |

Validation precedes aggregation. Invalid evidence is never silently rewritten,
rebound, or discarded. Duplicate identity fails closed even when claims agree,
metadata differs, or unrelated valid evidence is present. Separate source
objects sharing a name use the same identity scope. A textual ID from different
source names is distinct; identities may be reused in separate calls.

### Observation failure and cancellation

`ObservationFailure(source, code, message)` is separate immutable data, not
`Evidence`. Callers constructing it directly choose their own string code and
message. During reconciliation an ordinary exception raised by observation is
normalized to a record with the registered source name, code
`observation_failed`, and message `Evidence source observation failed.` Raw
exception details are not copied into this normalized record.

A source must return a sequence of `Evidence`, not an `ObservationFailure`
object. Returning that object directly raises `TypeError`; it is different
from raising an observation exception. Errors from Reconciler's source-output
and evidence checks propagate instead of becoming observation failures. An
ordinary exception raised inside `observe`, including `TypeError` or
`ValueError`, follows the observation-exception normalization described above.

Cancellation propagates to the caller and pending child observations are
cleaned up. `asyncio.CancelledError` does not become an indeterminate result or
an `ObservationFailure`.

## Retry decisions

`RetryPolicy` is a separate structural synchronous protocol with
`decide(outcome: Outcome) -> RetryDecision`. `ConservativeRetryPolicy` maps:

| Factual outcome | Caller-facing decision |
| --- | --- |
| `CONFIRMED_EXECUTED` | `DO_NOT_RETRY` |
| `CONFIRMED_NOT_EXECUTED` | `REPLAN_REQUIRED` |
| `INDETERMINATE` | `MANUAL_REVIEW` |

**Confirmed non-execution does not automatically mean safe retry.** The caller
may need to re-read state or re-check preconditions before another attempt is
safe. `RetryDecision.SAFE_TO_RETRY` exists for custom caller policies;
`ConservativeRetryPolicy` never emits it. Decisions are data: EffectRecon does
not execute, schedule, or perform background retries.

`RetryDecision` values are `safe_to_retry`, `do_not_retry`, `replan_required`,
and `manual_review`.

## Testing ambiguous outcomes

Import the testing API from `effectrecon.testing`, not the root package:

- `AmbiguousOutcomeScenario`: the six scenario enum members below.
- `ScenarioFixture`: immutable, slotted data exposing `scenario`, `effect`,
  `dispatched`, `unknown`, and a tuple of `sources`.
- `DeterministicEvidenceSource`: structural async fake with captured evidence.
- `build_scenario(scenario, effect)`: requires an `AmbiguousOutcomeScenario`
  and an `EffectIdentity`, without string coercion.

| Scenario | Fixture / reconciliation result |
| --- | --- |
| `BEFORE_DISPATCH_FAILURE` | Not dispatched; no `UnknownOutcome` or sources; do not call `Reconciler` |
| `DISCONNECT_DURING_DISPATCH` | `CONNECTION_DROPPED`; inconclusive evidence; `INDETERMINATE` / `INSUFFICIENT_EVIDENCE` |
| `EXECUTED_RESPONSE_LOST` | `CONFIRMED_EXECUTED` / `EXECUTION_CONFIRMED` |
| `NOT_EXECUTED_RESPONSE_LOST` | `CONFIRMED_NOT_EXECUTED` / `NON_EXECUTION_CONFIRMED`; no automatic retry authorization |
| `OBSERVATION_UNAVAILABLE` | Empty evidence, one observation failure; `INDETERMINATE` / `INSUFFICIENT_EVIDENCE` |
| `CONTRADICTORY_EVIDENCE` | `INDETERMINATE` / `CONTRADICTORY_EVIDENCE` |

Fixtures require no network, real sleep, randomness, or provider SDK. They
neither dispatch effects nor replace the ordinary `Reconciler`. All five
post-dispatch fixtures retain the exact supplied effect and use fixed aware
UTC timestamps and deterministic attempt/evidence IDs. Repeated construction
and observation preserve semantic content and ordering. Reconciler's runtime
start/completion timestamps are outside that guarantee.

`DeterministicEvidenceSource(name, evidence=(), *, observation_unavailable=False)`
captures evidence as a tuple and immediately returns it for the bound effect.
`observation_unavailable=True` instead raises a fixed local `RuntimeError`,
which the ordinary `Reconciler` normalizes as an observation failure. No
external state is read or changed.

```python
from effectrecon import EffectIdentity, Reconciler
from effectrecon.testing import AmbiguousOutcomeScenario, build_scenario


async def reconcile_test_scenario():
    effect = EffectIdentity("send", "queue:example", {}, "example-1")
    fixture = build_scenario(AmbiguousOutcomeScenario.CONTRADICTORY_EVIDENCE, effect)
    assert fixture.unknown is not None
    return await Reconciler(fixture.sources).reconcile(fixture.unknown)
```

## Provider-specific integration boundary

Core does not know how a payment API, queue, cloud provider, database, or
external service proves execution. That validation belongs to the
`EvidenceSource` adapter. It converts read-only provider observations into
semantically validated claims; core checks their structure, effect binding,
source provenance, and duplicate identity before aggregation.

A matching fingerprint is an identity check, not authentication or proof that
an observation is truthful. The quickstart source is local illustrative data,
not a production adapter. Production integrations must establish their own
authoritative observation rules and preserve the read-only boundary.

## Security considerations

- Never put API keys, access/authentication tokens, passwords, cookies, or
  credentials in `EffectIdentity`. It does not detect or redact secrets; its
  canonical JSON and accessors expose the supplied data.
- Evidence `binding` and `metadata` may contain application/provider data.
  Callers must control their collection, logging, persistence, and disclosure;
  EffectRecon does not redact these mappings.
- Source names, evidence IDs, remote resource IDs, and attempt IDs should not
  contain credentials. Identifiers can appear in returned data or diagnostics.
- EvidenceSource observation must stay read-only. The structural protocol
  cannot prevent a third-party adapter from violating this contract.
- Reconciler normalizes raw observation exceptions as described above. This is
  not general secret filtering: directly constructed `ObservationFailure`
  messages remain caller-supplied.
- EffectRecon does not provide authorization, secret storage, provider
  authentication, or validation of a provider's claims.

## Non-goals

EffectRecon is not:

- a workflow engine;
- a retry library;
- a distributed transaction manager;
- a Saga framework;
- an AI agent framework;
- an HTTP client;
- an idempotency-key service;
- a payment SDK;
- a deployment system;
- an autonomous retry system.

## Compatibility policy

For 0.x, patch releases should remain backward-compatible. Minor releases may
introduce justified breaking changes; intentional breaking changes must be
documented. `v1.0.0` is the target for stable public semantics.

This documents the current API; it does not freeze it or declare v0.1.0
released. [Issue #14](https://github.com/Rovyxel/EffectRecon/issues/14) owns the
final semantic and public-API review before that release.

## Development and validation

From a checkout, use a virtual environment and install the development tools:

```sh
python -m venv .venv
# Activate the environment using the command for your shell.
python -m pip install '.[dev]'
```

The runtime has no unconditional dependencies; the `dev` extra supplies the
test, lint, type-check, and build tools. In a POSIX shell, validate with:

```sh
PYTHONPATH=src python -m pytest
ruff check .
ruff format --check .
mypy src
MYPYPATH=src mypy examples/quickstart.py
python -m build
PYTHONPATH=src python examples/quickstart.py
```

The sdist includes the README, examples, tests, and `docs/` content. The wheel
contains the typed runtime package with `py.typed`; examples need not be
installed into it.
