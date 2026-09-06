"""Bulk result accounting must work with the real pool's dictionary rows."""
from contextlib import contextmanager


def test_real_bulk_loader_counts_dictionary_cursor_results(monkeypatch):
    from db import store

    class Cursor:
        def __enter__(self): return self
        def __exit__(self, *_): pass
        def execute(self, sql, params):
            self.sql = sql
        def fetchall(self):
            return [{'was_insert': True}, {'was_insert': False}]

    class Connection:
        def cursor(self): return Cursor()
        def commit(self): pass

    @contextmanager
    def connection():
        yield Connection()

    monkeypatch.setattr(store.dbpool, 'connection', connection)
    result = store.insert_bulk('OutlierUniverseFeatures',
                               [{'id': 'one'}, {'id': 'two'}, {'id': 'three'}])
    assert (result.inserted, result.replaced, result.unchanged) == (1, 1, 1)
