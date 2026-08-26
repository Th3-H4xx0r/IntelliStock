import json

import price_utils


class _Store:
    def __init__(self, document=None):
        self.document = document
        self.inserts = []

    def get(self, table_name, cache_id):
        return self.document

    def insert(self, table_name, document, conflict=None):
        self.inserts.append((table_name, document, conflict))


class _Schema:
    @staticmethod
    def ensure_table(table_name):
        return table_name


def _install_store(monkeypatch, document=None):
    store = _Store(document)
    monkeypatch.setattr(price_utils, "_store", store)
    monkeypatch.setattr(price_utils, "_db_schema", _Schema())
    monkeypatch.setattr(price_utils, "_table_ensured", {})
    return store


def _complete_bar(timestamp="2024-01-31T21:00:00Z"):
    return {"t": timestamp, "c": 100.0}


def test_cached_empty_bars_are_refetched_instead_of_treated_as_data(monkeypatch):
    store = _install_store(monkeypatch, {"bars": "[]", "compressed": False})
    fetched = [_complete_bar()]
    calls = []

    bars, from_cache = price_utils.get_bars_chunk_cached(
        object(),
        "QQQ",
        "2024-01-01",
        "2024-02-01",
        "1Day",
        "iex",
        lambda: calls.append(True) or fetched,
        adjustment="split",
    )

    assert calls == [True]
    assert bars == fetched
    assert from_cache is False
    assert json.loads(store.inserts[-1][1]["bars"]) == fetched


def test_empty_fetch_is_not_persisted_as_authoritative_market_data(monkeypatch):
    store = _install_store(monkeypatch)

    bars, from_cache = price_utils.get_bars_chunk_cached(
        object(),
        "QQQ",
        "2024-01-01",
        "2024-02-01",
        "1Day",
        "iex",
        lambda: [],
        adjustment="split",
    )

    assert bars == []
    assert from_cache is False
    assert store.inserts == []
