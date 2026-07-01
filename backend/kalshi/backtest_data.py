"""Data layer for the Kalshi soccer backtest.

`run_backtest` (built separately) replays historical fixtures tick-by-tick; this
module gives it everything it needs — the fixture list, final scores, Kalshi
price candles, and sharp (Pinnacle) odds — while fetching from the network APIs
ONCE and caching the immutable historical data, so re-running the same backtest
costs zero additional API calls.

Design:
- `final_score` is Kalshi-primary (settled markets, free/public) with an
  OddsPapi-fixture-result fallback (used only when Kalshi has no ticker match or
  no client is configured). An unsettled cache row is retried on every call
  until it settles; a settled row is never re-fetched.
- `fixtures`/`candles`/`sharp_odds` are simple fetch-or-cache: a cache hit
  never touches the network; a cache miss fetches once and stores the result.
  `fixtures` keys its cache on the (leagues, start_date, end_date) query.
- All OddsPapi calls (the metered ~250/mo free tier) are guarded by
  `ingest_odds.should_scan`; a budget-exhausted call returns cached-or-None and
  LOGS the skip — it is never silently dropped.
- The RethinkDB table access is abstracted behind `_table_get`/`_table_put` so
  tests can inject a plain in-memory dict (`tables={}`) instead of a live `conn`.
"""
from __future__ import annotations

import logging
from typing import Any, Callable, Optional

from kalshi.client import result_side_from_markets
from kalshi.data.sources.odds_api import match_event
from kalshi.data.ticker_names import parse_market_ticker
from kalshi.ingest_odds import should_scan
from kalshi.normalize import normalize_team

log = logging.getLogger(__name__)

_DEFAULT_SOCCER_SERIES = ("KXWCGAME",)
_DEFAULT_MARKET_STATUSES = ("open", "settled")

# OddsPapi uses a single sport id for ALL soccer (docs: sportId=10); leagues are
# distinguished by tournament name in the results, NOT by sport id. So every
# league resolves to the soccer id — league-agnostic, no per-league hardcoding.
# (A per-tournament name filter is a follow-up once a live key confirms the field.)
_SOCCER_SPORT_ID = 10
_SPORT_ID_BY_LEAGUE: dict = {}
_DEFAULT_SPORT_ID = _SOCCER_SPORT_ID


def _fx_get(fx: Any, key: str, default: Any = None) -> Any:
    """Read a field off a fixture that may be a dict or a dataclass-like object."""
    if isinstance(fx, dict):
        return fx.get(key, default)
    return getattr(fx, key, default)


def _event_prefix(ticker: str) -> str:
    """Drop the trailing '-<PICK>' suffix, leaving the shared event prefix
    ('KXWCGAME-26JUL01ENGCOD-ENG' -> 'KXWCGAME-26JUL01ENGCOD')."""
    return (ticker or "").rsplit("-", 1)[0]


def _group_tickers_by_event(tickers: list[str]) -> dict[str, list[str]]:
    groups: dict[str, list[str]] = {}
    for t in tickers:
        if not t:
            continue
        groups.setdefault(_event_prefix(t), []).append(t)
    return groups


