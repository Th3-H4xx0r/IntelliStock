import pytest


def test_invalid_brokerage_and_origins_reject_before_decrypt(monkeypatch):
    import read_only_broker_snapshot as mod
    monkeypatch.setattr(mod, "decrypt_required", lambda *a, **k: (_ for _ in ()).throw(AssertionError("decrypt")))
    for row in ({"brokerage_type": "other"}, {"brokerage_type": "alpaca", "alpaca_paper": True},
                {"brokerage_type": "alpaca", "alpaca_paper": False, "alpaca_base_url": "https://evil.example"},
                {"brokerage_type": "alpaca", "alpaca_paper": False, "alpaca_base_url": "https://api.alpaca.markets/path"}):
        with pytest.raises(RuntimeError):
            mod.read_authoritative_snapshot({}, row)


def test_read_only_snapshot_uses_exact_get_endpoints_and_validates_shapes(monkeypatch):
    import read_only_broker_snapshot as mod
    calls = []
    monkeypatch.setattr(mod, "decrypt_required", lambda value, **kw: "value")
    def get(url, headers):
        calls.append(url)
        if url.endswith("/account"): return {"account_number": "acct"}
        return []
    monkeypatch.setattr(mod, "_get_json", get)
    row = {"brokerage_type": "alpaca", "alpaca_paper": False, "alpaca_base_url": "https://api.alpaca.markets", "alpaca_key": "x", "alpaca_secret": "y", "alpaca_account_number": "acct"}
    result = mod.read_authoritative_snapshot({}, row)
    assert calls == ["https://api.alpaca.markets/v2/account", "https://api.alpaca.markets/v2/positions", "https://api.alpaca.markets/v2/orders?status=open&limit=500", "https://api.alpaca.markets/v2/orders?status=all&limit=500", "https://api.alpaca.markets/v2/account/activities/FILL?direction=desc&page_size=100"] and result["account_id"] == "acct"
    with pytest.raises(RuntimeError):
        monkeypatch.setattr(mod, "_get_json", lambda *a: {"account_number": "acct"}) or mod.read_authoritative_snapshot({}, row)


@pytest.mark.parametrize("full_index", [2, 3, 4])
def test_snapshot_fails_closed_when_broker_history_may_be_truncated(
        monkeypatch, full_index):
    import read_only_broker_snapshot as mod

    monkeypatch.setattr(mod, "decrypt_required", lambda value, **kw: "value")
    values = [{"account_number": "acct"}, [], [], [], []]
    limits = {2: 500, 3: 500, 4: 100}
    values[full_index] = [{"id": str(index)}
                          for index in range(limits[full_index])]
    monkeypatch.setattr(mod, "_get_json", lambda *args: values.pop(0))
    row = {
        "brokerage_type": "alpaca",
        "alpaca_paper": False,
        "alpaca_base_url": "https://api.alpaca.markets",
        "alpaca_key": "x",
        "alpaca_secret": "y",
        "alpaca_account_number": "acct",
    }
    with pytest.raises(RuntimeError, match="truncated"):
        mod.read_authoritative_snapshot({}, row)


@pytest.mark.parametrize("bad", [{"account_number": "other"}, [], None])
def test_account_mismatch_or_malformed_fails_closed(monkeypatch, bad):
    import read_only_broker_snapshot as mod
    monkeypatch.setattr(mod, "decrypt_required", lambda *a, **k: "derived-value")
    monkeypatch.setattr(mod, "_get_json", lambda *a: bad)
    row = {"brokerage_type": "alpaca", "alpaca_paper": False, "alpaca_base_url": "https://api.alpaca.markets", "alpaca_key": "x", "alpaca_secret": "y", "alpaca_account_number": "acct"}
    with pytest.raises(RuntimeError) as exc:
        mod.read_authoritative_snapshot({}, row)
    assert "derived-value" not in repr(exc.value)


@pytest.mark.parametrize("bad_index", [1, 2, 3, 4])
def test_each_non_list_collection_fails_closed(monkeypatch, bad_index):
    import read_only_broker_snapshot as mod
    monkeypatch.setattr(mod, "decrypt_required", lambda *a, **k: "derived")
    values = [{"account_number": "acct"}, [], [], [], []]
    values[bad_index] = {}
    monkeypatch.setattr(mod, "_get_json", lambda *a: values.pop(0))
    row = {"brokerage_type": "alpaca", "alpaca_paper": False, "alpaca_base_url": "https://api.alpaca.markets", "alpaca_key": "x", "alpaca_secret": "y", "alpaca_account_number": "acct"}
    with pytest.raises(RuntimeError):
        mod.read_authoritative_snapshot({}, row)


def test_get_json_refuses_redirect_and_uses_get(monkeypatch):
    import read_only_broker_snapshot as mod
    captured, requests = [], []
    class Response:
        status = 302
        def __enter__(self): return self
        def __exit__(self, *args): pass
    class Opener:
        def open(self, request, timeout): requests.append(request); return Response()
    monkeypatch.setattr(mod, "build_opener", lambda handler: captured.append(handler) or Opener())
    with pytest.raises(RuntimeError, match="redirect rejected"):
        mod._get_json("https://api.alpaca.markets/v2/account", {"X": "y"})
    assert len(captured) == 1 and captured[0].redirect_request(None, None, None, None, None, None, None, None) is None
    assert len(requests) == 1 and requests[0].get_method() == "GET"
