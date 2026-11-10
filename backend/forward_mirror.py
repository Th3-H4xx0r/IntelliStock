"""F14_F paper mirror: trade the frozen forward-test rule on one Alpaca paper account.

The rule is never re-implemented here. ``research/forward-test-2026-11`` is
bind-mounted read-only at ``FROZEN_DIR`` and runs in a subprocess (its
``strategies.py`` would otherwise collide with ``backend/strategies/``). Before
the mirror may trade, every file must match the SHA-256 block in its FROZEN.md,
and its ``repro_check.py`` must print ``OVERALL: PASS`` in this image, so a
different pandas cannot silently change the rule.

Each NYSE session, from ``ACT_MINUTES`` before the close until a hard deadline
``LATEST_ACT_MINUTES`` before it (the rule fills at the close), it re-fetches
the frozen data. The frozen fetcher drops today's unsettled row, so the frame
ends at the previous close. It then runs ``strategies.run_f14_live`` and:

  * TRADE: rebalances the account to the decision's target weights;
  * HOLD: leaves the drifted book alone. The exceptions are a never-synced
    account (first run) or a weight more than
    ``DRIFT_BAND`` off the model's drifted book. Then it rebalances to the
    model's current book.

The deadline bounds the fetch, and the clock is re-read before the sells and
again before the buys, so no order is sent after it. After each close the mirror
records the fills and writes one ``close`` row comparing the account to the model.

Safety. The brokerage row must be Alpaca paper. The account number must be the
pinned one. The account may hold nothing outside the rule's ETFs and may have no
open order the mirror did not place. No running IntelliStock instance may be
bound to the same brokerage. Orders are at-most-once per session: the session is
marked acted before the first order goes out, and a crash mid-rebalance is
repaired by the next session's sync or drift check, never by a resend.

Ledger: table ``ForwardMirrorLedger``. Row ids:
``f14-paper|<YYYY-MM-DD>|act`` (also carries a pending session's last error),
``f14-paper|<YYYY-MM-DD>|close``, ``f14-paper|<YYYY-MM-DD>|error`` (failures
after the session acted), ``f14-paper|boot|<utc iso>``, and the state row
``f14-paper|state``.

Runbook: docs/runbooks/f14-paper-mirror.md
"""
from __future__ import annotations

import datetime as dt
import functools
import hashlib
import json
import math
import os
import re
import subprocess
import sys
import time
from dataclasses import asdict, dataclass
from typing import Callable, Optional
from zoneinfo import ZoneInfo

MIRROR = "f14-paper"
TABLE = "ForwardMirrorLedger"
STATE_ID = f"{MIRROR}|state"

# The "TMasters Swing" paper account PA3TRLBV6ZNZ (stored in IntelliStock as
# the "Alpaca Crypto Paper" brokerage). 2026-11-09: the operator ruled that
# PA37JPXGU196 ("Swing Trade Paper", 1169c5e6...) must not be touched, so the
# mirror is pinned back here; it refuses with `unauthorized` until this row's
# API keys are regenerated in Alpaca and saved in the web UI. The shared
# "Alpaca Paper" row (PA3IBY5S84PG) hosts strategy-ht-paper and must never be
# traded from here.
DEFAULT_BROKERAGE_ID = "b0d7dc67-f12b-4959-83db-0d324c6bc517"
DEFAULT_ACCOUNT_NUMBER = "PA3TRLBV6ZNZ"
ORDER_PREFIX = "f14m-"

#: Frozen book key -> ETF. ``tbill`` is the residual cash (negative = margin).
TICKERS = {"market": "SPY", "bond10": "IEF", "MTUM": "MTUM", "IWD": "IWD", "fund": "DBMF"}
CASH_KEYS = frozenset({"tbill", "cash"})
UNIVERSE = frozenset(TICKERS.values())