class BacktestDataProvider:
    """Fetch-or-cache data provider for the Kalshi soccer backtest. All
    dependencies are injectable so unit tests run with fakes and no network."""

    def __init__(
        self,
        *,
        kalshi_client: Any = None,
        oddspapi_client: Any = None,
        conn: Any = None,
        tables: Optional[dict[str, dict[str, dict]]] = None,
        budget_used_getter: Optional[Callable[[], int]] = None,
        budget_bumper: Optional[Callable[[], None]] = None,
        logger: Optional[logging.Logger] = None,
    ):
        self.kalshi_client = kalshi_client
        self.oddspapi_client = oddspapi_client
        self.conn = conn
        # `tables` (even an empty dict) opts into the in-memory fake store used
        # by unit tests; `None` means "use the real `conn`" (or no-op if that's
        # also None — a provider with zero storage backend still functions, it
        # just never caches).
        self._tables = tables
        self._budget_used_getter = budget_used_getter or (lambda: 0)
        self._budget_bumper = budget_bumper or (lambda: None)
        self.log = logger or log
        self.api_calls = 0
        self.cache_hits = 0

    # --- table access (fake dict OR live RethinkDB `conn`) ---

    def _table_get(self, table: str, key: Optional[str]) -> Optional[dict]:
        if key is None:
            return None
        if self._tables is not None:
            return self._tables.get(table, {}).get(key)
        if self.conn is None:
            return None
        try:
            from kalshi import db
            return db._r.db(db.DB_NAME).table(table).get(key).run(self.conn)
        except Exception:
            return None

    def _table_put(self, table: str, doc: dict, pk_field: str) -> None:
        key = doc.get(pk_field)
        if key is None:
            return
        if self._tables is not None:
            self._tables.setdefault(table, {})[key] = doc
            return
        if self.conn is None:
            return
        try:
            from kalshi import db
            db._r.db(db.DB_NAME).table(table).insert(doc, conflict="replace").run(self.conn)
        except Exception:
            pass

    # --- OddsPapi budget guard ---

    def _oddspapi_allowed(self, cost: int = 1) -> bool:
        return should_scan(self._budget_used_getter(), cost)

    # --- fixtures ---

    def fixtures(self, leagues: list[str], start_date: str, end_date: str) -> list[dict]:
        """Fixture list for `leagues` over [start_date, end_date] (OddsPapi
        dates, YYYY-MM-DD). Each dict carries the parsed OddsPapi fields
        (home/away/kickoff_ts/home_score/away_score/result/settled) plus
        fixture_id/sport/league — a superset of the plain Fixture DTO so
        `final_score`'s fallback has the data it needs without a second call.

        The listing itself is cached per (leagues, start_date, end_date) query
        — a backtest is over a past, immutable window, so re-running it never
        re-hits OddsPapi for the fixture list."""
        cache_key = "fixturelist|" + ",".join(sorted(leagues or [])) + "|" + start_date + "|" + end_date
        cached = self._table_get("KalshiBtFixtureList", cache_key)
        if cached is not None:
            self.cache_hits += 1
            return cached.get("fixtures", [])
        out: list[dict] = []
        if self.oddspapi_client is None:
            return out
        # All soccer leagues share one OddsPapi sport id, so fetch once per unique
        # sport id (not per league) — league-agnostic and budget-frugal. Fixtures
        # are deduped by id; each is tagged with its own tournament when the feed
        # provides one, else the requested league(s).
        want = list(leagues or [])
        sport_ids = sorted({_SPORT_ID_BY_LEAGUE.get((lg or "").strip().lower(), _DEFAULT_SPORT_ID)
                            for lg in (want or [""])})
        seen: set = set()
        for sport_id in sport_ids:
            if not self._oddspapi_allowed(1):
                self.log.warning(
                    "BacktestDataProvider.fixtures: OddsPapi budget exhausted, "
                    "skipping sport_id=%s %s..%s", sport_id, start_date, end_date)
                continue
            try:
                rows = self.oddspapi_client.list_fixtures(sport_id, start_date, end_date)
            except Exception:
                self.log.warning("BacktestDataProvider.fixtures: list_fixtures failed for sport_id=%s", sport_id)
                continue
            self.api_calls += 1
            self._budget_bumper()
            for r in rows:
                fid = r.get("fixture_id")
                if fid in seen:
                    continue
                seen.add(fid)
                out.append({
                    "fixture_id": fid,
                    "sport": "soccer",
                    "league": r.get("league") or r.get("tournament") or (want[0] if want else ""),
                    "home": normalize_team(r.get("home", "")),
                    "away": normalize_team(r.get("away", "")),
                    "kickoff_ts": r.get("kickoff_ts"),
                    "home_score": r.get("home_score"),
                    "away_score": r.get("away_score"),
                    "result": r.get("result"),
                    "settled": r.get("settled", False),
                })
        if out:
            self._table_put("KalshiBtFixtureList", {"id": cache_key, "fixtures": out}, "id")
        return out

    # --- candles (Kalshi price history — immutable once traded) ---

    def candles(self, ticker: str, start_ts: int = 0, end_ts: int = 4102444800) -> list[dict]:
        """Kalshi candlesticks for `ticker`, cached forever once fetched (the
        history of a settled/closed market never changes)."""
        cached = self._table_get("KalshiHistCandles", ticker)
        if cached is not None:
            self.cache_hits += 1
            return cached.get("candles", [])
        if self.kalshi_client is None:
            return []
        rows = self.kalshi_client.get_candlesticks(ticker, start_ts, end_ts)
        self.api_calls += 1
        self._table_put("KalshiHistCandles", {"id": ticker, "candles": rows}, "id")
        return rows

    # --- sharp odds (OddsPapi historical odds — metered, budget-guarded) ---

    def sharp_odds(self, fx: Any) -> list[dict]:
        """Historical sharp-book odds snapshots for a fixture, cached forever
        once fetched. Budget-gated: an exhausted OddsPapi budget returns
        cached-or-empty and logs the skip; it never calls the client."""
        fixture_id = _fx_get(fx, "fixture_id")
        cached = self._table_get("KalshiHistOdds", fixture_id)
        if cached is not None:
            self.cache_hits += 1
            return cached.get("snapshots", [])
        if self.oddspapi_client is None:
            return []
        if not self._oddspapi_allowed(1):
            self.log.warning(
                "BacktestDataProvider.sharp_odds: OddsPapi budget exhausted, "
                "skipping fixture_id=%s (returning cached-or-empty)", fixture_id)
            return []
        rows = self.oddspapi_client.historical_odds(fixture_id)
        self.api_calls += 1
        self._budget_bumper()
        self._table_put("KalshiHistOdds", {"id": fixture_id, "snapshots": rows}, "id")
        return rows

    # --- final score (Kalshi-primary, OddsPapi-fallback, settled-aware cache) ---

    def final_score(self, fx: Any) -> Optional[str]:
        """'home' | 'draw' | 'away', or None if unsettled/unknown. A cached
        settled result is returned without any network call; a cached
        unsettled result is retried (the game may have finished since)."""
        fixture_id = _fx_get(fx, "fixture_id")
        cached = self._table_get("KalshiHistFixtures", fixture_id)
        if cached is not None and cached.get("settled"):
            self.cache_hits += 1
            return cached.get("result")
        result, settled = self._resolve_final_score(fx)
        self._table_put(
            "KalshiHistFixtures",
            {"fixture_key": fixture_id, "settled": settled, "result": result},
            "fixture_key",
        )
        return result

    def _resolve_final_score(self, fx: Any) -> tuple[Optional[str], bool]:
        # PRIMARY: Kalshi settled markets (free/public).
        tickers = self.kalshi_tickers(fx)
        if tickers and self.kalshi_client is not None:
            any_ticker = next(iter(tickers.values()))
            prefix = _event_prefix(any_ticker)
            series = prefix.split("-", 1)[0]
            try:
                markets = self.kalshi_client.list_settled_markets(series)
                self.api_calls += 1
            except Exception:
                markets = []
            side = result_side_from_markets(markets, prefix)
            if side:
                return side, True
        # FALLBACK: OddsPapi's own fixture result, if the caller already has it
        # (e.g. from `fixtures()`) — never an extra network call.
        if _fx_get(fx, "settled"):
            return _fx_get(fx, "result"), True
        return None, False

    # --- fixture <-> Kalshi ticker resolution ---

    def kalshi_tickers(
        self,
        fx: Any,
        *,
        series_options: tuple[str, ...] = _DEFAULT_SOCCER_SERIES,
        statuses: tuple[str, ...] = _DEFAULT_MARKET_STATUSES,
    ) -> dict[str, str]:
        """Resolve an OddsPapi/normalized fixture to its Kalshi side tickers:
        {"home": ticker, "draw": ticker, "away": ticker}. Reuses
        `ticker_names.parse_market_ticker` to fold a ticker into (home, away,
        pick), and `odds_api.match_event`'s fuzzy-uniqueness matcher to find the
        one Kalshi event that safely corresponds to the fixture. Unmatched
        fixtures return {} and are LOGGED — never guessed."""
        home, away = _fx_get(fx, "home", ""), _fx_get(fx, "away", "")
        tickers = self._list_kalshi_tickers(series_options, statuses)
        groups = _group_tickers_by_event(tickers)
        if not groups:
            self.log.warning(
                "kalshi_tickers: no Kalshi markets available to match fixture %s vs %s", home, away)
            return {}
        events = []
        for prefix, group_tickers in groups.items():
            parsed = parse_market_ticker(group_tickers[0])
            events.append({
                "prefix": prefix,
                "home": parsed.get("home", ""),
                "away": parsed.get("away", ""),
                "tickers": group_tickers,
            })
        ev = match_event(events, home, away)
        if not ev:
            self.log.warning(
                "kalshi_tickers: unmatched fixture %s vs %s (no unique Kalshi event) — not guessing", home, away)
            return {}
        out: dict[str, str] = {}
        for t in ev["tickers"]:
            pick = parse_market_ticker(t).get("pick")
            if pick in ("home", "away", "draw"):
                out[pick] = t
        return out

    def _list_kalshi_tickers(self, series_options: tuple[str, ...], statuses: tuple[str, ...]) -> list[str]:
        if self.kalshi_client is None:
            return []
        tickers: list[str] = []
        for series in series_options:
            for status in statuses:
                try:
                    d = self.kalshi_client.list_markets(status=status, series_ticker=series)
                except Exception:
                    continue
                for m in (d.get("markets") or []):
                    t = m.get("ticker") if isinstance(m, dict) else m
                    if t:
                        tickers.append(t)
        return tickers
