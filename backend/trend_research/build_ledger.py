"""Resumable builder of the TR research ledger (spec §7.2, plan Task 7).

One ledger row per week, built forward in time within a chain:

1. digest -- ``digest.weekly_digest`` at the week's ``as_of`` (no AI);
2. trend scan -- 1 call, given the trend list of the chain's previous ok
   row (none on a cold start, C10);
3. candidates -- 1 call, then a deterministic screen: the previous row's
   recommended names first (they are researched again regardless), then the
   model's candidates in rank order. A symbol is kept when it normalizes to a
   US ticker, is an allowed ETF or -- not being a known ETF -- has an
   operating-company filing before ``as_of`` (rulings R7/R7b; its filings are
   fetched first), and has at least ``MIN_SESSIONS`` daily bars before the
   session (ruling R16: missing bars are fetched into the SwingDailyBars
   cache first). At most ``MAX_CANDIDATES`` are researched;
4. research -- 1 call per kept candidate, on a dossier of its SEC filings and
   fundamentals (``filings.filing_dossier``; a ``fetch_failed`` is retried
   once, then the candidate is dropped), price-trend stats, its last 8 weeks
   of news (at most 40 items), last week's note on it and why it is a
   candidate;
5. book -- 1 call; the row is written.

Every dropped candidate is recorded with its reason.

Failure handling (spec §7.2):

* ``QuotaExhausted`` anywhere in a week: the week is discarded (nothing is
  written), the builder sleeps until ``reset_at`` + 60 s (30 min when the
  reset time is unknown) and builds the same week again -- at most
  ``MAX_QUOTA_WAITS_PER_WEEK`` times in a row; the next hit stops the build
  cleanly (summary ``stopped="quota_wait_cap"``), like the budget.
* ``BudgetExceeded``: the build stops; the in-progress week is not written.
* ``StageFailed`` twice on the scan, candidates or book stage: a
  ``status="failed"`` row records the error (the lane holds its book for
  that week). Twice on one research call: that candidate is dropped.

A week whose row is already ``ok`` is skipped without a call; a ``failed``
week is built again. Weeks whose ``as_of`` is not yet past are not built.

Recommended names are the book's picks with a positive weight that have a
research note this week (BIL, the cash remainder, never is); any other pick
is left out with a recorded reason (``dropped``, stage ``recommended``).

Chain continuity across a failed week. A week's prior state is the chain's
newest ``ok`` row before it, so after a failed week the next week builds on
the last ok week (two or more weeks back), and a chain whose only earlier
rows failed starts cold. The row's ``prior_row_id`` names the row it built
on; a gap shows as a ``prior_row_id`` older than the previous Monday. When a
failed week is rebuilt later, weeks already built after it keep the state
they were built on (ok rows are never rewritten).

CLI (run from ``backend/``)::

    python3 -m trend_research.build_ledger --chain honest-<model> \\
        --start 2025-08-04 --end 2026-11-02 --model-id <Models row id>
"""
from __future__ import annotations

import argparse
import logging
import time
from datetime import date, datetime, timedelta, timezone
from typing import Callable

from swing_trader import clock
from trend_research import book, digest, filings, ledger, llm, market_tape, prompts
from trend_research.ingest_alpaca_news import PAPER_INSTANCE, resolve_paper_credentials
from trend_research.llm import BudgetExceeded, QuotaExhausted, StageCaller, StageFailed
from trend_research.schemas import BookProposal, CandidateList, ResearchNote, TrendScan

__all__ = ["build", "build_week", "paper_bars_fetcher", "quota_delay", "MAX_CANDIDATES",
           "STAGE_ATTEMPTS", "main"]

log = logging.getLogger(__name__)

MAX_CANDIDATES = 30
#: Attempts per LLM stage before it counts as failed.
STAGE_ATTEMPTS = 2
#: Quota waits in a row for one week before the build stops.
MAX_QUOTA_WAITS_PER_WEEK = 12
#: Margin added after a known quota reset, and the wait when it is unknown.
QUOTA_RESET_MARGIN_SEC = 60
QUOTA_DEFAULT_WAIT_SEC = 30 * 60
#: Calendar days of filings indexed and excerpted before a decision.
FILINGS_LOOKBACK_DAYS = 400
NEWS_WEEKS = 8
NEWS_LIMIT = 40
ERROR_CHARS = 300


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _store(store):
    if store is not None:
        return store
    from db import store as default_store
    return default_store


