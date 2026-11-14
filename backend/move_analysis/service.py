"""Settings, storage and payloads for the why-it-moved analysis.

Every function takes the store as its first argument (``db.store`` in the
API, a FakeStore in tests) and accepts injected price, sector, news and model
callers, so nothing here needs the network to be tested.
"""
from __future__ import annotations

import re
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from typing import Callable, Optional

from . import decompose as dc
from . import explain as ex
from . import news as nw
from . import prices as px
from . import sectors as sc

EVENTS_TABLE = "MoveEvents"
SETTINGS_TABLE = "AppSettings"
SETTINGS_ID = "move_analysis"
MODELS_TABLE = "Models"

RANGES = {"1M": 31, "3M": 92, "6M": 183, "1Y": 366}
SIGMA_BOUNDS = (1.0, 6.0)
MIN_MOVE_BOUNDS = (0.5, 25.0)
PENDING_STALE = timedelta(minutes=10)
MAX_QUEUE = 40

_SYMBOL_RE = re.compile(r"^[A-Z][A-Z0-9]{0,5}(-[A-Z]{1,2})?$")


class NotFound(Exception):
    """Unknown symbol, or one with no price history."""


class Unavailable(Exception):
    """The price provider failed; nothing is wrong with the request."""


def _now(now: Optional[datetime] = None) -> datetime:
    return now or datetime.now(timezone.utc)


def _iso(stamp: datetime) -> str:
    return stamp.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def normalize_symbol(raw) -> str:
    symbol = str(raw or "").strip().upper().replace(".", "-")
    if not _SYMBOL_RE.match(symbol):
        raise ValueError("Enter a stock or ETF symbol, for example NVDA.")
    return symbol


def normalize_range(raw) -> str:
    key = str(raw or "3M").strip().upper()
    if key not in RANGES:
        raise ValueError("Invalid range: %s. Must be one of: %s" % (key, ", ".join(RANGES)))
    return key


# ── Settings ─────────────────────────────────────────────────────────────────


def _models(store) -> list:
    try:
        rows = list(store.run(MODELS_TABLE))
    except Exception:
        rows = []
    out = [{"id": str(row.get("id")), "name": str(row.get("name") or row.get("model") or ""),
            "provider": str(row.get("provider") or ""), "model": str(row.get("model") or "")}
           for row in rows if isinstance(row, dict) and row.get("id") is not None]
    out.sort(key=lambda m: m["name"].lower())
    return out


def _stored_settings(store) -> dict:
    try:
        row = store.get(SETTINGS_TABLE, SETTINGS_ID) or {}
    except Exception:
        row = {}
    return row if isinstance(row, dict) else {}


def _bounded(value, bounds: tuple, default: float) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    if number != number:
        return default
    return min(max(number, bounds[0]), bounds[1])


def get_settings(store) -> dict:
    row = _stored_settings(store)
    models = _models(store)
    by_id = {m["id"]: m for m in models}
    model_id = row.get("model_id")
    model_id = str(model_id) if model_id is not None and str(model_id) in by_id else None
    return {
        "model_id": model_id,
        "model_name": by_id[model_id]["name"] if model_id else None,
        "sigma": _bounded(row.get("sigma"), SIGMA_BOUNDS, dc.DEFAULT_SIGMA),
        "min_move_pct": _bounded(row.get("min_move_pct"), MIN_MOVE_BOUNDS,
                                 dc.DEFAULT_MIN_MOVE_PCT),
        "models": models,
    }


def put_settings(store, patch: dict) -> dict:
    """``patch`` holds only the fields the caller sent."""
    current = _stored_settings(store)
    doc = {"id": SETTINGS_ID, "model_id": current.get("model_id"),
           "sigma": current.get("sigma"), "min_move_pct": current.get("min_move_pct")}
    if "model_id" in patch:
        model_id = patch["model_id"]
        if model_id in (None, ""):
            doc["model_id"] = None
        else:
            if str(model_id) not in {m["id"] for m in _models(store)}:
                raise ValueError("That model no longer exists. Pick another.")
            doc["model_id"] = str(model_id)
    for field, bounds in (("sigma", SIGMA_BOUNDS), ("min_move_pct", MIN_MOVE_BOUNDS)):
        if field in patch and patch[field] is not None:
            try:
                number = float(patch[field])
            except (TypeError, ValueError):
                raise ValueError(f"{field} must be a number")
            if number != number or not (bounds[0] <= number <= bounds[1]):
                raise ValueError(f"{field} must be between {bounds[0]:g} and {bounds[1]:g}")
            doc[field] = number
    doc["updated_at"] = _iso(_now())
    store.insert(SETTINGS_TABLE, doc, conflict="replace")
    return get_settings(store)


