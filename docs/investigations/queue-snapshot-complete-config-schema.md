# Complete doc-193 Graph Nexus execution-snapshot schema

**Date:** 2026-08-12
**Base commit:** `06defae` (`Add authenticated execution snapshot foundation`)
**Scope:** read-only investigation and next-slice design. No production code, database row, provider, or runtime configuration was changed.

## Verdict

`backend/backtest_execution_snapshot.py` is a sound authenticated-envelope foundation, but its current positive schema **cannot represent the effective doc-193 run**. It accepts one small OpenRouter-only config, one default model role, the display name `Nexus Only`, a string strategy id, and no candidate overrides. The live doc-193 shape is one Graph Nexus spec with **580 operative row-config keys**, **13 top-level `*_llm_model_id` role references**, two model rows (OpenRouter and Bedrock), and **621 secret-free public config keys after the current resolver and `pit_mode=research` injection**.

A generated checked-in manifest is viable only in the following narrow form:

1. it is an **exact doc-193 admission manifest**, not a schema inferred from one row;
2. its generation input is the operative `strategies[0].config` only, after the secret boundary has proved that it contains no credential-bearing fields;
3. every base path/value is a checked-in `const` (or has a separately reviewed hand-authored domain), with no additional/missing paths;
4. model-derived fields, `pit_mode`, and approved evidence overrides are separate, positively validated derivations; and
5. runtime does not treat the manifest as proof that source-level `.get(..., default)` and environment fallbacks have been materialized.

A **type-only manifest generated from the current values is not sufficient**. It cannot infer whether an integer is a count, a percentage, a sentinel, or a real-valued threshold; it cannot infer legal enums/ranges; and it does not capture absent keys whose source defaults execute. In the principal readers, a conservative literal audit found 754 direct `config`/`cfg`/`settings` keys, of which 285 are absent from the reconstructed 621-key public config (some are runtime-injected/internal, but many are source defaults). There is no single authoritative Graph Nexus normalizer today.

Therefore the next safe implementation slice is **pure schema/manifest support plus tests only**, still default-inert and not queue-integrated. It may admit the complete current doc-193 input and its 13 model roles, but it must not advertise an executable “complete effective configuration” until the runtime fallback and access-revision blockers below are closed.

## What was inspected

- `backend/backtest_execution_snapshot.py` and its contract tests.
- Strategy loading, evidence overlay, preregistration, passive-execution setup, runtime condition/config merge, regime overlay, model refresh, and instance reads in `backend/broker.py`.
- `backend/model_resolver.py`.
- `backend/strategy_secret_boundary.py` and `backend/persistence_safety.py`.
- Strategy CRUD normalization in `backend/interactive_utils.py`.
- Graph Nexus config/model/provider/Neo4j readers in `backend/strategies/graph_nexus_analysis.py`, plus `core_sleeve.py`, `nexus_config_identity.py`, and `strategies/nexus_analyst_panel.py`.
- Queue construction in `interactive_utils.action_create_backtest`, API input in `api/main.py`, and worker launch in `engines/backtest_engine.py`.
- The current doc-193 row and its exact linked model/broker rows through read-only RethinkDB queries. Output was restricted to explicit public allowlists, boolean credential status, and secret-boundary-approved data. No credential value or ciphertext was printed or retained.
- Checked-in doc-193 backups/scripts and `docs/investigations/fix-audit-levers.md`.

GitNexus was queried before source inspection. Its index was one commit stale; the required refresh was attempted but could not acquire the LadybugDB lock held by another process. The older graph still identified `resolve_model_refs_in_config` and queue/strategy areas; commit-`06defae` source was inspected directly for the new module.

## Observed doc-193 shape

### Strategy and instance

| item | observed value/constraint |
|---|---|
| instance row | string id `v2-let-run-core` |
| instance kind | equity (the row has no non-equity kind) |
| strategy link | native RethinkDB integer `193`, not string `"193"` |
| strategy row | native integer id `193` |
| operative specs | exactly one |
| module | exactly `graph_nexus_analysis` |
| weight | numeric `1` |
| execution position | integer `0` |
| phase/scope | `pre` / `run_once` |
| conditions | exactly `{}` |
| `experiment_spec` | absent on the row; runtime projection is exactly JSON `null` |
| operative config | 580 top-level keys before resolution |

