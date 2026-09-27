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