# ── The split ────────────────────────────────────────────────────────────────


def compute(symbol: str, range_key: str, settings: dict, *,
            price_provider: Optional[Callable] = None,
            sector_lookup: Optional[Callable] = None,
            now: Optional[datetime] = None) -> dict:
    """Bars for the range (oldest first) with the split, plus sector and beta."""
    sector, etf = sc.sector_for(symbol, lookup=sector_lookup)
    try:
        aligned = px.load_aligned(symbol, etf, days=RANGES[range_key],
                                  lookback=dc.LOOKBACK, provider=price_provider, now=now)
    except px.NoPriceHistory:
        raise NotFound(f"No price history for {symbol}.")
    except Exception as exc:
        raise Unavailable(f"Prices for {symbol} could not be loaded. Try again shortly.") from exc
    bars = dc.decompose(
        aligned.dates, aligned.closes, aligned.basis, aligned.market,
        aligned.sector, aligned.volumes,
        sigma=settings["sigma"], min_move_pct=settings["min_move_pct"])
    local_now = (now or datetime.now(tz=px.NY)).astimezone(px.NY)
    cutoff = (local_now.date() - timedelta(days=RANGES[range_key])).isoformat()
    first = next((i for i, bar in enumerate(bars) if bar.date >= cutoff), len(bars))
    in_range = bars[first:]
    if not in_range:
        raise NotFound(f"No price history for {symbol}.")
    prev_date = {bars[i].date: bars[i - 1].date for i in range(1, len(bars))}
    betas = [bar.beta for bar in in_range if bar.beta is not None]
    return {"symbol": symbol, "sector": sector, "sector_etf": etf,
            "beta": round(betas[-1], 3) if betas else None,
            "bars": in_range, "prev_date": prev_date}


# ── Events ───────────────────────────────────────────────────────────────────


def event_id(symbol: str, day: str) -> str:
    return f"{symbol}|{day}"


def _blank_event(symbol: str, bar, etf: Optional[str]) -> dict:
    return {
        "id": event_id(symbol, bar.date), "symbol": symbol, "date": bar.date,
        "close": bar.close,
        "ret_pct": dc._r(bar.ret_pct), "market_pct": dc._r(bar.market_pct),
        "sector_pct": dc._r(bar.sector_pct), "specific_pct": dc._r(bar.specific_pct),
        "sector_etf": etf,
        "status": "unexplained", "category": None, "category_label": None,
        "headline": None, "explanation": None, "confidence": None, "sources": [],
        "model_name": None, "explained_at": None, "error": None,
    }


def _parse(stamp) -> Optional[datetime]:
    return nw._parse_stamp(stamp)


def _is_stale_pending(row: dict, now: datetime) -> bool:
    since = _parse(row.get("pending_since"))
    return since is None or now - since > PENDING_STALE


def _apply_explanation(event: dict, fields: dict) -> None:
    event["status"] = "explained"
    event["category"] = fields["category"]
    event["category_label"] = ex.CATEGORIES.get(fields["category"])
    event["headline"] = fields["headline"]
    event["explanation"] = fields["explanation"]
    event["confidence"] = fields["confidence"]
    event["sources"] = list(fields.get("sources") or [])


def build_event(store, symbol: str, bar, etf: Optional[str], *,
                now: Optional[datetime] = None) -> dict:
    """The event for one flagged bar: fresh numbers, stored explanation."""
    event = _blank_event(symbol, bar, etf)
    try:
        row = store.get(EVENTS_TABLE, event["id"])
    except Exception:
        row = None
    status = (row or {}).get("status")
    if status == "explained":
        _apply_explanation(event, row)
        event["model_name"] = row.get("model_name")
        event["explained_at"] = row.get("explained_at")
    elif status == "pending" and not _is_stale_pending(row, _now(now)):
        event["status"] = "pending"
    elif status in ("failed", "pending"):
        event["status"] = "failed"
        event["error"] = (row.get("error") if status == "failed"
                          else "The explanation did not finish. Try again.")
    elif bar.market_driven:
        _apply_explanation(event, ex.market_explanation(event))
    return event


