# Bug sweep B: `frozen_paired_state` pure contract

Date: 2026-08-12
Scope: `backend/frozen_paired_state.py` and `backend/tests/test_frozen_paired_state_contract.py` only
Verdict: **REQUEST CHANGES / NOT READY TO CALL A CAUSAL FROZEN-PAIR CONTRACT**

## Executive summary

The module is pure and default-inert: it imports only the standard library and typing, has no
module-level I/O, and a repository-wide text plus Python-AST scan found **no production importer or
caller**. The only executable consumer is the direct-load contract test. The module docstring also
correctly says that manifest claims are not runtime enforcement.

The positive foundations are useful: exact built-in JSON types, bool/int separation, integral-float
normalization, finite/safe-number limits, Unicode-surrogate rejection, bounded depth/nodes/strings,
strict lowercase SHA-256 and ASCII ID grammars, duplicate normalized-PK detection, strict positive
manifest shapes, bundle-hash recomputation, and equality of the currently declared negative-control
fields. The original focused suite passes (`15 passed`).

However, adversarial tests found several causal-integrity blockers. Most importantly, the treatment
comparator can accept a difference at the wrong structure because rendered paths are ambiguous, and
its public `allowed_values` argument lets a caller change the preregistered treatment. The state
schema accepts arbitrary table names despite the planning contract requiring an allowlist; receipt
coverage is materially incomplete; and several hostile built-in inputs escape as raw `TypeError`.
Audit A announced a blocker-fix plan, but no edits reached the shared files after two resumptions, so
B intentionally made **no edits** to either contract file and records the unchanged-hash verdict.

## Findings

### 1. CRITICAL — rendered diff paths do not identify the nested target

`_diff()` builds a dot/bracket string from unescaped attacker-controlled dictionary keys
(lines 193-214), then `compare_execution_snapshots()` compares that string to the allowed path.
A flat key can therefore masquerade as the preregistered nested path:

```python
key = "core.strategy.specs[0].config.anchor_reinforce_target_pct"
compare_execution_snapshots({key: 12}, {key: 20})  # accepted
```

The returned hashes authenticate the wrong snapshot structure. The fix must represent a path as
**typed segments** (e.g. `("core", "strategy", "specs", 0, "config", ...)`) and compare those
segments to a fixed constant; raw keys must never be compared through an ambiguous rendering.

There is also a contract spelling mismatch: the preregistration investigation specifies
`$.core.strategy.specs[0].config.anchor_reinforce_target_pct`, while the code and tests omit the root
`$`. Rooting the display string is necessary but does not fix the collision by itself.

### 2. CRITICAL — callers can redefine the fixed 12 -> 20 experiment

The public comparator accepts `allowed_values` (lines 217-240):

```python
compare_execution_snapshots(snapshot(12), snapshot(999), allowed_values=(12, 999))
```

This passes. A function documented as enforcing the one preregistered treatment must have no
caller-controlled path/value override: require exactly integer `12` to integer `20`. The current
arguments also create an unsafe equality surface: objects with hostile `__eq__`/`__ne__` raise raw
exceptions containing attacker-controlled text.

### 3. HIGH — the state manifest admits arbitrary tables and policies are self-asserted

`_TABLE_RE` is only a grammar, not a positive state-table allowlist (lines 28, 323-331). Adding a
syntactically valid `ProductionSecrets` table with an `arm_local` policy produces a valid bundle.
This contradicts the source planning requirement: “Do not accept arbitrary table names/fields as a
way to make the allowlist meaningless.” It also lets a manifest label a logically sealed table
`arm_local` unless expected policy is fixed per table.

The schema should enumerate every admitted table and its exact key fields/write policy. Unknown or
missing tables must fail. This is still only a declarative contract; runtime enforcement remains a
separate mandatory integration gate as the module docstring says.

### 4. HIGH — negative-control equality omits preregistered causal artifacts

The comparator checks a useful subset but cannot prove the negative-control gate described in
`frozen-state-next-implementation-slice.md` §3.8/§6. It lacks hashes/receipts for at least:

* NAV **and full position/quantity series** (a single `final_nav_sha256` is insufficient);
* ordered submitted orders and the anchor treatment-exposure ledger;
* first and last strict process-runtime state;
* graph and market/provider occurrence ledgers (only PIT/model appear);
* terminal summary/accounting/benchmark artifacts;
* explicit writes-only-to-allowed-namespaces / no-provider-fallback audit facts; and
* the negative-control pass receipt needed to gate later 12/20 pairs.

Two arms can currently diverge in any omitted artifact and still return `status="identical"`.
Expand the exact positive receipt schema before this comparator is treated as study authorization.
Operational envelope IDs/timestamps should remain deliberately excluded; causal fields must not.

### 5. HIGH — hostile built-in values escape the stable, value-free error contract

Several validators perform set membership/equality before exact type checks. Concrete reproductions:

```python
core["state"]["tables"]["GraphNexusNewsCache"]["write_policy"] = []
build_frozen_paired_state_manifest(core)  # TypeError: unhashable type: 'list'

core["isolation"]["neo4j"] = []
build_frozen_paired_state_manifest(core)  # TypeError: unhashable type: 'list'
```