def _dump(obj):
    return obj.model_dump() if hasattr(obj, "model_dump") else obj


def quota_delay(reset_at: datetime | None, now: datetime) -> float:
    """Seconds to sleep after a quota hit: until ``reset_at`` plus a margin,
    or ``QUOTA_DEFAULT_WAIT_SEC`` when the reset time is unknown."""
    if reset_at is None:
        return float(QUOTA_DEFAULT_WAIT_SEC)
    return max(0.0, (reset_at - now).total_seconds()) + QUOTA_RESET_MARGIN_SEC


def paper_bars_fetcher(instance_id: str = PAPER_INSTANCE, *, store=None) -> Callable:
    """``fetch(symbols, start, end) -> {symbol: [bar, ...]}`` over
    ``backtest_bars.fetch_daily_bars`` (through the SwingDailyBars cache),
    with the paper instance's Alpaca data credentials (never alpaca-main's)."""
    from swing_trader import backtest_bars
    key, secret = resolve_paper_credentials(instance_id, store=store)

    def fetch(symbols, start, end):
        return backtest_bars.fetch_daily_bars(symbols, start, end, key=key, secret=secret,
                                              feed="iex", store=store)
    return fetch


class _WeekFailed(Exception):
    def __init__(self, stage: str, error: str):
        super().__init__("%s: %s" % (stage, error))
        self.stage = stage
        self.error = error


class _Deps:
    """The week builder's outside world, injectable for tests."""

    def __init__(self, *, store, bars_fetcher, filings_fetcher, dossier_fn, bars_instance):
        self.store = store
        self._bars_fetcher = bars_fetcher
        self._bars_instance = bars_instance
        self.filings_fetcher = filings_fetcher or (
            lambda sym, start, end: filings.fetch_filings(sym, start, end, store=store))
        self.dossier_fn = dossier_fn or (
            lambda sym, as_of: filings.filing_dossier(sym, as_of, store=store))
        #: (symbol, as_of date) pairs whose filings this run already fetched.
        self.filings_fetched: set = set()

    @property
    def bars_fetcher(self) -> Callable:
        # Resolved on first use: a run whose weeks are all built never needs
        # the paper credentials.
        if self._bars_fetcher is None:
            self._bars_fetcher = paper_bars_fetcher(self._bars_instance, store=self.store)
        return self._bars_fetcher


def _scrub(text, secrets) -> str:
    out = str(text or "")
    for s in secrets:
        if s and len(s) >= 6:
            out = out.replace(s, "[redacted]")
    return out[:ERROR_CHARS]


def _stage(caller, stage: str, prompt: str, output_type, secrets=()):
    """``caller.call`` with ``STAGE_ATTEMPTS`` tries on ``StageFailed``;
    raise ``_WeekFailed`` (error text scrubbed of ``secrets``) after the
    last. Quota and budget errors pass."""
    last = ""
    for _ in range(STAGE_ATTEMPTS):
        try:
            return caller.call(prompt, output_type,
                               max_output_tokens=llm.MAX_OUTPUT_TOKENS[stage])
        except StageFailed as exc:
            last = _scrub(exc, secrets)
            log.warning("TR %s stage failed: %s", stage, last)
    raise _WeekFailed(stage, last)


# -- candidate screen ----------------------------------------------------------------

def _entries(prior: dict | None, cand_list: CandidateList) -> list:
    """Candidate entries in priority order -- last week's recommended names,
    then the model's list -- deduplicated by normalized symbol. Each entry:
    ``{raw, symbol, sources, trend_names, why}`` (``symbol`` None when the
    raw name is not a US ticker)."""
    out, by_sym = [], {}

    def add(raw, source, trend_names, why):
        sym = book.normalize_symbol(raw)
        if sym is not None and sym in by_sym:
            e = by_sym[sym]
            if source not in e["sources"]:
                e["sources"].append(source)
            if trend_names and not e["trend_names"]:
                e["trend_names"] = list(trend_names)
            if why and source == "candidates":
                e["why"] = why
            return
        e = {"raw": raw, "symbol": sym, "sources": [source],
             "trend_names": list(trend_names or []), "why": why or ""}
        out.append(e)
        if sym is not None:
            by_sym[sym] = e

    for r in (prior or {}).get("recommended") or ():
        raw = r.get("symbol") if isinstance(r, dict) else r
        thesis = r.get("thesis") if isinstance(r, dict) else ""
        add(raw, "prior_recommended", [], "Recommended last week. %s" % (thesis or ""))
    for c in cand_list.candidates:
        add(c.symbol, "candidates", c.trend_names, c.why)
    return out