The strategy display name is not `Nexus Only`; it must be a bounded secret-free display string and must not be used as an execution discriminator. Identity is the native typed primary key plus exact module/spec shape.

The row also contains a separate top-level legacy `config` object (183 keys). The broker does not execute it. It contains six credential-named placeholder fields (`alpaca_key`, `alpaca_secret`, `azure_openai_api_key`, `benzinga_api_key`, `llm_api_key`, and `neo4j_password`). **Do not copy or sanitize that legacy object into an execution snapshot.** The positive projection must select only `name`, `experiment_spec`, and the operative `strategies` list. Redaction markers are not executable values.

### Public config counts and structures

The operative 580-key config, after the strategy secret boundary, has this current JSON type distribution:

| type | count |
|---|---:|
| exact boolean | 146 |
| integer-valued number | 260 |
| non-integral number | 109 |
| string | 58 |
| array | 4 |
| object | 3 |

The four arrays are:

- `analyst_panel_horizons`: array of four bounded strings;
- `breadth_scan_regimes`: array of two regime strings;
- `profit_take_tiers`: array of two two-number tuples;
- `trailing_stop_ratchet_tiers`: array of two three-number tuples.

The three objects are:

- `momentum_weights`: exact string keys `10`, `20`, `21`, `42`, `63`, numeric values;
- `sector_watchlist`: exactly empty for current doc-193;
- `regime_profiles`: exact profiles `bull`, `chop`, `recovery` and no `bear` profile. The profile objects contain 18, 5, and 17 fields respectively. Their nested arrays are numeric profit-take tuples.

The current public base-config canonical SHA-256 is `968c4b357e9bd14cb601f1e540679eb611953fa11bde9a848b5e7882d2cd4eac`. After reproducing the resolver's public field propagation for all 13 roles and injecting `pit_mode=research`, it has 621 keys (146 booleans, 260 integer-valued numbers, 109 non-integral numbers, 99 strings, four arrays, three objects); the reconstructed canonical SHA-256 is `9bfb57f6255b8ee7d4b10eaa1d15dc64807e5cf808bb64f49f1ba2219d346d3c`. These hashes cover public config only and are not proposed protocol constants.

This explains the previously observed 621-key `BacktestResults.strategy_schema`: 580 row keys plus 40 public resolver additions plus `pit_mode`.

## Exact manifest rule

### What may be generated

Check in a versioned JSON manifest such as `graph-nexus-doc193-input-v1`, generated offline from a reviewed, secret-free public fixture. Runtime must only read the checked-in artifact; it must never generate an allowlist from the mutable row it is validating.

For the first doc-193-only version, the safest rule is:

- base row config has exactly the 580 reviewed paths and exact reviewed values;
- no missing or extra base field;
- the three structured objects and four arrays have exact recursive shape/order;
- integral floats normalize exactly as `canonical_execution_json` already specifies;
- approved queue overlays are applied only after the base comparison;
- model-derived public fields are produced only by strict resolution and are relationally checked against role bindings;
- any doc-193 edit fails requested snapshot enqueue until the public fixture/manifest is regenerated and reviewed.

This const-style manifest is restrictive, but it is honest and safe. A manifest that permits tuning needs hand-authored per-path domains. It must not infer them from names or current Python types.

### Primitive constraints for any later domain manifest

- Boolean: `type(value) is bool`; never `bool(value)`.
- Integer: exact integer after DB-stable integral-float normalization, `abs(value) <= 2^53-1`; bool forbidden; per-path min/max required.
- Number: exact int/float, finite, safe JSON range; bool forbidden; per-path min/max required.
- String: exact UTF-8 string, no surrogate code points, bounded bytes; preserve the value rather than stripping it unless that path explicitly declares normalization; enums/identifiers/URLs get path-specific validators.
- Array: exact length bounds, ordered, with exact item/tuple schemas.
- Object: string keys, exact property set, bounded size; no `additionalProperties`.
- Any credential-like key, redaction marker, approved secret reference, Fernet-looking text, URL userinfo, secret query parameter, bytes, non-finite number, tuple/set/object coercion, or sanitizer-changing value is fatal.

### Queue-dependent config fields

`pit_mode` must be present in the **effective** config and exactly equal the validated evidence value (`strict` or `research`). It is absent from the base doc-193 row today and is injected for equity backtests.