A hostile equality object passed through comparator options/negative receipt target can also leak
its exception message. Every semantic scalar needs an exact built-in type guard before membership
or equality. `target_pct=12.0` currently passes because Python equality aliases it to integer 12;
it must require `type(value) is int`.

### 6. MEDIUM — arbitrary table names leak through errors

Nested JSON field paths are thoughtfully hashed, but `_validate_table()` interpolates the raw table
name into `FrozenStateError.paths` (lines 250-263). A hostile valid table named
`DO_NOT_ECHO_SECRET` is echoed as `$.state.tables.DO_NOT_ECHO_SECRET`. Hash unknown table identities
(or report only the fixed container path) before schema validation.

### 7. MEDIUM — window/clock strings have no real grammar or ordering

Window values need only be nonempty and wall time need only end in `Z` (lines 292-296, 335-340).
The manifest accepts `start="later"`, `end="earlier"`, `baseline_cutoff="future"`, and
`wall_time="Z"`. Require one exact canonical UTC grammar, real calendar dates/times, `start < end`,
and baseline/wall-time ordering consistent with the registered treatment boundary. Do not silently
accept offset-equivalent or noncanonical representations if bytes are identity material.

### 8. MEDIUM — primary-key declaration and aggregate resource bounds need tightening

`key_fields=["id", "id"]` is accepted. Composite key field names should be unique and bounded in
count and UTF-8 bytes. `_normalize()` counts value strings but not dictionary-key bytes, so it can
do substantially more work before final serialized-size rejection. `state_rows_sha256()` resets
normalization budgets for every row and then constructs/sorts another full aggregate; the ultimate
16 MiB canonicalization catches oversized output but does not bound intermediate work tightly.

Use one explicit aggregate byte/node budget for a table hash (including keys), reject duplicate key
fields, and avoid multiple whole-object copies where possible.

### 9. MEDIUM — diff materialization is a hostile memory amplifier

`_diff()` builds every differing tuple and every path string even though the caller only needs to
know whether there is exactly one. At 200,000 scalar differences, a subprocess used about 60 MiB RSS;
the public node limit permits an order of magnitude more. Stop after two differences or stream an
iterator, and retain typed path segments rather than rendered attacker keys.

## Boundary matrix

| Boundary | Result |
|---|---|
| `True` vs `1` canonical JSON / PK | distinct; good |
| `1`, `1.0`; `-0.0`, `0` | normalized identically; duplicate PK rejected; intended DB stability |
| NaN / infinities / integers above 2^53-1 | rejected |
| Unicode astral/combining text | accepted and byte-authenticated without lossy normalization |
| lone surrogate in key/value | rejected |
| cyclic/deep list/dict | bounded by depth error |
| non-string key / tuple/set/custom object | rejected |
| digest grammar | exact lowercase `sha256:` plus 64 hex characters |
| ID grammar | bounded ASCII grammar; Unicode/whitespace/control characters rejected |
| bundle tampering / stale hash | rejected; verify returns a normalized copy |
| duplicate normalized PK | rejected |
| duplicate key-field declaration | incorrectly accepted |
| exact nested treatment path | vulnerable to flat-key collision and caller override |
| arbitrary manifest fields | rejected at declared objects, but arbitrary table names accepted |
| negative receipt differences in declared set | rejected; field name only, no value leak |
| negative receipt differences outside declared set | cannot be represented; important causal coverage absent |
| replay/read-only claims | strict strings, but declarative only; no runtime enforcement and arbitrary tables weaken meaning |

## Tests and static evidence

Commands were read-only and launched no run:

```text
python3 -m pytest -q backend/tests/test_frozen_paired_state_contract.py
15 passed in 0.05s
```

A repository-wide `git grep`, full text scan, and AST scan of `backend/**/*.py` found no importer,
dynamic import string, caller, or symbol reference outside the test. The only non-test mention is
the planning document that requests this future module. GitNexus is up to date at commit `06defae`,
but upstream impact reports `target not found` for each new symbol because both files are untracked;
the manual scan therefore supplies the relevant zero-caller evidence. Operational blast radius is
**LOW/default-inert**, while semantic risk if used as a causal authorization gate is **CRITICAL**.

No config, run, CSV, queue, broker, or production file was changed or read through an external
service. No backtest was launched.

## Hashes reviewed (unchanged by B)

```text
backend/frozen_paired_state.py
  sha256:dd5269c27a2fcd22e29fc0caa733d183e2b9d105451b02a408fa70780c68f9a8
backend/tests/test_frozen_paired_state_contract.py
  sha256:4682b30bbd41552a1690352e225e3e913f5a76ea66c3dbb56afc998d200d8c4d
```

## Recommendation

**REQUEST CHANGES.** Fix findings 1-5 and add exact adversarial regressions before accepting this as
a frozen-pair/negative-control contract. Findings 6-9 should also be closed before exposing the pure
functions to untrusted exported data. Then rerun this independent sweep against stable final hashes.
