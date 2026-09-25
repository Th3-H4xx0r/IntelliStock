"""Ported logic from github.com/tmasters2876/swing-trader @ c2afa71 ("ST").

Every module names the ST file and line range it came from. Pure functions
are copied verbatim; only I/O changed: files became db.store tables, Pushover
became notifications, the Anthropic SDK became llm_utils. The operator-
approved deviations are spec §9 items 1-17, each marked at its line.

Spec: docs/superpowers/specs/2026-09-24-swing-trader-port-design.md
"""