COST_BPS = 5.0           # FROZEN.md section 2, primary cost
MAX_GROSS = 2.0          # FROZEN.md section 1, gross cap
ACT_MINUTES = 20         # start acting this many minutes before the close
LATEST_ACT_MINUTES = 3   # hard deadline: no order is sent closer to the close than this
DRIFT_BAND = 0.05        # max |account - model| weight before a repair rebalance
MIN_ORDER_USD = 20.0
FETCH_RETRY_SECONDS = 300
FETCH_TIMEOUT_SECONDS = 600
MIN_FETCH_SECONDS = 30   # don't start a fetch with less time than this before the deadline
SNAPSHOT_AFTER_ET = dt.time(18, 30)   # fetch_data keeps today's row from 18:00 ET
SELL_FILL_WAIT_SECONDS = 90
LOOP_SECONDS = 60
TERMINAL = frozenset({"filled", "canceled", "rejected", "expired"})

ET = ZoneInfo("America/New_York")


class MirrorRefused(RuntimeError):
    """A standing condition under which the mirror must not trade."""


# ---------------------------------------------------------------- frozen dir

_HASH_LINE = re.compile(r"^([0-9a-f]{64})  (\S.*)$")


def parse_hash_block(frozen_md: str) -> dict:
    """{relative path: sha256} from FROZEN.md's ``shasum -a 256`` block."""
    out = {}
    for line in frozen_md.splitlines():
        m = _HASH_LINE.match(line.strip())
        if m:
            out[m.group(2)] = m.group(1)
    return out


def verify_frozen(frozen_dir: str) -> list:
    """Every problem with the frozen dir; empty means every pinned file matches."""
    md = os.path.join(frozen_dir, "FROZEN.md")
    try:
        with open(md, encoding="utf-8") as f:
            pins = parse_hash_block(f.read())
    except OSError as e:
        return [f"FROZEN.md unreadable: {e}"]
    if not pins:
        return ["FROZEN.md has no SHA-256 block"]
    problems = []
    for rel, want in sorted(pins.items()):
        path = os.path.join(frozen_dir, rel)
        try:
            with open(path, "rb") as f:
                got = hashlib.sha256(f.read()).hexdigest()
        except OSError:
            problems.append(f"missing {rel}")
            continue
        if got != want:
            problems.append(f"hash mismatch {rel}")
    return problems


#: The only variables the frozen subprocess sees: no DB, broker or LLM secrets.
_FROZEN_ENV_KEYS = ("PATH", "HOME", "TZ", "LANG", "LC_ALL", "SSL_CERT_FILE", "REQUESTS_CA_BUNDLE",
                    "HTTP_PROXY", "HTTPS_PROXY", "NO_PROXY", "http_proxy", "https_proxy", "no_proxy")


def _frozen_env() -> dict:
    env = {k: os.environ[k] for k in _FROZEN_ENV_KEYS if k in os.environ}
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    return env


def run_repro(frozen_dir: str, timeout: int = 900) -> tuple:
    """(passed, last lines) of the frozen reproduction check, run in this image."""
    p = subprocess.run([sys.executable, "repro_check.py"], cwd=frozen_dir, env=_frozen_env(),
                       capture_output=True, text=True, timeout=timeout)
    tail = "\n".join((p.stdout or "").strip().splitlines()[-3:] + (p.stderr or "").strip().splitlines()[-3:])
    return p.returncode == 0 and "OVERALL: PASS" in (p.stdout or ""), tail


#: Runs inside the frozen dir: fetch (optionally), build the frame, run F14_F_live.
_NEXT_SNIPPET = r"""
import json, sys
import fetch_data, strategies
data_dir, do_fetch, cost = sys.argv[1], sys.argv[2] == "1", float(sys.argv[3])
if do_fetch:
    fetch_data.fetch(data_dir)
P = fetch_data.build_frame(data_dir)
end = str(P.index[-1].date())
name = strategies.PRIMARY
r = strategies.run_f14_live(P, strategies.INCEPTION[name], end, cost)
print("__F14_NEXT__" + json.dumps({"last_date": end, "next": r["next"]}))
"""


def frozen_next(frozen_dir: str, data_dir: str, fetch: bool = True, timeout: float = FETCH_TIMEOUT_SECONDS) -> tuple:
    """(last close date, next-session info) from the frozen F14_F_live rule."""
    os.makedirs(data_dir, exist_ok=True)
    p = subprocess.run([sys.executable, "-c", _NEXT_SNIPPET, data_dir, "1" if fetch else "0", str(COST_BPS)],
                       cwd=frozen_dir, env=_frozen_env(), capture_output=True, text=True, timeout=timeout)
    for line in (p.stdout or "").splitlines():
        if line.startswith("__F14_NEXT__"):
            d = json.loads(line[len("__F14_NEXT__"):])
            return dt.date.fromisoformat(d["last_date"]), d["next"]
    raise RuntimeError("frozen rule failed: " + "\n".join((p.stderr or "").strip().splitlines()[-5:]))