def moves_payload(store, symbol_raw, range_raw, *, now: Optional[datetime] = None,
                  **inject) -> dict:
    symbol = normalize_symbol(symbol_raw)
    range_key = normalize_range(range_raw)
    settings = get_settings(store)
    computed = compute(symbol, range_key, settings, now=now, **inject)
    events = [build_event(store, symbol, bar, computed["sector_etf"], now=now)
              for bar in computed["bars"] if bar.flagged]
    events.sort(key=lambda e: e["date"], reverse=True)
    return {
        "symbol": symbol, "range": range_key,
        "sector": computed["sector"], "sector_etf": computed["sector_etf"],
        "beta": computed["beta"],
        "model_configured": settings["model_id"] is not None,
        "pending": sum(1 for e in events if e["status"] == "pending"),
        "bars": [bar.as_dict() for bar in computed["bars"]],
        "events": events,
        "categories": ex.category_list(),
    }


# ── Explaining ───────────────────────────────────────────────────────────────


def _spawn(target: Callable) -> None:
    threading.Thread(target=target, name="move-analysis-explain", daemon=True).start()


def queue_explain(store, symbol_raw, range_raw, dates=None, force: bool = False, *,
                  brokerage_id: Optional[str] = None,
                  now: Optional[datetime] = None,
                  spawn: Callable = _spawn,
                  articles_fn: Optional[Callable] = None,
                  llm_factory: Optional[Callable] = None,
                  **inject) -> dict:
    """Mark the chosen events pending and explain them in the background."""
    symbol = normalize_symbol(symbol_raw)
    range_key = normalize_range(range_raw)
    settings = get_settings(store)
    if settings["model_id"] is None:
        raise ValueError("Choose a model for AI Analysis in Settings first.")
    model_doc = store.get(MODELS_TABLE, settings["model_id"])
    if not model_doc:
        raise ValueError("The chosen model no longer exists. Pick another in Settings.")

    wanted = None
    if dates is not None:
        wanted = {str(d).strip() for d in dates if str(d).strip()}
        if not wanted:
            return {"queued": 0}
    computed = compute(symbol, range_key, settings, now=now, **inject)
    stamp = _now(now)
    jobs = []
    for bar in reversed(computed["bars"]):
        if not bar.flagged or (wanted is not None and bar.date not in wanted):
            continue
        row = store.get(EVENTS_TABLE, event_id(symbol, bar.date)) or {}
        status = row.get("status")
        if status == "pending" and not _is_stale_pending(row, stamp):
            continue
        if not force and (status == "explained" or (bar.market_driven and not status)):
            continue
        event = _blank_event(symbol, bar, computed["sector_etf"])
        jobs.append({"event": event, "prev_date": computed["prev_date"].get(bar.date)})
        if len(jobs) >= MAX_QUEUE:
            break
    for job in jobs:
        store.insert(EVENTS_TABLE, {
            "id": job["event"]["id"], "symbol": symbol, "date": job["event"]["date"],
            "status": "pending", "pending_since": _iso(stamp),
        }, conflict="replace")
    if jobs:
        credentials = None
        if articles_fn is None:
            credentials = nw.alpaca_credentials(store, brokerage_id)
        spawn(lambda: run_jobs(store, jobs, model_doc, credentials=credentials,
                               articles_fn=articles_fn, llm_factory=llm_factory))
    return {"queued": len(jobs)}