def _drop(dropped: list, entry: dict, stage: str, reason: str) -> None:
    dropped.append({"symbol": entry.get("symbol"), "raw": entry.get("raw"),
                    "sources": list(entry.get("sources") or []), "stage": stage,
                    "reason": reason})


def _screen_universe(entries: list, as_of: datetime, deps: _Deps, dropped: list) -> list:
    """Entries allowed by the universe rules (§5.3, R7/R7b)."""
    kept = []
    for e in entries:
        sym = e["symbol"]
        if sym is None:
            _drop(dropped, e, "universe", "not a US-listed ticker")
            continue
        if sym in book.ALL_ALLOWED_ETFS:
            e["kind"] = "etf"
            kept.append(e)
            continue
        if book.is_etf_symbol(sym):
            _drop(dropped, e, "universe", "ETF outside the allowed universe")
            continue
        if not book.is_allowed_symbol(sym, is_etf=False):
            _drop(dropped, e, "universe", "not an allowed ticker")
            continue
        fetch_error = None
        key = (sym, as_of.date())
        if key not in deps.filings_fetched:
            try:
                deps.filings_fetcher(sym, as_of.date() - timedelta(days=FILINGS_LOOKBACK_DAYS),
                                     as_of.date())
            except Exception as exc:        # noqa: BLE001 -- recorded on the row
                fetch_error = "%s: %s" % (type(exc).__name__, str(exc)[:200])
                log.warning("TR filings fetch %s failed: %s", sym, fetch_error)
            else:
                deps.filings_fetched.add(key)
        if not filings.has_operating_filings(sym, as_of, store=deps.store):
            reason = ("no 10-K/10-Q/20-F/40-F/6-K by an operating company before the "
                      "decision (not a stock in the universe)")
            if fetch_error:
                reason += "; filings fetch failed: %s" % fetch_error
            _drop(dropped, e, "universe", reason)
            continue
        e["kind"] = "stock"
        kept.append(e)
    return kept


def _screen_bars(entries: list, as_of: datetime, deps: _Deps, dropped: list) -> list:
    """Entries with at least ``MIN_SESSIONS`` daily bars before the session;
    each kept entry gains ``trend_stats``."""
    if not entries:
        return []
    session = date.fromisoformat(clock.ny_date(as_of))
    fetched = deps.bars_fetcher([e["symbol"] for e in entries],
                                session - timedelta(days=market_tape.LOOKBACK_DAYS),
                                session - timedelta(days=1)) or {}
    fetched = {str(k).upper(): v for k, v in fetched.items()}

    def loader(symbols, _start, _end):
        return {s: fetched.get(str(s).upper()) or [] for s in symbols}

    kept = []
    for e in entries:
        stats = market_tape.symbol_trend_stats(e["symbol"], as_of, bars_loader=loader,
                                               store=deps.store)
        if stats is None:
            _drop(dropped, e, "bars", "fewer than %d sessions of daily bars before the "
                                      "decision" % market_tape.MIN_SESSIONS)
            continue
        e["trend_stats"] = stats
        kept.append(e)
    return kept


# -- one week ------------------------------------------------------------------------

