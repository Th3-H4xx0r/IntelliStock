# IntelliStock — documentation

This is the documentation tree. The [main README](../README.md) covers
the install path, the security model, and the headline features. The
docs below go deeper.

## By goal

| Goal                                        | Read                                                                                       | Time   |
| ------------------------------------------- | ------------------------------------------------------------------------------------------ | ------ |
| Get the platform running                    | [Main README — Install](../README.md#install-recommended)                                  | 5 min  |
| Understand the architecture                 | [Architecture](./architecture.md)                                                          | 15 min |
| Write your first strategy module            | [Strategy authoring guide](./strategies/authoring-guide.md)                                | 45 min |
| Add a new data source to the Graph Nexus    | [Graph Nexus phase authoring](./graph-nexus/authoring-guide.md)                            | 60 min |
| Diagnose a first-run failure                | [Troubleshooting](./operations/troubleshooting.md)                                         | 5 min  |
| Contribute code                             | [CONTRIBUTING](./contributing/CONTRIBUTING.md)                                             | 10 min |
| Reference the env-var surface               | [Main README — Configuration](../README.md#configuration)                                  | —      |

## By persona

- **Operator** — you run the platform and watch trades. Start with the
  [Main README](../README.md) end-to-end, then keep
  [Troubleshooting](./operations/troubleshooting.md) in a tab.
- **Strategy developer** — you write trading logic. Start with the
  [Strategy authoring guide](./strategies/authoring-guide.md), then
  read `backend/strategies/rsi.py` for a complete reference
  implementation.
- **Graph integrator** — you wire new data sources into Neo4j. Start
  with [Graph Nexus phase authoring](./graph-nexus/authoring-guide.md)
  and the deep-dive in the [main README](../README.md#graph-nexus).
- **Contributor** — you want to fix bugs or add features. Read
  [CONTRIBUTING](./contributing/CONTRIBUTING.md) and the
  [Architecture](./architecture.md) overview.

## What's not here yet

This tree is intentionally minimal at launch. Everything else lives in
the source — the codebase is small enough that reading the relevant
file is faster than writing a doc page that paraphrases it.

- Per-strategy reference → read `backend/strategies/<name>.py`. Every
  strategy module ships its own JSON schema header at the top of the
  file describing its config keys.
- Per-phase Nexus reference → read
  `backend/engines/nexus_graph_engine.py`. Phases are sequential and
  numbered.
- API reference → run the API locally and visit `/docs` (FastAPI
  auto-generates OpenAPI from the route signatures).
- CLI reference → `docker compose exec backend python cli.py help`.

PRs that fill in any of the gaps above are welcome.