# ---------------------------------------------------------------- pure logic

def model_weights(book: dict) -> dict:
    """Frozen book {key: weight} -> {ETF: weight}, cash dropped."""
    out = {}
    for k, w in (book or {}).items():
        if k in CASH_KEYS:
            continue
        if k not in TICKERS:
            raise ValueError(f"unknown frozen book key {k!r}")
        w = float(w)
        if w < -1e-9:
            raise ValueError(f"negative weight for {k}: {w}")
        if w > 1e-9:
            out[TICKERS[k]] = w
    return out


def account_weights(equity: float, market_values: dict) -> dict:
    if equity <= 0:
        return {}
    return {s: mv / equity for s, mv in market_values.items() if abs(mv) > 0}


def max_deviation(a: dict, b: dict) -> float:
    return max((abs(a.get(s, 0.0) - b.get(s, 0.0)) for s in set(a) | set(b)), default=0.0)


def choose_target(nxt: dict, acct_w: dict, synced: bool, band: float = DRIFT_BAND) -> tuple:
    """(target weights or None, reason).

    A TRADE target is the rule's own and must respect the frozen gross cap. A
    HOLD book can legitimately drift above it (a 1.9x book after a selloff), so
    a sync or repair to it is scaled down to the cap rather than refused."""
    if nxt.get("action") == "TRADE":
        target = model_weights(nxt["target"])
        if sum(target.values()) > MAX_GROSS + 1e-6:
            raise MirrorRefused(f"TRADE target gross {sum(target.values()):.3f} above the frozen cap {MAX_GROSS}")
        return target, "trade"
    current = model_weights(nxt.get("current") or {})
    if not synced:
        target, reason = current, "initial_sync"
    elif max_deviation(acct_w, current) > band:
        target, reason = current, "drift_repair"
    else:
        return None, "hold"
    gross = sum(target.values())
    if gross > MAX_GROSS:
        target = {s: w * MAX_GROSS / gross for s, w in target.items()}
        reason += "_capped"
    return target, reason


@dataclass
class Order:
    symbol: str
    side: str                       # "buy" | "sell"
    qty: Optional[float] = None
    notional: Optional[float] = None


def _floor(x: float, places: int) -> float:
    q = 10 ** places
    return math.floor(x * q + 1e-9) / q


def plan_orders(target: dict, equity: float, positions: dict, prices: dict, fractionable: dict,
                min_usd: float = MIN_ORDER_USD) -> list:
    """Sells, then buys, that move ``positions`` ({sym: qty}) to ``target`` x ``equity``.

    A symbol leaving the book is sold in full whatever its size. Every other
    leg under ``min_usd`` is skipped. Buys of fractionable ETFs go by notional,
    everything else by whole or 6-decimal quantity.
    """
    sells, buys = [], []
    for s in sorted(set(target) | {k for k, q in positions.items() if q}):
        qty = float(positions.get(s, 0.0) or 0.0)
        tw = float(target.get(s, 0.0))
        if tw <= 0 and qty > 0:
            sells.append(Order(s, "sell", qty=qty))
            continue
        price = float(prices[s])
        delta = tw * equity - qty * price
        if abs(delta) < min_usd:
            continue
        frac = bool(fractionable.get(s, False))
        if delta < 0:
            q = min(qty, _floor(-delta / price, 6) if frac else math.floor(-delta / price))
            if q > 0:
                sells.append(Order(s, "sell", qty=q))
        elif frac:
            buys.append(Order(s, "buy", notional=_floor(delta, 2)))
        else:
            q = math.floor(delta / price)
            if q >= 1:
                buys.append(Order(s, "buy", qty=float(q)))
    return sells + buys


def in_act_window(is_open: bool, now: dt.datetime, next_close: dt.datetime) -> bool:
    if not is_open:
        return False
    minutes = (next_close - now).total_seconds() / 60.0
    return LATEST_ACT_MINUTES <= minutes <= ACT_MINUTES