def build_week(chain_id: str, monday: date, *, caller, provider: str, model: str,
               prompt_version: str, deps: _Deps, now: Callable[[], datetime],
               secrets=()) -> dict:
    """Build (and return, unwritten) the row for the week of ``monday``.

    Raises ``QuotaExhausted`` / ``BudgetExceeded`` from the caller unchanged:
    the week is then discarded. A scan, candidates or book stage that fails
    twice gives a ``status="failed"`` row."""
    as_of = ledger.week_as_of(monday)
    started = now()
    calls_before = caller.calls_used
    rid = ledger.week_id(chain_id, monday, model, prompt_version)
    prior = ledger.latest_row(chain_id, model, prompt_version, as_of, store=deps.store)
    row = {"id": rid, "chain_id": chain_id, "week_start": monday.isoformat(),
           "as_of": _iso(as_of), "available_at": _iso(as_of), "model": model,
           "provider": provider, "prompt_version": prompt_version,
           "prior_row_id": prior["id"] if prior else None}

    dig = digest.weekly_digest(as_of, store=deps.store, bars_loader=deps.bars_fetcher)
    row["digest_hash"] = digest.digest_hash(dig)
    provenance = {
        "headlines": len(dig.get("headlines") or ()),
        "themes": len(dig.get("themes") or ()),
        "theme_titles": sum(len(t.get("titles") or ()) for t in dig.get("themes") or ()),
        "macro_series": len(dig.get("macro") or ()),
        "calendar_events": len(dig.get("calendar") or ()),
        "tape_symbols": sum(1 for v in ((dig.get("tape") or {}).get("symbols") or {}).values()
                            if v),
    }
    row["provenance"] = provenance
    dropped: list = []
    kept: list = []
    notes: list = []
    scan = None
    try:
        prior_trends = ((prior or {}).get("scan") or {}).get("trends") or None
        scan = _stage(caller, "scan", prompts.trend_scan_prompt(dig, prior_trends, as_of),
                      TrendScan, secrets)
        cand_list = _stage(caller, "candidates",
                           prompts.candidate_prompt(scan, (prior or {}).get("recommended"),
                                                    as_of), CandidateList, secrets)
        provenance["candidates_named"] = len(cand_list.candidates)
        provenance["prior_recommended"] = len((prior or {}).get("recommended") or ())

        screened = _screen_bars(_screen_universe(_entries(prior, cand_list), as_of, deps,
                                                 dropped), as_of, deps, dropped)

        articles = digest.visible_news(as_of - timedelta(weeks=NEWS_WEEKS), as_of,
                                       store=deps.store)
        provenance["news_rows_8w"] = len(articles)
        prior_notes = {}
        for n in (prior or {}).get("notes") or ():
            sym = book.normalize_symbol((n or {}).get("symbol"))
            if sym:
                prior_notes[sym] = n
        news_items = filings_used = 0
        retried = []
        for e in screened:
            sym = e["symbol"]
            if len(kept) >= MAX_CANDIDATES:
                _drop(dropped, e, "cap", "over the %d-candidate cap" % MAX_CANDIDATES)
                continue
            dossier = deps.dossier_fn(sym, as_of)
            if (dossier or {}).get("factors_status") == filings.FACTORS_FETCH_FAILED:
                retried.append(sym)
                dossier = deps.dossier_fn(sym, as_of)
                if (dossier or {}).get("factors_status") == filings.FACTORS_FETCH_FAILED:
                    _drop(dropped, e, "dossier",
                          "SEC fundamentals fetch failed twice (factors_status=fetch_failed)")
                    continue
            news = digest.symbol_news(sym, as_of, weeks=NEWS_WEEKS, limit=NEWS_LIMIT,
                                      store=deps.store, articles=articles)
            dossier = dict(dossier or {})
            dossier.update({"trend_stats": e.get("trend_stats"), "news": news,
                            "prior_note": prior_notes.get(sym),
                            "trend_names": e["trend_names"], "why": e["why"]})
            try:
                note = _stage(caller, "research", prompts.research_prompt(sym, dossier, as_of),
                              ResearchNote, secrets)
            except _WeekFailed as exc:
                _drop(dropped, e, "research", "research call failed twice: %s"
                      % _scrub(exc.error, secrets))
                continue
            if book.normalize_symbol(note.symbol) != sym:
                note = note.model_copy(update={"symbol": sym})
            notes.append(note)
            news_items += len(news)
            filings_used += len(dossier.get("filings") or ())
            kept.append({"symbol": sym, "kind": e["kind"], "sources": e["sources"],
                         "trend_names": e["trend_names"], "why": e["why"],
                         "factors_status": dossier.get("factors_status"),
                         "filings": len(dossier.get("filings") or ()),
                         "news_items": len(news)})
        provenance.update({"news_items": news_items, "filings": filings_used,
                           "dossier_retries": retried})

        proposal = _stage(caller, "book", prompts.book_prompt(scan, notes, as_of), BookProposal,
                          secrets)
    except _WeekFailed as exc:
        row.update({"status": "failed", "failed_stage": exc.stage,
                    "error": _scrub(exc.error, secrets) or "no structured result",
                    "scan": _dump(scan) if scan is not None else None,
                    "candidates": kept, "dropped": dropped,
                    "notes": [_dump(n) for n in notes], "book": None, "recommended": [],
                    "calls_used": caller.calls_used - calls_before,
                    "built_at": {"started": _iso(started), "finished": _iso(now())}})
        return row

    by_note = {n.symbol: n for n in notes}
    recommended, seen = [], set()
    for p in proposal.picks:
        sym = book.normalize_symbol(p.symbol)
        if sym == book.BIL or (sym is not None and sym in seen):
            continue                        # the cash remainder; a repeated pick
        entry = {"raw": p.symbol, "symbol": sym, "sources": ["book"]}
        if sym is None:
            _drop(dropped, entry, "recommended", "book pick is not a US-listed ticker")
            continue
        seen.add(sym)
        if p.target_weight <= 0:
            _drop(dropped, entry, "recommended", "book pick at target_weight 0")
            continue
        n = by_note.get(sym)
        if n is None:
            _drop(dropped, entry, "recommended", "book pick has no research note this week")
            continue
        recommended.append({"symbol": sym, "conviction": n.conviction, "thesis": n.thesis})
    row.update({"status": "ok", "scan": _dump(scan), "candidates": kept,
                "dropped": dropped, "notes": [_dump(n) for n in notes],
                "book": _dump(proposal), "recommended": recommended,
                "calls_used": caller.calls_used - calls_before,
                "built_at": {"started": _iso(started), "finished": _iso(now())}})
    return row