def run_jobs(store, jobs: list, model_doc: dict, *, credentials=None,
             articles_fn: Optional[Callable] = None,
             llm_factory: Optional[Callable] = None) -> None:
    """Explain each queued event and store the outcome. Never raises: every
    job ends as an explained or a failed row."""
    model_name = str(model_doc.get("name") or model_doc.get("model") or "")
    llm_call = None
    setup_error = None
    try:
        llm_call = (llm_factory or ex.make_llm_call)(model_doc)
    except Exception as exc:
        setup_error = str(exc)[:200] or type(exc).__name__

    for job in jobs:
        event = job["event"]
        base = {"id": event["id"], "symbol": event["symbol"], "date": event["date"],
                "model_id": str(model_doc.get("id")), "model_name": model_name,
                "snapshot": {k: event.get(k) for k in
                             ("close", "ret_pct", "market_pct", "sector_pct",
                              "specific_pct", "sector_etf")}}
        try:
            if llm_call is None:
                raise ex.ExplainError(setup_error or "the model could not be set up")
            if articles_fn is not None:
                articles = articles_fn(event["symbol"], job.get("prev_date"), event["date"])
            else:
                articles = nw.articles_for(event["symbol"], job.get("prev_date"),
                                           event["date"], credentials=credentials)
            fields = ex.explain_event(event, articles, llm_call)
            row = dict(base, status="explained", explained_at=_iso(_now()),
                       article_count=len(articles), error=None, **fields)
        except Exception as exc:
            row = dict(base, status="failed", failed_at=_iso(_now()),
                       error=(str(exc)[:200] or type(exc).__name__))
        try:
            store.insert(EVENTS_TABLE, row, conflict="replace")
        except Exception:
            pass


# ── Account view ─────────────────────────────────────────────────────────────


def portfolio_payload(store, positions: list, cash=None, *,
                      now: Optional[datetime] = None, **inject) -> dict:
    """The last finished session's move per equity holding, as contributions
    to the account. Weights use current market values, so the contributions
    are an approximation of the account's own return."""
    empty = {"as_of": None, "spy_pct": None, "holdings": [],
             "totals": {"contribution_pct": 0.0, "market_pct": 0.0,
                        "sector_pct": 0.0, "specific_pct": 0.0}}
    held = []
    for position in positions or []:
        try:
            symbol = normalize_symbol((position or {}).get("symbol"))
            value = float((position or {}).get("marketValue") or 0.0)
        except (TypeError, ValueError):
            continue
        if value:
            held.append((symbol, value))
    try:
        cash_value = float(cash) if cash is not None else 0.0
    except (TypeError, ValueError):
        cash_value = 0.0
    total = sum(value for _, value in held) + max(cash_value, 0.0)
    if not held or total <= 0:
        return empty

    settings = get_settings(store)

    def _one(symbol: str):
        try:
            return compute(symbol, "1M", settings, now=now, **inject)
        except Exception:
            return None

    symbols = [symbol for symbol, _ in held]
    with ThreadPoolExecutor(max_workers=min(4, len(symbols))) as pool:
        computed = dict(zip(symbols, pool.map(_one, symbols)))
    last_dates = [c["bars"][-1].date for c in computed.values() if c]
    if not last_dates:
        return empty
    as_of = max(last_dates)

    spy_pct = None
    try:
        spy = compute(px.MARKET_SYMBOL, "1M", settings, now=now, **inject)
        spy_bar = next((b for b in spy["bars"] if b.date == as_of), None)
        spy_pct = dc._r(spy_bar.ret_pct) if spy_bar else None
    except Exception:
        spy_pct = None

    holdings = []
    for symbol, value in held:
        result = computed.get(symbol)
        bar = next((b for b in result["bars"] if b.date == as_of), None) if result else None
        if bar is None or bar.ret_pct is None:
            continue
        weight = value / total
        market = (bar.market_pct or 0.0) * weight
        sector = (bar.sector_pct or 0.0) * weight
        contribution = bar.ret_pct * weight
        # Keep the parts summing to the contribution when the split is missing.
        specific = contribution - market - sector
        holdings.append({
            "symbol": symbol, "weight_pct": round(weight * 100, 2),
            "ret_pct": dc._r(bar.ret_pct),
            "contribution_pct": round(contribution, 4),
            "market_pct": round(market, 4), "sector_pct": round(sector, 4),
            "specific_pct": round(specific, 4), "flagged": bar.flagged,
            "event": (build_event(store, symbol, bar, result["sector_etf"], now=now)
                      if bar.flagged else None),
        })
    holdings.sort(key=lambda h: abs(h["contribution_pct"]), reverse=True)
    totals = {key: round(sum(h[key] for h in holdings), 4)
              for key in ("contribution_pct", "market_pct", "sector_pct", "specific_pct")}
    return {"as_of": as_of, "spy_pct": spy_pct, "holdings": holdings, "totals": totals}
