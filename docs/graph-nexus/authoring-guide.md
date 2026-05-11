# Graph Nexus phase authoring guide

This page is for someone wiring a new data source into the Graph
Nexus. Read the [Graph Nexus](../../README.md#graph-nexus) section of
the main README first — it covers the 11 existing phases, the build
mechanics, and the entity-resolution defense layers.

## When to add a phase

Add a phase when:

- You have a structured public data source (SEC filings, government
  contracts, patent filings, ownership records).
- The data implies a *relationship* between two companies (or between
  a company and a non-company node like a sector, government agency,
  or institution).
- The relationship can be represented as a directed or bidirectional
  edge with a confidence score.

Don't add a phase when:

- The data is real-time / event-shaped (news headlines, price ticks,
  earnings announcements). Those flow through Benzinga / news
  ingestion in the strategy loop, not the graph build.
- The data is per-trade outcome (P&L, position size). Those go in
  RethinkDB tables (`GraphNexusOutcomes`).
- The relationship is stale-by-design (an old M&A from 2008). The
  Nexus is for *currently-active* relationships; one-time historical
  events belong in a different store.

## The phase contract

Every phase in `backend/engines/nexus_graph_engine.py` follows the
same contract. A new phase must too.

### 1. Idempotency tokens

```python
# At the top of the build run, the engine generates one token:
current_run_token = uuid4().hex

# Every edge your phase writes must carry it:
session.run("""
    MERGE (a:Company {ticker: $a})-[r:YOUR_EDGE_TYPE]->(b:Company {ticker: $b})
    SET r.current_run_token = $token,
        r.edge_state = 'open',
        r.confidence = $conf,
        r.source = 'your_phase'
""", a=a, b=b, token=current_run_token, conf=0.9)
```

At the end of your phase, close edges that didn't reappear in this
run:

```python
session.run("""
    MATCH ()-[r:YOUR_EDGE_TYPE]->()
    WHERE r.current_run_token <> $token AND r.edge_state = 'open'
    SET r.edge_state = 'closed',
        r.valid_until = datetime()
""", token=current_run_token)
```

Old edges are *closed*, not *deleted*. The graph keeps history.

### 2. Caching

Every external HTTP call must go through the per-phase cache:

```python
cache = NexusCache(scope="your_phase", ttl_hours=24)
data = cache.get(cache_key)
if data is None:
    data = fetch_from_external_source(...)
    cache.put(cache_key, data)
```

TTL guidance:

| Data type                  | TTL           | Example                                |
| -------------------------- | ------------- | -------------------------------------- |
| Daily-refresh data         | 24h           | Polygon ticker list, news              |
| Weekly-refresh data        | 168h (7d)     | Wikidata SPARQL responses              |
| Quarterly data             | Indefinite (`-1`) | SEC 13F filings (immutable once filed) |
| Annual data                | Indefinite (`-1`) | SEC 10-K, GLEIF LEI hierarchy         |

The cache is at `/app/.cache/graph_nexus/<scope>/` inside the
container. Honor the user's `NEXUS_CACHE_MAX_AGE_HISTORIC` override.

### 3. Rate-limit handling

External APIs *will* rate-limit you. Phase 8 (USASpending) and Phase 9
(Wikidata) implement exponential backoff with a failure-burst cooldown
that you can copy. The pattern:

```python
backoff_state = {
    "consecutive_failures": 0,
    "cooldown_until": None,
    "max_cooldown_seconds": 180,
}

def fetch_with_backoff(url):
    if backoff_state["cooldown_until"] and time.time() < backoff_state["cooldown_until"]:
        sleep(backoff_state["cooldown_until"] - time.time())
    try:
        resp = http.get(url)
        if resp.status_code == 429:
            backoff_state["consecutive_failures"] += 1
            cooldown = min(60 * (2 ** backoff_state["consecutive_failures"]),
                           backoff_state["max_cooldown_seconds"])
            backoff_state["cooldown_until"] = time.time() + cooldown
            raise RateLimitError()
        backoff_state["consecutive_failures"] = 0
        return resp
    except ...:
        ...
```

Don't silently swallow 429s — the failure-burst counter is what
triggers the cooldown.

### 4. Entity resolution

Joining external names to your graph universe is where most phases
fall over. The defense layers (read in order):

1. **Heavy normalization at ingest.** Use the existing
   `_company_identity_key()` helper. It strips legal suffixes, drops
   "The", removes punctuation, normalizes whitespace.

   ```python
   from engines.nexus_graph_engine import _company_identity_key
   key = _company_identity_key("First Solar, Inc.")  # → "first solar"
   ```

2. **Fuzzy matching as fallback.** If exact-key match fails, use
   RapidFuzz's `token_set_ratio` with a confidence threshold. Phase 8
   sets the floor at 0.85.

3. **LLM validator on borderline matches.** When confidence is in the
   0.70–0.85 band, ask an LLM (with web-search grounding via Gemini)
   whether the two names are the same entity. Phases 8/9/10 ship
   reference validators (`_llm_validate_usaspending_edges`,
   `_llm_validate_controls_edges`, `_llm_resolve_patent_assignees`).

4. **Manual denylist.** When a wrong edge slips through, add the
   pair to `backend/data/graph_edge_denylist.json`. The engine reads
   this file at the start of each run and blocks those edges from
   being written.

The bias is intentional: a missing edge (false negative) is cheap; a
wrong edge (false positive) propagates bad signal through every
strategy that walks the graph.

### 5. Confidence on every edge

Every edge must carry a `confidence` property in `[0.0, 1.0]`. Strategy
queries filter and weight by this. Don't write edges with confidence
< 0.85 unless your phase has a specific reason to (and document it).

## Skeleton

A new phase looks roughly like this:

```python
def phase_N_your_source(context: BuildContext) -> PhaseResult:
    log = context.logger
    cache = context.cache_for("your_source", ttl_hours=24)
    universe = context.universe  # list of Company nodes

    edges_written = 0
    edges_closed = 0
    near_miss = []

    for company in universe:
        # 1. Fetch from external source (cached + rate-limited).
        data = fetch_with_backoff(cache, company)
        if data is None:
            continue

        # 2. Resolve external names → graph tickers.
        for raw_name, payload in data:
            ticker = resolve_to_ticker(raw_name, context.norm_to_ticker)
            if ticker is None:
                near_miss.append((raw_name, payload))
                continue

            # 3. Write the edge with idempotency token.
            with context.neo4j_session() as session:
                session.run(
                    "MERGE (a)-[r:YOUR_EDGE_TYPE]->(b) "
                    "SET r.current_run_token = $tok, r.edge_state = 'open', "
                    "r.confidence = $conf, r.source = 'your_source'",
                    tok=context.run_token, conf=0.9, ...
                )
                edges_written += 1

    # 4. Optional: LLM validation pass on near-misses.
    if near_miss:
        validated = llm_validate_batch(near_miss)
        edges_written += write_validated_edges(validated, context)

    # 5. Close edges from prior runs that didn't reappear.
    edges_closed = close_stale_edges(context, edge_type="YOUR_EDGE_TYPE")

    return PhaseResult(
        phase_name="your_source",
        edges_written=edges_written,
        edges_closed=edges_closed,
        near_miss_count=len(near_miss),
    )
```

Look at Phase 8 (`_resolve_usaspending_to_edges`) for the most complete
template — it does external fetch, fuzzy resolution, LLM validation,
and edge writing all in one phase.

## Wiring it in

1. Add your phase function to `nexus_graph_engine.py` with the next
   sequential number (current last is Phase 11 — yours becomes Phase
   12).
2. Add it to the phase dispatch list in `_run_phases()`.
3. Bump the default `GRAPH_NEXUS_PHASE_END` if you want it included in
   default runs.
4. Document the new edge type in the [main README's per-phase table](../../README.md#the-engine).

## Testing

- **Manual run**:
  ```bash
  docker compose exec backend python cli.py nexus run --phases 12
  ```
- **Edge count assertion**: `MATCH ()-[r:YOUR_EDGE_TYPE]->() RETURN count(r)`
  in the Neo4j browser. Compare to expected count for the universe
  size.
- **Re-run idempotency**: run the phase twice in a row. Edge count
  should not change. `r.edge_state = 'closed'` should be 0.
- **LLM validator regression**: pick a known-bad name pair (e.g.
  "First Solar" vs "First Republic Bank") and confirm your validator
  rejects it.

## See also

- [`backend/engines/nexus_graph_engine.py`](../../backend/engines/nexus_graph_engine.py)
  — every existing phase. Phases 8 / 9 / 10 are the most complex
  templates.
- [`backend/data/graph_edge_denylist.json`](../../backend/data/graph_edge_denylist.json)
  — the manual escape hatch.
- [Graph Nexus section in the README](../../README.md#graph-nexus) —
  per-phase summary and entity-resolution philosophy.