The existing A1-A4 candidate overrides retain their existing exact validators:

- `regime_position_cap_recovery_hard_enforce`: exact boolean;
- `circuit_breaker_regime_adjustment_semantics_v2`: exact boolean;
- `momentum_breakout_max_nav_pct_by_regime`: exact keys `default`, `bull`, `recovery`, each finite number in `(0, 1]`;
- `deployment_ramp_caps_by_regime`: exact key `bull`, exactly three finite caps in `(0, 1]`.

Do not add a second override implementation in the snapshot module. Reuse `validate_evidence_options` and `apply_candidate_overrides`. Any proposed anchor `12`/`20` override remains a separate policy change and is not silently admitted by this schema.

### Conditions and experiment declaration

For the doc-193 slice:

- `conditions` must be exactly `{}`. Although runtime merges conditions first and config second, allowing nonempty conditions creates a second config namespace and alias/fallback behavior. Strategy CRUD already promotes legacy conditions into config.
- `experiment_spec` must be exactly JSON `null`. Do not accept `{}` as equivalent. A future non-null version must validate the complete `ExperimentSpec` declaration schema before signing; it cannot be an arbitrary JSON mapping.

## All configuration consumers: implications

The relevant current consumer chain is:

1. `load_strategies_from_db` reads `Instances.strategy_id`, then the `Strategies` row, scrubs the operative specs, and projects `experiment_spec`.
2. Backtest startup resolves every `*_llm_model_id`, sorts specs, injects research PIT, applies candidate overrides, and replaces `strategy_schema.strategies` with that resolved copy.
3. `_preregister_backtest_experiment` fingerprints those effective specs.
4. `run_run_once_strategies` applies a per-cycle regime profile, re-resolves model rows, merges `conditions` then `config`, derives history/runtime identities, injects brokerage credentials/feed and run context, then calls Graph Nexus.
5. Graph Nexus and broker helpers consume the mapping. The largest static literal consumers are `graph_nexus_analysis.py` (444 current doc keys found) and `broker.py` (99), followed by `nexus_config_identity.py`, `nexus_analyst_panel.py`, and `core_sleeve.py`. Model-role field names are also constructed dynamically, so literal grep is not a complete schema.

The checked-in `INTELLISTOCK_SCHEMA` at the top of `graph_nexus_analysis.py` has only the 183-key legacy/default shape. It is not the 580-key operative doc-193 schema and cannot be used as the manifest source.

The regime system does not require a row reread: preserve the signed base config and signed `regime_profiles`, cache `_regime_base_config` from that verified mapping, and shallow-merge only the selected signed profile. Retain the current prohibition on `regime_*`, `max_positions*`, and other base-only transition controls inside overlays.

Dynamic run-context fields (`base_instance_id`, history/runtime scope ids, feed, `_nexus_is_live_mode`, determinism marker, telemetry ids, and resolved time increment) should not be copied from a mutable row. Derive them from signed core fields and the verified config, then inject them into a fresh in-memory runtime copy.

## Multi-role model identity

There are exactly 13 top-level role references and two unique model rows. A unique-model list is not enough: the snapshot must bind every config field/role, because the same row can be used under different fallback prefixes.

### Bedrock row (two bindings)

Provider/model: `bedrock` / `openai.gpt-oss-120b-1:0`
Model row id: `17d867a5-b0e5-478f-9a92-2358b583fd7f`

- `company_article_llm_model_id`
- `lookback_company_article_llm_model_id`

Its effective public adapter identity includes Bedrock region `us-east-1`, Bedrock reasoning `medium`, model cache family `gpt-oss-120b`, the resolver-propagated Azure API-version field, and all four telemetry-pricing slots (including nulls). Its encrypted API material is present and strictly decryptable, but no material/ciphertext/hash may enter the snapshot.

### OpenRouter row (eleven bindings)

Provider/model: `openrouter` / `nvidia/nemotron-3-ultra-550b-a55b`
Model row id: `5aa6b31a-55ff-4f34-9e12-7db39c6a94eb`

