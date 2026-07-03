"""Model registry writers: save version, champion get/set, instance->default
fallback. Uses an in-memory fake of the rethinkdb query chain (no DB)."""
import sys, types
sys.modules.setdefault("socketio", types.ModuleType("socketio"))

from kalshi import db


# --- minimal in-memory fake for the exact query chains db.py uses ---
class _Insert:
    def __init__(self, rows, doc): self.rows, self.doc = rows, doc
    def run(self, conn):
        for d in (self.doc if isinstance(self.doc, list) else [self.doc]):
            self.rows[:] = [r for r in self.rows if r.get("id") != d.get("id")]
            self.rows.append(dict(d))
        return {"inserted": 1}


class _Upd:
    def __init__(self, targets, upd): self.targets, self.upd = targets, upd
    def run(self, conn):
        for r in self.targets:
            r.update(self.upd)
        return {"replaced": len(self.targets)}


class _Limited:
    def __init__(self, matched): self.matched = matched
    def run(self, conn): return list(self.matched)


class _Filtered:
    def __init__(self, rows, pred): self.rows, self.pred = rows, pred
    def _m(self, r): return all(r.get(k) == v for k, v in self.pred.items())
    def limit(self, n): return _Limited([r for r in self.rows if self._m(r)][:n])
    def update(self, upd): return _Upd([r for r in self.rows if self._m(r)], upd)
    def run(self, conn): return [r for r in self.rows if self._m(r)]


class _GetOne:
    def __init__(self, rows, _id): self.rows, self._id = rows, _id
    def _row(self): return next((r for r in self.rows if r.get("id") == self._id), None)
    def update(self, upd):
        r = self._row()
        return _Upd([r] if r else [], upd)
    def run(self, conn): return self._row()


class FakeTable:
    def __init__(self, rows): self.rows = rows
    def insert(self, doc, conflict=None): return _Insert(self.rows, doc)
    def filter(self, pred): return _Filtered(self.rows, pred)
    def get(self, _id): return _GetOne(self.rows, _id)


class FakeR:
    def __init__(self): self.store = {}
    def db(self, name): return self
    def table(self, name): return FakeTable(self.store.setdefault(name, []))


def _setup(monkeypatch):
    fake = FakeR()
    monkeypatch.setattr(db, "_r", fake)
    return fake, object()  # (fake driver, dummy conn)


def _doc(id, inst, ll):
    return {"id": id, "instance_id": inst, "kind": "calibrator",
            "is_champion": False, "metrics": {"cal_logloss": ll}}


def test_save_and_get_champion(monkeypatch):
    _, conn = _setup(monkeypatch)
    db.save_model_version(conn, _doc("v1", "inst-A", 0.80))
    db.set_champion(conn, "v1", "inst-A")
    champ = db.get_champion(conn, "inst-A")
    assert champ and champ["id"] == "v1" and champ["is_champion"] is True


def test_set_champion_demotes_prior(monkeypatch):
    _, conn = _setup(monkeypatch)
    db.save_model_version(conn, _doc("v1", "inst-A", 0.80))
    db.save_model_version(conn, _doc("v2", "inst-A", 0.75))
    db.set_champion(conn, "v1", "inst-A")
    db.set_champion(conn, "v2", "inst-A")  # promote v2
    champ = db.get_champion(conn, "inst-A")
    assert champ["id"] == "v2"
    # exactly one champion in scope
    champs = [r for r in db._r.store["KalshiModelRegistry"]
              if r["instance_id"] == "inst-A" and r["is_champion"]]
    assert len(champs) == 1


def test_instance_falls_back_to_default_scope(monkeypatch):
    _, conn = _setup(monkeypatch)
    db.save_model_version(conn, _doc("g1", "__default__", 0.9))
    db.set_champion(conn, "g1", "__default__")
    # instance with no champion of its own -> gets the global default
    champ = db.get_champion(conn, "inst-NEW")
    assert champ and champ["id"] == "g1"


def test_get_champion_none_when_empty(monkeypatch):
    _, conn = _setup(monkeypatch)
    assert db.get_champion(conn, "inst-A") is None
