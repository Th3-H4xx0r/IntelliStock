# Contributing to IntelliStock

Thanks for your interest in contributing. IntelliStock is a self-hosted
algorithmic trading platform, and the project moves fastest when
contributors run the same code paths in paper mode that they're proposing
to change. This document covers everything you need to know to land a
change here.

By participating in this project you agree to abide by the
[Code of Conduct](./CODE_OF_CONDUCT.md).

## Ways to contribute

- **Report a bug.** Open an issue using the **Bug report** template.
  Include reproduction steps, the strategy / engine involved, and any
  relevant log lines (with secrets scrubbed).
- **Propose a feature.** Open an issue using the **Feature request**
  template. The triage label is added automatically.
- **Improve docs.** Typos, clarifications, and new How-To guides under
  `docs/` are always welcome.
- **Submit a strategy or engine improvement.** See the workflow below.
- **Report a security vulnerability.** Do *not* open a public issue.
  See [SECURITY.md](./SECURITY.md) for the private disclosure process.

## Asking questions

For open-ended questions ("how does X work?", "is this a good way to
structure Y?"), use [GitHub Discussions](https://github.com/Th3-H4xx0r/IntelliStock/discussions)
instead of an issue. Issues are for bugs and concrete proposals.

## Development setup

The fastest path to a working environment is the same one users run:

```bash
git clone https://github.com/Th3-H4xx0r/IntelliStock.git
cd IntelliStock
./install.sh        # or install.ps1 on Windows
```

That brings up the full stack (RethinkDB, Neo4j, backend, api,
frontend, price-service, backtest-engine, credential-service) in
Docker. From there, `docker compose logs -f <service>` tails any
container while you iterate.

For local-without-Docker development, see the **From source
(development)** section of the [README](./README.md) — you'll need
Python 3.11+, Node 22+, and the TA-Lib system library.

## Branch + PR workflow

1. **Fork** the repo (or create a topic branch if you have write
   access).
2. **Branch naming**: `<type>/<short-description>`, e.g.
   `fix/credential-refresh-race`, `feat/macd-divergence-strategy`,
   `docs/install-on-arm-mac`.
3. **Keep PRs focused**. One logical change per PR. If you find
   yourself fixing an unrelated bug along the way, open a second PR.
4. **Open a draft PR early** if you want feedback on direction before
   you finish the implementation.
5. **Link the issue** the PR addresses in the description
   (`Fixes #123`, `Refs #456`).
6. **Fill out the PR template** — it's short and exists so reviewers
   don't have to chase context.

## Signed commits — required

The `main` branch ruleset rejects unsigned commits. Every commit on
every PR must be signed, otherwise GitHub will block the merge. There
are two easy options:

### Option A: SSH signing (recommended if you already use SSH for git)

```bash
# Tell git to sign with the SSH key you push with
git config --global gpg.format ssh
git config --global user.signingkey ~/.ssh/id_ed25519.pub
git config --global commit.gpgsign true

# Then add the same public key to GitHub as a "Signing key":
# https://github.com/settings/ssh/new (set Key type = Signing Key)
```

### Option B: GPG signing

```bash
gpg --full-generate-key                      # follow the prompts
gpg --list-secret-keys --keyid-format=long   # copy the key ID
git config --global user.signingkey <KEY_ID>
git config --global commit.gpgsign true
gpg --armor --export <KEY_ID>                # paste into GitHub > Settings > GPG keys
```

### Option C: GitHub web edits

Small typo fixes can be edited directly in the GitHub web UI — those
commits are auto-signed by GitHub, so no local setup needed.

Verify a commit is signed with `git log --show-signature -1`. PRs with
unsigned commits won't merge; the easiest fix is to amend with `git
commit --amend -S --no-edit` and force-push your branch.

## Code style

There is no enforced formatter on the Python or JS side right now, but:

- **Python**: PEP 8, type hints on public functions, prefer explicit
  imports over `from foo import *`.
- **JavaScript / Vue**: match the conventions of the surrounding file.
  The frontend is plain Vite + Vue 3 with single-file components.
- **Commits**: imperative mood ("Fix race in credential refresh", not
  "Fixed" or "Fixes"). Body wrapped at ~72 chars.

If you'd like to propose adopting `ruff` / `prettier` / `eslint`,
open an issue first — it's a one-PR change but it touches every file.

## Testing

There isn't a comprehensive automated test suite yet (this is on the
roadmap). For now:

- **For strategy / engine changes**: run the affected strategy in
  backtest mode against at least one historical window and attach the
  realised P&L curve to the PR.
- **For backend API changes**: hit the endpoints via curl or the
  embedded chatbot's tool calls and paste sample request / response
  pairs into the PR description.
- **For frontend changes**: include before / after screenshots.
- **For anything touching live trading**: paper mode only, period.
  Never test live-broker code paths against a real-money account
  before review.

## What gets merged

A PR is likely to be merged when:

- It does one clear thing.
- The intent is documented in the description (the **Why**, not just
  the **What** — the diff already shows the what).
- It doesn't regress an existing strategy's backtest behaviour
  without explanation.
- It doesn't add a dependency that's only used in one place.
- It doesn't leak credentials, hard-code API keys, or weaken auth.

## License

By contributing, you agree that your contributions are licensed under
the same [MIT License](./LICENSE) that covers the project.
