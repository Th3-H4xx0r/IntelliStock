"""Reject and scrub inline credentials in persisted strategy configuration.

Strategy rows may contain references to encrypted ``Models`` records and safe
secret references, but never credential material itself.  Runtime resolution
injects credentials in memory after the persisted configuration crosses this
boundary.
"""
from __future__ import annotations

import copy
import re
from typing import Iterator

from persistence_safety import REDACTION_MARKER, _is_secret_key


_PLACEHOLDER_RE = re.compile(r"^<[^<>]+>$")
_MASKED_RE = re.compile(r"^[*xX•._-]{4,}$")
_SECRET_REFERENCE_RE = re.compile(
    r"^(?:env|vault|aws-sm|gcp-sm|keychain):[A-Za-z0-9_./-]+$"
)


class InlineStrategySecretError(ValueError):
    """Raised when a strategy write contains inline credential material."""


def is_inline_strategy_secret_key(key: object) -> bool:
    """Return whether ``key`` is credential-bearing rather than metadata."""
    return bool(_is_secret_key(key))


def _is_non_material_placeholder(value: object) -> bool:
    if value in (None, ""):
        return True
    if value == REDACTION_MARKER:
        return True
    if not isinstance(value, str):
        return False
    stripped = value.strip()
    if not stripped:
        return True
    return bool(
        _PLACEHOLDER_RE.fullmatch(stripped)
        or _MASKED_RE.fullmatch(stripped)
        or _SECRET_REFERENCE_RE.fullmatch(stripped)
    )


def iter_inline_strategy_secrets(
    value: object,
    *,
    path: str = "",
) -> Iterator[tuple[str, object]]:
    """Yield ``(path, value)`` for credential-bearing keys at any depth."""
    if isinstance(value, dict):
        for key, item in value.items():
            child = f"{path}.{key}" if path else str(key)
            if is_inline_strategy_secret_key(key):
                if not _is_non_material_placeholder(item):
                    yield child, item
                continue
            yield from iter_inline_strategy_secrets(item, path=child)
    elif isinstance(value, (list, tuple)):
        for index, item in enumerate(value):
            child = f"{path}[{index}]" if path else f"[{index}]"
            yield from iter_inline_strategy_secrets(item, path=child)


def scrub_inline_strategy_secrets(
    value: object,
    *,
    reject_material: bool,
) -> object:
    """Deep-copy ``value`` while removing every inline credential field.

    When ``reject_material`` is true, non-placeholder values cause a
    path-only exception.  The exception never contains the stored value.
    Read paths use ``reject_material=False`` to safely expose legacy rows while
    the migration removes their credential fields.
    """
    findings = tuple(iter_inline_strategy_secrets(value))
    if reject_material and findings:
        paths = ", ".join(path for path, _stored in findings)
        raise InlineStrategySecretError(
            f"Inline strategy credentials are forbidden at: {paths}. "
            "Use a linked brokerage, encrypted model record, or runtime secret reference."
        )

    def _scrub(item: object) -> object:
        if isinstance(item, dict):
            clean = {}
            for key, child in item.items():
                if is_inline_strategy_secret_key(key):
                    continue
                clean[key] = _scrub(child)
            return clean
        if isinstance(item, list):
            return [_scrub(child) for child in item]
        if isinstance(item, tuple):
            return tuple(_scrub(child) for child in item)
        if isinstance(item, set):
            return [_scrub(child) for child in sorted(item, key=repr)]
        if isinstance(item, frozenset):
            return [_scrub(child) for child in sorted(item, key=repr)]
        return copy.deepcopy(item)

    return _scrub(value)