# ---------------------------------------------------------------- the runner

def _utc_iso(t: Optional[dt.datetime] = None) -> str:
    return (t or dt.datetime.now(dt.timezone.utc)).astimezone(dt.timezone.utc).isoformat(timespec="seconds")


def _log(msg: str) -> None:
    print(f"[f14-mirror] {_utc_iso()} {msg}", flush=True)


def _order_fields(o) -> dict:
    out = {"status": _status(o)}
    for k in ("filled_qty", "filled_avg_price"):
        v = getattr(o, k, None)
        out[k] = float(v) if v not in (None, "") else None
    return out


class Mirror:
    """One mirror over one paper account. ``client`` is an alpaca-py TradingClient,
    ``prices_fn(symbols) -> {sym: price}`` the latest trades, ``next_fn(timeout=)``
    the frozen rule; all are injected for tests."""

    def __init__(self, *, store, client, prices_fn: Callable, brokerage_id: str, frozen_dir: str,
                 data_dir: str, account_number: str = DEFAULT_ACCOUNT_NUMBER, dry_run: bool = False,
                 next_fn: Optional[Callable] = None,
                 now_fn: Callable = lambda: dt.datetime.now(dt.timezone.utc), sleep_fn: Callable = time.sleep):
        self.store = store
        self.client = client
        self.prices_fn = prices_fn
        self.brokerage_id = brokerage_id
        self.frozen_dir = frozen_dir
        self.data_dir = data_dir
        self.account_number = account_number
        self.dry_run = dry_run
        self.next_fn = next_fn or functools.partial(frozen_next, frozen_dir, os.path.join(data_dir, "cache"))
        self.now = now_fn
        self.sleep = sleep_fn
        self._last_fetch_attempt = None
        self._fresh = None              # (session, frame last close, next info) of the last good fetch
        # A dry run spends its own session key, so turning it off trades the same day.
        self._session_key = "last_dry_session" if dry_run else "last_act_session"
        self._act_kind = "dry" if dry_run else "act"     # ...and its own row, never the live act row

    # -- ledger ----------------------------------------------------------
    def state(self) -> dict:
        return self.store.get(TABLE, STATE_ID) or {"id": STATE_ID, "mirror": MIRROR}

    def save_state(self, **patch) -> None:
        doc = {**self.state(), **patch, "updated_at": _utc_iso(self.now())}
        self.store.insert(TABLE, doc, conflict="replace")

    def record(self, row_id: str, kind: str, **fields) -> dict:
        doc = {"id": row_id, "mirror": MIRROR, "kind": kind, "at": _utc_iso(self.now()),
               "dry_run": self.dry_run, **fields}
        # Log first: if the insert fails, stdout still carries what was sent.
        _log(f"{kind} {row_id}: " + json.dumps(
            {k: v for k, v in fields.items() if k in ("reason", "action", "error", "orders")}, default=str)[:1500])
        self.store.insert(TABLE, doc, conflict="replace")
        return doc

    def note_error(self, session: dt.date, err: str) -> None:
        """Leave a persistent failure in the ledger, not only on stdout. Before the
        session acts, the error sits in its pending ``act`` row; after, in an ``error`` row."""
        acted = self.state().get(self._session_key) == session.isoformat()
        kind = "error" if acted else self._act_kind
        row_id = f"{MIRROR}|{session.isoformat()}|{kind}"
        prev = self.store.get(TABLE, row_id) or {}
        attempts = int(prev.get("attempts") or 0) + 1
        try:
            self.record(row_id, kind, session=session.isoformat(), status="pending",
                        error=str(err)[:1000], attempts=attempts)
        except Exception as e:      # the ledger itself is down: stdout is all there is
            _log(f"could not record error for {session}: {e}")

    # -- safety ------------------------------------------------------------
    def check_account(self):
        acct = self.client.get_account()
        number = str(getattr(acct, "account_number", "") or "")
        if not number.startswith("PA"):
            raise MirrorRefused("account is not an Alpaca paper account")
        if number != self.account_number:
            raise MirrorRefused(f"account {number} is not the pinned mirror account")
        if getattr(acct, "trading_blocked", False) or getattr(acct, "account_blocked", False):
            raise MirrorRefused("account is blocked")
        bound = [i.get("id") for i in self.store.run(self.store.filter("Instances", {"brokerage_id": self.brokerage_id}))
                 if i.get("runCommand")]
        if bound:
            raise MirrorRefused(f"running instance(s) bound to this brokerage: {bound}")
        from alpaca.trading.enums import QueryOrderStatus
        from alpaca.trading.requests import GetOrdersRequest
        foreign = [o.symbol for o in self.client.get_orders(GetOrdersRequest(status=QueryOrderStatus.OPEN))
                   if not str(getattr(o, "client_order_id", "") or "").startswith(ORDER_PREFIX)]
        if foreign:
            raise MirrorRefused(f"open orders the mirror did not place: {foreign}")
        return acct

    def boot(self) -> None:
        problems = verify_frozen(self.frozen_dir)
        if problems:
            raise MirrorRefused("frozen dir: " + "; ".join(problems[:5]))
        ok, tail = run_repro(self.frozen_dir)
        if not ok:
            raise MirrorRefused("repro_check did not PASS in this image: " + tail)
        acct = self.check_account()
        assets = {}
        for s in sorted(UNIVERSE):
            a = self.client.get_asset(s)
            assets[s] = {k: bool(getattr(a, k, False)) for k in ("tradable", "fractionable", "marginable")}
        self.record(f"{MIRROR}|boot|{_utc_iso(self.now())}", "boot", ok=True, repro=tail,
                    equity=float(acct.equity), multiplier=float(getattr(acct, "multiplier", 1) or 1),
                    assets=assets)

    # -- calendar ----------------------------------------------------------
    def sessions(self, around: dt.date) -> list:
        from alpaca.trading.requests import GetCalendarRequest
        cal = self.client.get_calendar(GetCalendarRequest(start=around - dt.timedelta(days=14), end=around))
        return sorted(_as_date(c.date) for c in cal)

    def open_before(self, deadline: Optional[dt.datetime]) -> bool:
        """Is the market open, with the deadline still ahead, on Alpaca's clock?"""
        if deadline is None:
            return True
        clock = self.client.get_clock()
        return bool(clock.is_open) and clock.timestamp < deadline

    # -- one loop iteration -------------------------------------------------
    def step(self) -> None:
        now = self.now()
        today = now.astimezone(ET).date()
        clock = self.client.get_clock()
        st = self.state()
        if in_act_window(clock.is_open, clock.timestamp, clock.next_close):
            if st.get(self._session_key) != today.isoformat():
                deadline = clock.next_close - dt.timedelta(minutes=LATEST_ACT_MINUTES)
                try:
                    self.act(today, deadline)
                except MirrorRefused:
                    raise
                except Exception as e:
                    self.note_error(today, f"{type(e).__name__}: {e}")
                    raise
            return
        if (not clock.is_open and now.astimezone(ET).time() >= SNAPSHOT_AFTER_ET
                and st.get("last_close_session") != today.isoformat()):
            sessions = self.sessions(today)
            if sessions and sessions[-1] == today:
                try:
                    self.snapshot(today)
                except MirrorRefused:
                    raise
                except Exception as e:
                    self.record(f"{MIRROR}|{today.isoformat()}|close", "close", session=today.isoformat(),
                                status="pending", error=f"{type(e).__name__}: {str(e)[:900]}")
                    raise

    def _fetch(self, session: dt.date, want_last: dt.date, deadline: Optional[dt.datetime]):
        """(frame last close, next info) once the frame ends at ``want_last``, else None.

        A good fetch is reused for the rest of the session. A stale one is
        retried at most every FETCH_RETRY_SECONDS (each attempt re-downloads the
        whole history), and never with less than MIN_FETCH_SECONDS before the
        deadline, which also bounds the subprocess."""
        if self._fresh and self._fresh[0] == session and self._fresh[1] == want_last:
            return self._fresh[1], self._fresh[2]
        now = self.now()
        if self._last_fetch_attempt and (now - self._last_fetch_attempt).total_seconds() < FETCH_RETRY_SECONDS:
            return None
        timeout = FETCH_TIMEOUT_SECONDS
        if deadline is not None:
            timeout = min(timeout, (deadline - now).total_seconds())
            if timeout < MIN_FETCH_SECONDS:
                return None
        self._last_fetch_attempt = now
        last_date, nxt = self.next_fn(timeout=timeout)
        if last_date != want_last:
            raise _Stale(f"frame ends {last_date}, want {want_last}")
        self._fresh = (session, last_date, nxt)
        return last_date, nxt

    def _account_book(self):
        acct = self.check_account()
        equity = float(acct.equity)
        positions = self.client.get_all_positions()
        qty = {p.symbol: float(p.qty) for p in positions}
        mv = {p.symbol: float(p.market_value) for p in positions}
        px = {p.symbol: float(p.current_price) for p in positions if getattr(p, "current_price", None)}
        foreign = sorted(s for s in qty if s not in UNIVERSE)
        if foreign:
            raise MirrorRefused(f"account holds non-mirror positions {foreign}")
        return acct, equity, qty, mv, px

    def act(self, session: dt.date, deadline: Optional[dt.datetime] = None) -> None:
        prior = [d for d in self.sessions(session) if d < session]
        if not prior:
            raise RuntimeError(f"no session before {session} in Alpaca's calendar")
        try:
            got = self._fetch(session, prior[-1], deadline)
        except _Stale as e:
            self.note_error(session, f"stale frame: {e}; retrying")
            return
        if got is None:
            return
        last_date, nxt = got
        acct, equity, qty, mv, px = self._account_book()
        acct_w = account_weights(equity, mv)
        target, reason = choose_target(nxt, acct_w, bool(self.state().get("synced")))
        base = dict(session=session.isoformat(), frame_last_close=last_date.isoformat(), action=nxt.get("action"),
                    reason=reason, equity=equity, account_weights=acct_w,
                    model_current=model_weights(nxt.get("current") or {}),
                    model_target=model_weights(nxt["target"]) if nxt.get("target") else None)
        row_id = f"{MIRROR}|{session.isoformat()}|{self._act_kind}"
        if target is None:
            self.save_state(**{self._session_key: session.isoformat()})
            self.record(row_id, "act", status="done", orders=[], **base)
            return
        gross = sum(target.values())
        multiplier = float(getattr(acct, "multiplier", 1) or 1)
        if gross > 1.0 + 1e-6 and multiplier < 2.0:
            self.save_state(**{self._session_key: session.isoformat()})
            self.record(row_id, "act", status="done", orders=[],
                        error=f"margin unavailable (multiplier {multiplier}) for gross {gross:.3f}", **base)
            return
        syms = sorted(set(target) | set(qty))
        prices = {s: px[s] for s in syms if s in px}
        missing = [s for s in syms if s not in prices]
        if missing:
            prices.update(self.prices_fn(missing))
        frac = {s: bool(getattr(self.client.get_asset(s), "fractionable", False)) for s in syms}
        orders = plan_orders(target, equity, qty, prices, frac)
        if not self.open_before(deadline):
            self.note_error(session, "deadline passed before the first order")
            return
        # At-most-once: the session is spent before the first order leaves.
        self.save_state(**{self._session_key: session.isoformat()})
        sent = []
        try:
            if self.dry_run:
                sent.extend(dict(asdict(o), status="dry_run") for o in orders)
            else:
                self.submit(orders, session, deadline, sent)
        finally:
            # Synced is sticky: once a rebalance got any leg through, the drift band
            # (not a full re-sync) repairs a leg that failed, so one leg that keeps
            # failing cannot turn the mirror into a daily full-book trader.
            went = any(o.get("status") not in ("rejected", "error", "skipped_deadline") for o in sent)
            if not self.dry_run and (went or not orders):
                self.save_state(synced=True)
            self.record(row_id, "act", status="done", orders=sent, target=target, prices=prices, **base)

    def submit(self, orders: list, session: dt.date, deadline: Optional[dt.datetime], out: list) -> list:
        """Send the sells, wait for them, then the buys, appending each order's record to
        ``out`` as it goes (so a failure part-way still leaves the ledger complete)."""
        from alpaca.trading.enums import OrderSide, TimeInForce
        from alpaca.trading.requests import MarketOrderRequest
        sells = [o for o in orders if o.side == "sell"]
        buys = [o for o in orders if o.side == "buy"]
        late = False
        for group in (sells, buys):
            ids = []
            for n, o in enumerate(group):
                # The clock is re-read before every order: a slow API must not
                # carry a leg past the deadline and into the next open.
                if late or not self.open_before(deadline):
                    late = True
                    out.append(dict(asdict(o), status="skipped_deadline"))
                    continue
                cid = f"{ORDER_PREFIX}{session.isoformat()}-{o.side}-{o.symbol}-{n}"
                kw = dict(symbol=o.symbol, side=OrderSide.BUY if o.side == "buy" else OrderSide.SELL,
                          time_in_force=TimeInForce.DAY, client_order_id=cid)
                kw["notional" if o.notional is not None else "qty"] = o.notional if o.notional is not None else o.qty
                rec = dict(asdict(o), client_order_id=cid)
                out.append(rec)
                try:
                    res = self.client.submit_order(MarketOrderRequest(**kw))
                except Exception as e:      # one refused leg must not strand the rest
                    res = self._by_client_id(cid)
                    if res is None:
                        rec.update(status="error", error=str(e)[:300])
                        continue
                rec.update(id=str(res.id), **_order_fields(res))
                ids.append((rec, str(res.id)))
            if group is sells and ids:
                self._wait_filled(ids)
        return out

    def _by_client_id(self, cid: str):
        """The order behind a submit that raised, if Alpaca accepted it anyway (a timeout)."""
        try:
            return self.client.get_order_by_client_id(cid)
        except Exception:
            return None

    def _wait_filled(self, ids: list) -> None:
        pending = list(ids)
        for _ in range(SELL_FILL_WAIT_SECONDS // 3):
            if not pending:
                break
            self.sleep(3)
            still = []
            for rec, oid in pending:
                try:
                    rec.update(_order_fields(self.client.get_order_by_id(oid)))
                except Exception as e:      # a failed poll must not cost the buys
                    _log(f"poll {oid} failed: {e}")
                if rec.get("status") not in TERMINAL:
                    still.append((rec, oid))
            pending = still

    def refresh_fills(self, session: dt.date) -> Optional[bool]:
        """Record the session's final order states on its act row. True if every leg
        filled, False if any did not, None when the session sent nothing."""
        row_id = f"{MIRROR}|{session.isoformat()}|act"
        row = self.store.get(TABLE, row_id)
        orders = (row or {}).get("orders") or []
        if not orders or row.get("dry_run"):
            return None
        for o in orders:
            if o.get("id"):
                try:
                    o.update(_order_fields(self.client.get_order_by_id(o["id"])))
                except Exception as e:
                    _log(f"fill refresh {o['id']} failed: {e}")
        self.store.insert(TABLE, {**row, "orders": orders, "fills_at": _utc_iso(self.now())}, conflict="replace")
        return all(o.get("status") == "filled" for o in orders)

    def snapshot(self, session: dt.date) -> None:
        try:
            got = self._fetch(session, session, None)
        except _Stale as e:
            _log(f"close snapshot: {e}; retrying")
            return
        if got is None:
            return
        last_date, nxt = got
        all_filled = self.refresh_fills(session)
        acct, equity, qty, mv, px = self._account_book()
        acct_w = account_weights(equity, mv)
        model = model_weights(nxt.get("current") or {})
        st = self.state()
        self.save_state(last_close_session=session.isoformat())
        self.record(f"{MIRROR}|{session.isoformat()}|close", "close", session=session.isoformat(), equity=equity,
                    account_weights=acct_w, model_current=model, deviation=max_deviation(acct_w, model),
                    acted=st.get(self._session_key) == session.isoformat(), all_filled=all_filled,
                    next_action=nxt.get("action"),
                    next_target=model_weights(nxt["target"]) if nxt.get("target") else None)


class _Stale(Exception):
    """The frame does not yet end at the close the step needs."""


def _status(order) -> str:
    s = getattr(order, "status", "")
    return str(getattr(s, "value", s) or "").lower()


def _as_date(x) -> dt.date:
    if isinstance(x, dt.datetime):
        return x.date()
    if isinstance(x, dt.date):
        return x
    return dt.date.fromisoformat(str(x)[:10])


# ---------------------------------------------------------------- wiring

def _truthy(v) -> bool:
    return str(v or "").strip().lower() in {"1", "true", "yes", "on"}


def _with_timeout(client, seconds: int = 30):
    """alpaca-py's requests.Session has no default timeout (see broker_adapters/alpaca.py)."""
    sess = getattr(client, "_session", None)
    if sess is not None and not getattr(sess, "_f14_timeout", False):
        orig = sess.request

        @functools.wraps(orig)
        def request(method, url, **kw):
            kw.setdefault("timeout", seconds)
            return orig(method, url, **kw)

        sess.request = request
        sess._f14_timeout = True
    return client


def from_env() -> Mirror:
    from db import store
    from stock_credential_boundary import resolve_alpaca_brokerage_credentials
    from alpaca.data.enums import DataFeed
    from alpaca.data.historical import StockHistoricalDataClient
    from alpaca.data.requests import StockLatestTradeRequest
    from alpaca.trading.client import TradingClient

    bid = os.environ.get("F14_MIRROR_BROKERAGE_ID") or DEFAULT_BROKERAGE_ID
    creds = resolve_alpaca_brokerage_credentials(store.get("BrokerageAccounts", bid), expected_brokerage_id=bid)
    if creds.paper is not True:
        raise MirrorRefused("brokerage row is not paper")
    client = _with_timeout(TradingClient(api_key=creds.key, secret_key=creds.secret, paper=True))
    data = _with_timeout(StockHistoricalDataClient(api_key=creds.key, secret_key=creds.secret))
    feed = DataFeed.SIP if creds.data_feed == "sip" else DataFeed.IEX

    def prices(symbols):
        res = data.get_stock_latest_trade(StockLatestTradeRequest(symbol_or_symbols=list(symbols), feed=feed))
        missing = [s for s in symbols if s not in res]
        if missing:
            raise RuntimeError(f"no latest trade for {missing} on the {creds.data_feed} feed")
        return {s: float(res[s].price) for s in symbols}

    return Mirror(store=store, client=client, prices_fn=prices, brokerage_id=bid,
                  frozen_dir=os.environ.get("F14_MIRROR_FROZEN_DIR") or "/frozen",
                  data_dir=os.environ.get("F14_MIRROR_DATA_DIR") or "/data",
                  account_number=os.environ.get("F14_MIRROR_ACCOUNT_NUMBER") or DEFAULT_ACCOUNT_NUMBER,
                  dry_run=_truthy(os.environ.get("F14_MIRROR_DRY_RUN")))


def _boot_row(ok: bool, error: str) -> None:
    try:
        from db import store
        store.insert(TABLE, {"id": f"{MIRROR}|boot|{_utc_iso()}", "mirror": MIRROR, "kind": "boot",
                             "ok": ok, "error": error[:1000], "at": _utc_iso()}, conflict="replace")
    except Exception as e:
        _log(f"could not record boot failure: {e}")


def main() -> None:
    if not _truthy(os.environ.get("F14_MIRROR_ENABLED", "1")):
        _log("disabled (F14_MIRROR_ENABLED); idling")
        while True:
            time.sleep(3600)
    from db import schema
    schema.ensure_table(TABLE)
    while True:
        try:
            m = from_env()
            m.boot()
            break
        except MirrorRefused as e:
            _log(f"REFUSED at boot: {e}")
            _boot_row(False, f"refused: {e}")
            time.sleep(3600)
        except Exception as e:
            _log(f"boot failed: {type(e).__name__}: {str(e)[:300]}")
            _boot_row(False, f"{type(e).__name__}: {e}")
            time.sleep(300)
    _log(f"armed (dry_run={m.dry_run})")
    while True:
        try:
            m.step()
        except MirrorRefused as e:
            _log(f"REFUSED: {e}")
            try:
                m.record(f"{MIRROR}|refused|{_utc_iso()}", "refused", error=str(e)[:1000])
            except Exception as e2:
                _log(f"could not record refusal: {e2}")
            time.sleep(3600)
        except Exception as e:
            _log(f"step failed: {type(e).__name__}: {str(e)[:300]}")
        time.sleep(LOOP_SECONDS)


if __name__ == "__main__":
    main()