# -- the run -------------------------------------------------------------------------

def _mondays(start: date, end: date):
    d, last = clock.week_monday(start), clock.week_monday(end)
    while d <= last:
        yield d
        d += timedelta(days=7)


def build(chain_id: str, start: date, end: date, *, provider: str, model: str,
          api_key: str | None, max_calls: int, prompt_version: str = prompts.PROMPT_VERSION,
          provider_config: dict | None = None, sleep=time.sleep, now=_utcnow, store=None,
          caller=None, bars_fetcher=None, filings_fetcher=None, dossier_fn=None,
          bars_instance: str = PAPER_INSTANCE) -> dict:
    """Build the chain's weeks from ``start``'s to ``end``'s, oldest first,
    and return a summary of counts (see the module docstring).

    ``caller``: a ``StageCaller``-like object (``call`` and ``calls_used``);
    by default one over ``provider`` / ``model`` / ``api_key`` with
    ``max_calls``. ``bars_fetcher(symbols, start, end)``,
    ``filings_fetcher(symbol, start, end)`` and ``dossier_fn(symbol,
    as_of)`` default to the network-backed paper-credential bars fetch,
    ``filings.fetch_filings`` and ``filings.filing_dossier``."""
    if end < start:
        raise ValueError("end %s is before start %s" % (end, start))
    st = _store(store)
    if caller is None:
        caller = StageCaller(provider, model, api_key, max_calls=max_calls,
                             provider_config=provider_config, now=now)
    deps = _Deps(store=st, bars_fetcher=bars_fetcher, filings_fetcher=filings_fetcher,
                 dossier_fn=dossier_fn, bars_instance=bars_instance)
    secrets = tuple(s for s in (api_key,) if s)
    summary = {"chain_id": chain_id, "model": model, "prompt_version": prompt_version,
               "weeks": 0, "built_ok": 0, "built_failed": 0, "skipped_ok": 0,
               "not_yet_due": 0, "quota_waits": 0, "calls_used": 0, "stopped": None,
               "stopped_week": None}
    calls_at_start = caller.calls_used
    try:
        for monday in _mondays(start, end):
            summary["weeks"] += 1
            existing = ledger.get_week(chain_id, monday, model, prompt_version, store=st)
            if isinstance(existing, dict) and existing.get("status") == "ok":
                summary["skipped_ok"] += 1
                continue
            if ledger.week_as_of(monday) >= now():
                summary["not_yet_due"] += 1
                continue
            summary["stopped_week"] = monday.isoformat()
            waits = 0
            row = None
            while row is None:
                try:
                    row = build_week(chain_id, monday, caller=caller, provider=provider,
                                     model=model, prompt_version=prompt_version, deps=deps,
                                     now=now, secrets=secrets)
                except QuotaExhausted as exc:
                    if waits >= MAX_QUOTA_WAITS_PER_WEEK:
                        summary["stopped"] = "quota_wait_cap"
                        log.warning("TR ledger %s week %s: quota hit after %d waits in a "
                                    "row; stopping (week not written)", chain_id, monday,
                                    waits)
                        break
                    waits += 1
                    delay = quota_delay(exc.reset_at, now())
                    summary["quota_waits"] += 1
                    log.warning("TR ledger %s week %s: quota hit; week discarded, retrying "
                                "in %.0f s (wait %d of %d)", chain_id, monday, delay, waits,
                                MAX_QUOTA_WAITS_PER_WEEK)
                    sleep(delay)
            if row is None:
                break
            summary["stopped_week"] = None
            ledger.put_week(row, store=st)
            summary["built_ok" if row["status"] == "ok" else "built_failed"] += 1
            log.info("TR ledger %s week %s: %s, %d calls, %d researched, %d dropped",
                     chain_id, monday, row["status"], row["calls_used"],
                     len(row.get("candidates") or ()), len(row.get("dropped") or ()))
    except BudgetExceeded:
        summary["stopped"] = "budget"
        log.warning("TR ledger %s: LLM call budget reached; stopping (in-progress week "
                    "not written)", chain_id)
    summary["calls_used"] = caller.calls_used - calls_at_start
    return summary


