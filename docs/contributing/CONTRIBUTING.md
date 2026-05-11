# Contributing

Thanks for considering a contribution. IntelliStock is small and the
review surface is small — pull requests get attention.

## What's in scope

Welcome:

- **Bug fixes**, especially first-run issues and trading-critical paths.
- **New strategy modules** under `backend/strategies/`. The
  [strategy authoring guide](../strategies/authoring-guide.md) is the
  starting point.
- **New Graph Nexus phases** for additional public data sources. See
  the [phase authoring guide](../graph-nexus/authoring-guide.md).
- **Broker adapters** for brokerages other than Alpaca and Robinhood.
  The interface lives in `backend/broker_adapters/`.
- **Documentation** that fills in gaps the README and `docs/` tree
  don't cover yet.
- **Tests** — there's never enough of them.

Out of scope (for now):

- Major architecture changes (replacing RethinkDB, switching from
  FastAPI). Open an issue first to discuss.
- New LLM providers. The provider abstraction in
  `backend/chatbot/llm.py` covers OpenAI / Azure / Gemini /
  DeepSeek / NVIDIA NIM. Adding a sixth is fine; replacing the
  abstraction isn't.
- UI redesigns. Targeted UX fixes welcome; full redesigns should
  start with a design discussion.

## Workflow

1. **Open an issue first** for anything bigger than a one-line fix.
   Tag it `bug`, `feature`, `docs`, or `question`. Describe what you
   want to change and why. This is cheap upfront and saves rework.
2. **Fork the repo**, branch from `main`. Branch naming:
   `fix/short-description`, `feat/short-description`,
   `docs/short-description`, `strategy/your-strategy-name`.
3. **Run the relevant code path locally** before pushing. For backend
   changes: at least one paper-mode backtest. For strategy changes: a
   backtest comparing your change against the unmodified baseline on
   the same date range. For Nexus phases: a `nexus run --phases <N>`
   pass and a Neo4j edge-count check.
4. **Keep PRs scoped.** One feature or one fix per PR. Splitting a
   large PR into smaller ones gets faster review than a 2,000-line
   omnibus.
5. **Write a PR description that answers**: what changed, why, how to
   test it, and any follow-ups. Screenshots / log snippets for UI or
   trading-path changes.
6. **Don't rebase published branches.** Merge `main` into your branch
   if you need updates. Rebasing rewrites commits other people may be
   reviewing.

## Code style

### Python

- Python 3.11+.
- Follow the existing style. The codebase isn't strictly
  black-formatted but is close — match what's around your changes.
- `import os, sys` — stdlib first, then third-party, then local.
  No relative imports except inside test packages.
- Logging via `logging.getLogger(__name__)`. No `print()` in code
  that runs in containers.
- Type hints are encouraged but not enforced. If a function takes
  `dict`, the function header should say which keys it expects.

### JavaScript / Vue

- Vue 3 Composition API (`<script setup>`).
- Tailwind utility classes; avoid raw CSS unless there's a clear
  reason. The shared design tokens live in
  [`frontend/tailwind.config.js`](../../frontend/tailwind.config.js).
- Component imports relative to `src/`. Keep view-level state in
  composables under `src/composables/`.

### Commit messages

Follow [Conventional Commits](https://www.conventionalcommits.org/)
loosely:

- `feat(strategy): add MeanReversion strategy module`
- `fix(broker): handle Alpaca 429 with exponential backoff`
- `docs(readme): clarify SECRET_AUTH_KEY rotation`
- `refactor(nexus): split phase 8 into resolver + writer`
- `chore: bump pydantic-ai to 1.0.18`

Body explains *why*, not what (the diff already shows what).

## Testing

- Unit tests live in `backend/tests/`. Run with:
  ```bash
  docker compose exec backend pytest backend/tests/ -v
  ```
- For strategy / Nexus changes, also run a manual backtest end-to-end.
- Don't ship a PR that breaks a previously-green test without
  explaining why in the PR description.

There's no CI gate yet — that's on the roadmap. Until there is,
pre-merge review is the gate.

## Trading-critical paths

These directories are higher-stakes than the rest of the codebase.
Extra care in PRs that touch them:

- `backend/broker.py` — the per-instance trading loop.
- `backend/broker_adapters/` — order routing.
- `backend/strategies/` — decision logic that ends in real orders.
- `backend/credential_service.py` — Robinhood token refresh.
- `backend/secret_store.py` — Fernet encryption.

For changes here, include in the PR description:

1. What the change does to live-trading behaviour. Be explicit:
   "this changes the size of every BUY order by 5%" is the kind of
   sentence reviewers need to see.
2. The paper-mode backtest you ran to verify it.
3. Any user-facing migration step (e.g. "users with existing
   instances need to do X").

## AI / vibe-coded PRs

AI-assisted PRs are welcome. Call them out in the PR description so
reviewers know to apply extra scrutiny on the trading-critical paths
above. The reviewer's job is to read the code, not the prompt — but
knowing the provenance helps calibrate expectations.

## Code of conduct

Be respectful. Disagreements are technical, not personal. Maintainers
will close PRs that involve harassment, discriminatory language, or
bad-faith reviews without warning.

## Licensing

All contributions are licensed under the project's
[MIT License](../../LICENSE). By submitting a PR, you agree your
contribution can be distributed under those terms.
