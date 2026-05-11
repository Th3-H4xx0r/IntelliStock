<!--
Thanks for opening a PR! A few things that make review faster:
- Keep the description focused on the WHY. The diff shows the WHAT.
- Link the related issue ("Fixes #123" / "Refs #456").
- Make sure every commit is signed. The main ruleset rejects unsigned commits.
- If this touches a strategy or engine, include a backtest sample or
  the P&L curve in the description.
-->

## Summary

<!-- One or two sentences on what this change does and why it's needed. -->

## Related issue

<!-- Fixes # / Refs # / N/A -->

## Type of change

- [ ] Bug fix (non-breaking change that fixes an issue)
- [ ] New feature (non-breaking change that adds functionality)
- [ ] Breaking change (fix or feature that changes existing behaviour)
- [ ] Documentation only
- [ ] Refactor / cleanup (no functional change)
- [ ] Build / CI / infrastructure

## How was this tested?

<!--
- Backtest results (with date range + symbols), screenshots, or
  curl / chatbot output. "Manually verified" is fine for small docs
  changes but not for code paths.
- For anything touching live trading: PAPER MODE ONLY.
-->

## Checklist

- [ ] Every commit is signed (`git log --show-signature` confirms it)
- [ ] PR is focused on one logical change
- [ ] Docs / README / inline comments updated if behaviour changed
- [ ] No secrets, API keys, or `.env` content in the diff
- [ ] I have read [CONTRIBUTING.md](../CONTRIBUTING.md) and agree to license my contribution under the MIT License
