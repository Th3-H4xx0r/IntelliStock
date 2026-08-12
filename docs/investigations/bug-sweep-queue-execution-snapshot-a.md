# Bug sweep: queue execution snapshot foundational contract (A)

Date: 2026-08-12
Scope: `backend/backtest_execution_snapshot.py` and `backend/tests/test_backtest_execution_snapshot_contract.py` only
Reviewed production SHA-256: `11f990d9fd45a2bd8b212d1d186211f9180e6f9d6db4d6dc6b28936f8fd7c967`
Reviewed test SHA-256: `1bc9892cc566994ef9d9eed166db56994a4ecf5abb47f2599cd94ff6d39b05c0`
Integration status: default-inert / not integrated
Verdict: **PASS**

## Final verdict

No unsafe blocker remains in the reviewed pure foundation.

The current narrow v1 contract:

- canonicalizes numbers into a RethinkDB-stable representation, bounds JSON traversal/text, rejects
  non-JSON values, unsafe integers, non-finite numbers, subclasses, and invalid Unicode;
- positively validates one equity Graph Nexus execution shape, including explicit/discovery symbol
  semantics, exact no-fee equity settings, seed/evidence controls, one mandatory OpenRouter model,
  exact model/config/access relationships, broker access revisions, and source/image/dependency
  identities;
- rejects unsupported candidate overrides rather than signing metadata that is absent from the
  effective strategy;
- rejects secret-bearing field names, redaction markers, unsafe URLs, value-pattern secrets, and
  embedded Fernet ciphertext, while returning only hashed/bounded paths in stable errors;
- binds mode, signer, normalized queue-row ID, canonical creation time, snapshot digest, and complete
  snapshot body in the HMAC;
- fails closed for partial, malformed, tampered, wrong-key, wrong-identity, unsupported-protocol, and
  engine-digest-mismatched contracts;
- returns immutable authenticated canonical bytes with a fresh snapshot materialization per access;
- exposes only explicitly labelled unverified syntactic metadata from the public-status helper; and
- preserves the default-OFF path: a row with no recognized snapshot fields returns `None` without
  requiring snapshot identity, creation time, or the HMAC key, while `required=True` or an engine
  digest still raises `contract_missing`.

## Re-audit history

Earlier revisions were blocked for DB-unstable int/float canonicalization, an open arbitrary core,
secret canary bypasses, body leakage through public status, mutable verified bodies, unsafe errors,
missing model/effective-config relationships, and non-inert legacy key validation. Those findings
were re-tested against the file hashes above and are resolved. In particular, the final ordering is:
validate policy inputs, detect absence, return only for optional absence, then require identity/key
for every present contract.

## Verification performed

Native targeted suite:

```text
cd backend && python3 -m pytest -q tests/test_backtest_execution_snapshot_contract.py
55 passed in 0.15s
```

The final outer-control/resource hardening was diff-reviewed: it rejects oversized text before
encoding/hash allocation, validates queue-row key and control-value exact types, and does not alter
canonical/HMAC/schema semantics.
The subsequent bounded-window/workload gate (including the broker's max(700 cycles, 90-day) warmup and a conservative 90-name Graph Nexus discovery expansion for either symbol mode) and evidence cost-label/value consistency checks were also diff-reviewed; both fail closed before signing and do not alter the authenticated envelope semantics.

Additional direct checks in a fresh native Python process confirmed:

```text
absent + optional + identity/time/key None -> None
absent + required -> contract_missing
absent + expected engine digest -> contract_missing
```

The audit also rechecked canonical order/list/numeric behavior, full digest/HMAC known-answer vectors,
plain-SHA recomputation attacks, identity/time/key binding, deletion/partial downgrade handling,
secret/URL/marker adversarial cases, strict schema relationships, verified-body detachment, public
projection, stable error hygiene, and resource limits.

No production code was edited by this audit. Only this report was updated; no provider, database, or
backtest was invoked.

## Integration boundary

This **PASS** applies only to the two reviewed, currently unintegrated foundational files. It does not
approve a partial API/engine/broker integration or a causal paired backtest. Integration must still
verify before mutable configuration/credential/provider work, execute the verified snapshot as the
authority, hydrate only exact access identities/revisions, carry an independent required/engine
binding against all-marker deletion, and preserve the ordinary path. Full causal claims still require
the separate frozen state/data/model-output isolation design.
