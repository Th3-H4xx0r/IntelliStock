from kalshi.models import KalshiMarket
from kalshi.risk import RiskCaps
from kalshi.engine import should_execute, plan_orders, execute_plan, OrderIntent


def _markets():
    return [
        KalshiMarket("KX-HOME", "f1", "home", 48),  # cheap vs fair -> edge
        KalshiMarket("KX-AWAY", "f1", "away", 21),  # ~fairly priced -> no edge
        KalshiMarket("KX-DRAW", "f1", "draw", 19),  # cheap vs fair -> edge
    ]


def _fair():
    return {"home": 0.55, "draw": 0.27, "away": 0.20}


def _caps():
    return RiskCaps(edge_threshold=0.03, max_contracts_per_market=50,
                    max_open_exposure_frac=0.6, per_league_cap_frac=0.25,
                    daily_loss_cap_cents=40000, bankroll_cents=100000)


def test_should_execute_gate():
    assert should_execute("demo", live_enabled=False) is True   # paper always
    assert should_execute("live", live_enabled=False) is False  # live gated
    assert should_execute("live", live_enabled=True) is True
    assert should_execute("prod", live_enabled=True) is True


def test_plan_orders_picks_edges_and_sizes():
    intents, reason = plan_orders(
        fixture_id="f1", league="EPL", fair=_fair(), markets=_markets(),
        caps=_caps(), fee_rate=0.07,
    )
    assert reason == ""
    tickers = [i.market_ticker for i in intents]
    assert "KX-HOME" in tickers and "KX-DRAW" in tickers
    assert "KX-AWAY" not in tickers           # below threshold, dropped
    assert all(0 < i.contracts <= 50 for i in intents)
    assert intents == sorted(intents, key=lambda i: i.edge, reverse=True)


def test_plan_orders_blocked_by_daily_loss():
    intents, reason = plan_orders(
        fixture_id="f1", league="EPL", fair=_fair(), markets=_markets(),
        caps=_caps(), fee_rate=0.07, day_pnl_cents=-40000,
    )
    assert intents == []
    assert "daily" in reason.lower()


class _RecordingClient:
    def __init__(self):
        self.submitted = []

    def submit_order(self, **kw):
        self.submitted.append(kw)
        return {"order_id": "o-" + kw["market_ticker"], "status": "resting"}


def test_execute_plan_dry_run_places_nothing():
    client = _RecordingClient()
    intents = [OrderIntent("KX-HOME", "yes", "buy", 10, 48, 0.05, "r")]
    out = execute_plan(client, intents, dry_run=True, cid_prefix="inst1")
    assert client.submitted == []
    assert out[0]["submitted"] is False
    assert out[0]["client_order_id"] == "inst1-KX-HOME"


def test_execute_plan_live_submits():
    client = _RecordingClient()
    intents = [OrderIntent("KX-HOME", "yes", "buy", 10, 48, 0.05, "r")]
    out = execute_plan(client, intents, dry_run=False, cid_prefix="inst1")
    assert len(client.submitted) == 1
    assert client.submitted[0]["market_ticker"] == "KX-HOME"
    assert client.submitted[0]["client_order_id"] == "inst1-KX-HOME"
    assert out[0]["submitted"] is True


class _MarketClient(_RecordingClient):
    def get_markets(self, fixture_id):
        return _markets()


def _fixture():
    from kalshi.models import Fixture
    return Fixture(fixture_id="f1", sport="soccer", league="EPL",
                   home="A", away="B", kickoff_utc="2026-06-22T14:00:00Z")


def test_run_once_demo_executes():
    from kalshi.engine import run_once
    client = _MarketClient()
    out = run_once(client=client, fixtures=[_fixture()], fair_by_fixture={"f1": _fair()},
                   caps=_caps(), fee_rate=0.07, environment="demo", live_enabled=False,
                   cid_prefix="inst1")
    assert out[0]["dry_run"] is False
    assert len(client.submitted) >= 1   # demo executes


def test_run_once_live_gated_is_dry():
    from kalshi.engine import run_once
    client = _MarketClient()
    out = run_once(client=client, fixtures=[_fixture()], fair_by_fixture={"f1": _fair()},
                   caps=_caps(), fee_rate=0.07, environment="live", live_enabled=False,
                   cid_prefix="inst1")
    assert out[0]["dry_run"] is True
    assert client.submitted == []       # live without the gate places nothing


def test_run_once_aggregate_exposure_cap_blocks():
    # over the max_open_exposure_frac (0.6) -> the aggregate cap must block,
    # placing nothing even on demo.
    from kalshi.engine import run_once
    client = _MarketClient()
    out = run_once(client=client, fixtures=[_fixture()], fair_by_fixture={"f1": _fair()},
                   caps=_caps(), fee_rate=0.07, environment="demo", live_enabled=False,
                   cid_prefix="inst1", open_exposure_frac=0.95)
    assert client.submitted == []
    assert "exposure" in out[0]["blocked"].lower()
