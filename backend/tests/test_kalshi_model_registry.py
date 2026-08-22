"""Model registry writers: save version, champion get/set, instance->default
fallback. Runs on the shared `store` fixture (FakeStore, or real Postgres when
PG_TEST_DSN is set) — it used to drive a hand-rolled fake of the rethinkdb
query chain, which could only ever agree with itself."""
import os
import sys
import types

sys.modules.setdefault("socketio", types.ModuleType("socketio"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest

from kalshi import db


@pytest.fixture
def kstore(store, monkeypatch):
    monkeypatch.setattr(db, "store", store)
    return store


def _doc(id, inst, ll):
    return {"id": id, "instance_id": inst, "kind": "calibrator",
            "is_champion": False, "metrics": {"cal_logloss": ll}}


def test_save_and_get_champion(kstore):
    db.save_model_version(None, _doc("v1", "inst-A", 0.80))
    db.set_champion(None, "v1", "inst-A")
    champ = db.get_champion(None, "inst-A")
    assert champ and champ["id"] == "v1" and champ["is_champion"] is True


def test_set_champion_demotes_prior(kstore):
    db.save_model_version(None, _doc("v1", "inst-A", 0.80))
    db.save_model_version(None, _doc("v2", "inst-A", 0.75))
    db.set_champion(None, "v1", "inst-A")
    db.set_champion(None, "v2", "inst-A")  # promote v2
    champ = db.get_champion(None, "inst-A")
    assert champ["id"] == "v2"
    # exactly one champion in scope
    champs = [r for r in kstore.run("KalshiModelRegistry")
              if r["instance_id"] == "inst-A" and r["is_champion"]]
    assert len(champs) == 1


def test_instance_falls_back_to_default_scope(kstore):
    db.save_model_version(None, _doc("g1", "__default__", 0.9))
    db.set_champion(None, "g1", "__default__")
    # instance with no champion of its own -> gets the global default
    champ = db.get_champion(None, "inst-NEW")
    assert champ and champ["id"] == "g1"


def test_get_champion_none_when_empty(kstore):
    assert db.get_champion(None, "inst-A") is None