- `llm_model_id` (default role, empty prefix)
- `analyst_panel_llm_model_id`
- `event_maintenance_llm_model_id`
- `lookback_llm_model_id`
- `lookback_event_maintenance_llm_model_id`
- `lookback_macro_article_llm_model_id`
- `lookback_overlay_llm_model_id`
- `lookback_sentiment_llm_model_id`
- `macro_article_llm_model_id`
- `overlay_llm_model_id`
- `sentiment_llm_model_id`

Its effective public adapter identity includes the exact OpenRouter base URL, reasoning `medium`, the resolver-propagated Azure API-version field, and the four pricing slots. The row is encrypted and strictly decryptable. The raw analyst-panel config says reasoning `high`; the current model resolver overwrites it with the model row's `medium`. The effective snapshot must therefore carry `medium`, not the stale row value.

### Required binding schema

Use one `model_bindings` entry per role, ordered by `(spec_ordinal, config_field)`, with exact fields:

- `spec_ordinal`: integer, here `0`;
- `config_field`: one of the exact 13 names above;
- `role_prefix`: exact lowercase prefix ending in `_`, or `""` for default;
- `record_id`: bounded identifier;
- `provider` and `model`: exact normalized nonempty strings;
- `adapter`: exact provider-aware public identity;
- `runtime_access`: exact model-row id, explicit integer access revision, `required=true`.

Relational validation must require:

- `config_field == role_prefix + "llm_model_id"`;
- exactly one binding for every top-level model-id field and no other/nested model-id field;
- repeated model ids have byte-identical public identity and access binding;
- the effective config's role-specific provider/model/active adapter fields equal the binding;
- default role has both `model_name` and `llm_model` equal to the bound model;
- provider-specific required fields are explicit (OpenRouter base URL; Bedrock region); and
- required decrypted access material is injected only into a fresh runtime config.

Normalize public model-row empty string and null to the same absent adapter value only where the resolver treats them identically. Do not sanitize an entire Models row: select an explicit allowlist. Exclude names, timestamps, credentials, ciphertext, and unrelated metadata. Pricing fields must be included if telemetry is to avoid its current live row reread.

## Exact access blockers

The current rows do **not** have an integer `access_revision`:

- both referenced Models rows: missing;
- linked Alpaca BrokerageAccounts row `bf78ad0c-3073-4aac-97a5-a29c7b043404`: missing.

All three rows have `updated_at`, but using it as an access-revision fallback would violate this task's no-fallback requirement and would mix public edits with secret-material revision. Do not hash plaintext, ciphertext, or a reversible mask. Add a separately maintained monotonic integer revision in a later migration/write-path slice, incremented atomically whenever required encrypted access material changes. Until those revisions exist, a requested execute snapshot must fail closed.

The instance uses the one linked Alpaca row for both trading and market data. Its public identity is Alpaca, paper `true`, feed `iex`; encrypted key and secret are present and strictly decryptable. The snapshot may reference the same access row for both purposes, but runtime should deduplicate the one exact row read.

There is another blocker outside Models/BrokerageAccounts: Graph Nexus resolves Neo4j password from config, then `NEO4J_PASSWORD`, then a source-literal default; the engine also forwards `NEO4J_PASSWORD` with that default. The public doc supplies Neo4j URI and user but not password. Snapshot mode needs one explicit required runtime-secret access binding and must refuse missing material; it may inject that secret in memory after verification, but it may not persist the value or fall through to an environment/default password. A revisioned secret manager reference would be preferable; a bare unversioned environment variable is not an exact access identity.

## Queue, engine, startup, and runtime without current-row rereads

### Queue creation

For requested execute mode only:

1. Validate evidence and equity mode normally.
2. Reserve the numeric backtest id and one exact UTC `created_at` before signing.
3. On one connection, read the exact instance, native-typed strategy id `193`, exact strategy row, the two exact Models rows, and the exact linked Alpaca row. Do not scan for alternatives.
4. Positively project only the operative spec. Reject any credential-named field in it even if it is a placeholder; the current operative config has none.
5. Compare the base config to the checked-in exact manifest; strictly resolve all 13 roles; inject PIT and validated candidate overrides; build the public role/access identities.
6. Prove the public object is unchanged by the sanitizer and passes `assert_secret_free` plus the stricter snapshot scanner.
7. Build the HMAC envelope and insert the full queue row atomically. Resolution/signing/insertion failure inserts no partial row.