# -- CLI -----------------------------------------------------------------------------

def resolve_model_row(model_id: str) -> dict:
    """``{"provider", "model", "api_key", "provider_config"}`` from a Models
    row, through the broker's ``model_resolver``. Raises RuntimeError when
    the row is missing."""
    from model_resolver import resolve_model_refs_in_config
    cfg = resolve_model_refs_in_config(None, {"tr_llm_model_id": model_id},
                                       force_refresh=True)
    provider = str(cfg.get("tr_llm_provider") or "").strip().lower()
    model = str(cfg.get("tr_llm_model") or "").strip()
    if not provider or not model:
        raise RuntimeError("Models row %r not found or has no provider/model" % model_id)
    provider_config = {}
    for src, dst in (("tr_cli_path", "cli_path"), ("tr_extra_args", "extra_args"),
                     ("tr_llm_reasoning_effort", "reasoning_effort")):
        if cfg.get(src):
            provider_config[dst] = cfg[src]
    return {"provider": provider, "model": model, "api_key": cfg.get("tr_llm_api_key") or None,
            "provider_config": provider_config or None}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        prog="python3 -m trend_research.build_ledger",
        description="Build the TR research ledger, one row per week (resumable).")
    parser.add_argument("--chain", required=True, help="chain id, e.g. honest-<model>")
    parser.add_argument("--start", required=True, type=date.fromisoformat,
                        help="a day in the first week, YYYY-MM-DD")
    parser.add_argument("--end", required=True, type=date.fromisoformat,
                        help="a day in the last week, YYYY-MM-DD")
    parser.add_argument("--model-id", required=True, help="Models row id")
    parser.add_argument("--max-calls", type=int, default=2000,
                        help="LLM call budget for this run (default 2000)")
    parser.add_argument("--prompt-version", default=prompts.PROMPT_VERSION)
    parser.add_argument("--instance", default=PAPER_INSTANCE,
                        help="paper instance whose Alpaca data credentials fetch bars")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    m = resolve_model_row(args.model_id)
    summary = build(args.chain, args.start, args.end, provider=m["provider"],
                    model=m["model"], api_key=m["api_key"], max_calls=args.max_calls,
                    prompt_version=args.prompt_version,
                    provider_config=m["provider_config"], bars_instance=args.instance)
    print("TR ledger %s (%s, %s): %d weeks -- %d built ok, %d failed, %d already ok, "
          "%d not yet due; %d LLM calls, %d quota waits%s"
          % (summary["chain_id"], summary["model"], summary["prompt_version"],
             summary["weeks"], summary["built_ok"], summary["built_failed"],
             summary["skipped_ok"], summary["not_yet_due"], summary["calls_used"],
             summary["quota_waits"],
             {"budget": "; stopped at the call budget",
              "quota_wait_cap": "; stopped after %d quota waits in a row"
              % MAX_QUOTA_WAITS_PER_WEEK}.get(summary["stopped"], "")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
