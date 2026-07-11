"""Guard: a crypto strategy basename must not collide with a flat strategy file.

Crypto strategies are listed with bare ids (e.g. ``momentum``) and the broker's
``_load_strategy_class`` resolves ``strategies.<name>`` BEFORE
``strategies.crypto.<name>``. So a future flat ``strategies/<name>.py`` sharing a
basename with a crypto strategy would silently SHADOW the crypto one and
mis-route a crypto instance. This test FAILS the moment such a collision is
introduced, so CI catches it. Pure/dependency-light — filesystem only.
"""
from __future__ import annotations

import os

_BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_STRATEGIES_DIR = os.path.join(_BACKEND_DIR, "strategies")
_CRYPTO_DIR = os.path.join(_STRATEGIES_DIR, "crypto")


def _py_basenames(dirpath: str) -> set:
    if not os.path.isdir(dirpath):
        return set()
    return {
        fn[:-3]
        for fn in os.listdir(dirpath)
        if fn.endswith(".py") and fn != "__init__.py"
    }


def test_no_crypto_strategy_shadowed_by_flat_strategy():
    flat = _py_basenames(_STRATEGIES_DIR)
    crypto = _py_basenames(_CRYPTO_DIR)
    collisions = flat & crypto
    assert not collisions, (
        "Crypto strategy basename(s) collide with a flat strategies/<name>.py: "
        f"{sorted(collisions)}. broker._load_strategy_class resolves "
        "strategies.<name> BEFORE strategies.crypto.<name>, so the flat file "
        "would SHADOW the crypto strategy. Rename one, or list the crypto "
        "strategy with a 'crypto_'-prefixed id to disambiguate."
    )