The existing random-id insert helper needs a builder/reservation form because the HMAC binds `backtest_id` and `created_at`; never insert then patch an envelope.

### Backtest engine

For an execute envelope, verify the HMAC before parsing launch fields. Build instance id, symbols/discovery mode, dates, granularity, cash, fee, and seed from verified signed core fields. Do not call `_get_instance_doc`, and do not apply the current `60`/`100000`/fee coercion fallbacks. Pass only the backtest id and expected snapshot digest as non-secret process binding. Legacy/off rows retain their current behavior.

### Broker startup

The broker must independently fetch and verify the exact queue envelope before `_instance_kind_and_crypto_config`, credential setup, telemetry setup, strategy loading, evidence lifecycle, passive execution, preregistration, provider/data work, or result creation. Current module-level initialization reads the instance before the backtest startup block, so integration requires an explicit early execute-mode gate; merely adding a branch around line 9419 is too late.

After verification:

- use `core.instance.kind` and ids; do not read current Instances/Strategies public config;
- hydrate only the exact revisioned Models/Brokerage/graph access rows needed for secrets;
- ignore those access rows' current public model/adapter values;
- configure passive execution, evidence, cost model, seed, and preregistration from signed core/config;
- persist the verified snapshot-derived strategy schema, not a freshly loaded comparison.

A current-row comparison may be emitted as non-authoritative, value-free drift telemetry after startup. Edits/deletion of Strategies/Instances after enqueue must not change execution.

### Runtime

- Skip `resolve_model_refs_in_config` at startup and in every `run_run_once_strategies` invocation for verified snapshot specs.
- Add a strict snapshot role path: a missing role provider/model/key/active adapter field is fatal and must not reach the role/default/environment chains in `_resolve_role_llm_config` or `_resolve_role_llm_provider_config_fields`.
- Use the signed regime profiles and derived run context only.
- Replace `_init_llm_telemetry`'s Models id lookup/provider-model scan with a lookup over the verified bindings, including the four pricing fields. No provider/model fallback scan.
- Do not refresh instance links or data credentials from current Instances. Hydrate the signed exact brokerage id/revision.
- Keep secrets only in the runtime copy; never put that copy in `BacktestResults`, experiment config, logs, errors, or the queue.

## Why the current foundation rejects doc-193

The present v1 validator has several deliberate incompatibilities:

- `_SPEC_CONFIG_FIELDS` allows only 11 narrow fields, versus 621 effective public keys.
- It requires the display name `Nexus Only`; doc-193 has a different name.
- It requires a string strategy identity; the real Strategies primary key is integer `193`.
- It permits exactly one spec and one unique default-role model binding only; doc-193 has 13 role bindings and two unique models.
- It is OpenRouter-only; two roles are Bedrock.
- It requires every non-OpenRouter adapter field to be null, while the real OpenRouter row carries a resolver-propagated Azure API version and reasoning settings.
- It rejects all candidate overrides because they cannot yet be projected into the narrow config.
- It requires integer access revisions that the current rows do not have.

These are fail-closed limitations, not bugs to loosen generically.

## Recommended next slice and stop line

Implement, in isolation:

1. an offline generator plus checked-in, reviewed, secret-free **exact doc-193 input manifest**;
2. a pure recursive manifest validator in `backtest_execution_snapshot.py`;
3. the 13-role relational binding schema with OpenRouter and Bedrock provider-specific validators;
4. exact `conditions={}` and `experiment_spec=null` handling;
5. fixtures/tests proving the observed 580-to-621 projection, duplicate-row multi-role binding, analyst reasoning overwrite, nested profile/list shapes, secret rejection, and no sanitizer mutation.

Do **not** yet wire the API/queue/engine/broker to execute this version. The stop line is reached until:

- explicit access revisions exist on the two Models rows and linked Alpaca row;
- Neo4j secret access has an exact no-default binding;
- snapshot startup verification can run before current module-level instance/credential/telemetry reads;
- per-cycle model resolution and telemetry row fallbacks have a verified-snapshot bypass; and
- either a centralized effective-config normalizer materializes source defaults, or the claim is explicitly narrowed to “authenticated complete row input under a source-pinned runtime,” not “complete effective configuration.”

That is the smallest slice that expands the authenticated contract without creating a false executable claim or a secret-bearing fallback path.
