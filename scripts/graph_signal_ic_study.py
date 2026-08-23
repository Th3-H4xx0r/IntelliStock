#!/usr/bin/env python3
"""CROSS-SECTIONAL skill test for the Graph Nexus signal.

Question (never measured here before): does the per-ticker Nexus score RANK
stocks better than chance?  This is orthogonal to the DIRECTIONAL question
already answered by scripts/strategy_x_voter_study.py (macro_llm 0.4762 on
market direction = no skill).  Cohen & Frazzini economic-links alpha is a
cross-sectional effect; market timing is not.

Stages are cached to the scratchpad so re-runs are cheap:
    stage 1  signals   <- Postgres
    stage 2  prices    <- yfinance
    stage 3  IC tables <- pandas

Run:  python3 scripts/_xsec_study.py [--refetch-signals] [--refetch-prices]
"""
from __future__ import annotations

import os
import sys
import math
import argparse
from pathlib import Path

_PRIMARY = Path("/Users/pranavkrishna/PranavFiles/coding-projects/IntelliStock")
sys.path.insert(0, str(_PRIMARY / "scripts"))
from pull_backtest_logs import _load_dotenv  # noqa: E402

_load_dotenv(_PRIMARY)

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402
import psycopg2  # noqa: E402

CACHE = Path(
    "/private/tmp/claude-501/-Users-pranavkrishna-PranavFiles-coding-projects-"
    "IntelliStock--claude-worktrees-main-session/"
    "45660cc4-858b-490a-90fc-c7bcebc48402/scratchpad"
)
CACHE.mkdir(parents=True, exist_ok=True)

SIG_TC = CACHE / "sig_tradectx.pkl"
SIG_DF = CACHE / "sig_dayfeat.pkl"
PX = CACHE / "prices.pkl"

# entry lags (trading days after date_key) and holding horizons
LAGS = [0, 1, 5, 10, 21]   # decay curve: how stale can the signal get?
HORIZONS = [1, 5, 21, 63]
MIN_NAMES = 20          # min cross-section width for a date to count
MIN_DATES_PER_SYM = 15  # universe filter


# --------------------------------------------------------------------------
# stats helpers
# --------------------------------------------------------------------------
def spearman(a: pd.Series, b: pd.Series) -> float:
    """Spearman rho = Pearson on average-tied ranks. scipy is not installed."""
    ra = a.rank()
    rb = b.rank()
    sa, sb = ra.std(), rb.std()
    if not (sa > 0 and sb > 0):
        return float("nan")
    return float(ra.corr(rb))



def newey_west_se(x: np.ndarray, lags: int) -> float:
    """NW SE of the mean of a serially-correlated series."""
    x = np.asarray(x, dtype=float)
    x = x[~np.isnan(x)]
    n = len(x)
    if n < 3:
        return float("nan")
    d = x - x.mean()
    g0 = float(d @ d) / n
    var = g0
    for L in range(1, min(lags, n - 1) + 1):
        gl = float(d[L:] @ d[:-L]) / n
        var += 2.0 * (1.0 - L / (lags + 1.0)) * gl
    var = max(var, 1e-18)
    return math.sqrt(var / n)


def summarise(ic: pd.Series, overlap: int, label: str) -> dict:
    """mean IC, NW t-stat, 95% CI, plus a non-overlapping cross-check."""
    ic = ic.dropna()
    n = len(ic)
    if n < 8:
        return {"n_dates": n, "mean_ic": np.nan, "t": np.nan,
                "ci_lo": np.nan, "ci_hi": np.nan, "hit": np.nan,
                "n_indep": 0, "mean_ic_indep": np.nan, "t_indep": np.nan,
                "signal": label}
    m = float(ic.mean())
    se = newey_west_se(ic.values, max(overlap - 1, 0))
    t = m / se if se and se == se else float("nan")
    # non-overlapping cross-check: every `overlap`-th date
    ind = ic.iloc[::max(overlap, 1)]
    mi = float(ind.mean())
    sei = float(ind.std(ddof=1) / math.sqrt(len(ind))) if len(ind) > 2 else np.nan
    ti = mi / sei if sei and sei == sei and sei > 0 else float("nan")
    return {
        "signal": label,
        "n_dates": n,
        "mean_ic": m,
        "se": se,
        "t": t,
        "ci_lo": m - 1.96 * se,
        "ci_hi": m + 1.96 * se,
        "hit": float((ic > 0).mean()),
        # minimum IC this sample could have detected at 80% power, 5% two-sided
        "mde80": 2.80 * se,
        "n_indep": len(ind),
        "mean_ic_indep": mi,
        "t_indep": ti,
    }


# --------------------------------------------------------------------------
# stage 1 - signals
# --------------------------------------------------------------------------
def pg():
    return psycopg2.connect(
        host=os.environ.get("POSTGRES_HOST", "server7"),
        port=int(os.environ.get("POSTGRES_PORT", "5432")),
        user=os.environ.get("POSTGRES_USER", "intellistock"),
        dbname=os.environ.get("POSTGRES_DB", "IntelliStock"),
        password=os.environ.get("POSTGRES_PASSWORD"),
        connect_timeout=30,
    )


TC_SQL = """
SELECT doc->>'symbol'                                            AS symbol,
       doc->>'date_key'                                          AS date_key,
       doc->>'base_instance_id'                                  AS base_inst,
       doc->>'history_scope_id'                                  AS scope,
       (substring(doc->>'reason' from 'Base=([+-]?[0-9.]+)'))::float AS base_score,
       (substring(doc->>'reason' from 'raw=([+-]?[0-9.]+)'))::float   AS raw_score,
       (doc->>'score')::float                                     AS nexus_score,
       (doc->>'confidence')::float                                AS confidence,
       (doc->'features'->>'base_raw_score')::float                AS f_base_raw,
       (doc->'features'->>'direct_sentiment')::float              AS f_direct_sent,
       (doc->'features'->>'historical_analog_avg_return')::float   AS f_analog_ret,
       (doc->'features'->>'company_avg_impact')::float            AS f_impact,
       (doc->'features'->>'n_paths')::float                       AS f_npaths,
       doc->>'dominant_event_type'                                AS event_type
FROM "GraphNexusTradeContexts"
"""

DF_SQL = """
SELECT doc->>'symbol'                       AS symbol,
       doc->>'date_key'                     AS date_key,
       (doc->>'finbert_sentiment_avg')::float AS finbert_sent,
       (doc->>'finbert_impulse_max')::float   AS finbert_impulse,
       (doc->>'avg_impact_strength')::float   AS impact,
       (doc->>'avg_relevance')::float         AS relevance,
       (doc->>'positive_count')::float        AS pos,
       (doc->>'negative_count')::float        AS neg,
       (doc->>'news_count')::float            AS news_count,
       (doc->>'government_action_hits')::float AS gov_hits
FROM "GraphNexusNewsDayFeatures"
"""


PUBTIME_SQL = """
SELECT sym                                        AS symbol,
       date_key,
       count(*)                                   AS n_art,
       count(*) FILTER (WHERE hr >= 20 OR hr = 0) AS n_after_close
FROM (
  SELECT doc->>'date_key' AS date_key,
         substring(doc->>'published_at',12,2)::int AS hr,
         jsonb_array_elements_text(doc->'symbols') AS sym
  FROM "GraphNexusNewsRaw"
  WHERE doc->>'published_at' ~ '^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}'
) x
GROUP BY 1, 2
"""


def fetch_pubtime():
    f = CACHE / "pubtime.pkl"
    if f.exists():
        return pd.read_pickle(f)
    con = pg()
    d = pd.read_sql(PUBTIME_SQL, con)
    con.close()
    d.to_pickle(f)
    return d


def fetch_signals(refetch: bool):
    if SIG_TC.exists() and SIG_DF.exists() and not refetch:
        return pd.read_pickle(SIG_TC), pd.read_pickle(SIG_DF)
    con = pg()
    print("  querying GraphNexusTradeContexts ...", flush=True)
    tc = pd.read_sql(TC_SQL, con)
    print(f"    {len(tc):,} rows")
    print("  querying GraphNexusNewsDayFeatures ...", flush=True)
    df = pd.read_sql(DF_SQL, con)
    print(f"    {len(df):,} rows")
    con.close()
    tc.to_pickle(SIG_TC)
    df.to_pickle(SIG_DF)
    return tc, df


# --------------------------------------------------------------------------
# stage 2 - prices
# --------------------------------------------------------------------------
def fetch_prices(symbols, refetch: bool) -> pd.DataFrame:
    """Incremental + rate-limit-aware. yfinance returns YFRateLimitError for
    whole batches; those are NOT delistings, so retry them with backoff and
    persist after every batch."""
    import time
    import yfinance as yf

    syms = sorted(set(symbols))
    have = pd.DataFrame()
    if PX.exists() and not refetch:
        have = pd.read_pickle(PX)
    dead_f = CACHE / "dead_symbols.txt"
    dead = set(dead_f.read_text().split()) if dead_f.exists() else set()

    def missing():
        return [x for x in syms if x not in have.columns and x not in dead]

    passes = [(120, 1.0), (60, 3.0), (25, 8.0), (10, 15.0)]
    for bsize, pause in passes:
        miss = missing()
        if not miss:
            break
        print(f"  pass batch={bsize} sleep={pause}s  missing={len(miss)}",
              flush=True)
        for i in range(0, len(miss), bsize):
            chunk = miss[i:i + bsize]
            try:
                d = yf.download(chunk, start="2023-05-01", end="2026-08-23",
                                auto_adjust=True, progress=False, threads=True,
                                group_by="column")
            except Exception as e:  # noqa: BLE001
                print(f"    batch {i} error: {type(e).__name__}")
                time.sleep(pause * 3)
                continue
            if d is None or len(d) == 0:
                time.sleep(pause)
                continue
            if isinstance(d.columns, pd.MultiIndex):
                cl = d["Close"]
            else:
                cl = d[["Close"]].copy()
                cl.columns = chunk[:1]
            cl = cl.dropna(axis=1, how="all")
            cl.index = pd.to_datetime(cl.index).tz_localize(None).normalize()
            if len(cl.columns):
                have = cl if len(have) == 0 else have.join(
                    cl[[c for c in cl.columns if c not in have.columns]],
                    how="outer")
                have.to_pickle(PX)
            time.sleep(pause)
        # anything still absent after the last (smallest) pass is presumed dead
        if bsize == passes[-1][0]:
            dead |= set(missing())
            dead_f.write_text("\n".join(sorted(dead)))
        print(f"    have {len([x for x in syms if x in have.columns])}/{len(syms)}",
              flush=True)

    have = have.loc[:, ~have.columns.duplicated()].sort_index()
    have.to_pickle(PX)
    return have


# --------------------------------------------------------------------------
# stage 3 - panel construction
# --------------------------------------------------------------------------
def build_returns(px: pd.DataFrame) -> dict:
    """fwd[(L,h)] -> DataFrame indexed by trading day i, value = ret from
    close[i+L] to close[i+L+h]."""
    out = {}
    for L in LAGS:
        base = px.shift(-L)
        for h in HORIZONS:
            out[(L, h)] = px.shift(-(L + h)) / base - 1.0
    return out


def build_momentum(px: pd.DataFrame) -> dict:
    ret = px.pct_change()
    return {
        "mom_21": px / px.shift(21) - 1.0,
        "mom_5": px / px.shift(5) - 1.0,
        "rev_1": ret,
        "vol_20": ret.rolling(20).std(),
    }


def cs_ic(panel: pd.DataFrame, sig_col: str, ret_col: str,
          min_names: int = MIN_NAMES) -> pd.Series:
    """Per-date cross-sectional Spearman IC. Rank-based => automatically
    market-neutral (a common additive shift preserves ranks)."""
    def _one(g):
        g = g[[sig_col, ret_col]].dropna()
        if len(g) < min_names or g[sig_col].nunique() < 3:
            return np.nan
        return spearman(g[sig_col], g[ret_col])
    return panel.groupby("ti", sort=True).apply(_one)


def cs_ic_resid(panel: pd.DataFrame, sig_col: str, ctrl_cols: list,
                ret_col: str, min_names: int = MIN_NAMES) -> pd.Series:
    """IC of the signal AFTER orthogonalising its cross-sectional ranks
    against control ranks (momentum). Per date: rank both, OLS-residualise
    the signal rank on the control ranks, Spearman vs forward return."""
    def _one(g):
        cols = [sig_col, ret_col] + ctrl_cols
        g = g[cols].dropna()
        if len(g) < min_names or g[sig_col].nunique() < 3:
            return np.nan
        y = g[sig_col].rank().values
        X = np.column_stack([np.ones(len(g))] +
                            [g[c].rank().values for c in ctrl_cols])
        try:
            beta, *_ = np.linalg.lstsq(X, y, rcond=None)
        except np.linalg.LinAlgError:
            return np.nan
        res = y - X @ beta
        if np.nanstd(res) < 1e-12:
            return np.nan
        return spearman(pd.Series(res, index=g.index), g[ret_col])
    return panel.groupby("ti", sort=True).apply(_one)


def decile_spread(panel: pd.DataFrame, sig_col: str, ret_col: str,
                  min_names: int = MIN_NAMES) -> pd.Series:
    def _one(g):
        g = g[[sig_col, ret_col]].dropna()
        if len(g) < min_names or g[sig_col].nunique() < 5:
            return np.nan
        k = max(int(len(g) * 0.10), 3)
        s = g.sort_values(sig_col)
        return float(s[ret_col].iloc[-k:].mean() - s[ret_col].iloc[:k].mean())
    return panel.groupby("ti", sort=True).apply(_one)


# --------------------------------------------------------------------------
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--refetch-signals", action="store_true")
    ap.add_argument("--refetch-prices", action="store_true")
    args = ap.parse_args()

    pd.set_option("display.width", 200)
    pd.set_option("display.max_columns", 40)

    print("=" * 96)
    print("CROSS-SECTIONAL SKILL TEST - Graph Nexus per-ticker score")
    print("=" * 96)

    # ---------------- stage 1 -------------------------------------------
    print("\n[1] signals")
    tc, dfeat = fetch_signals(args.refetch_signals)

    # dedupe: rows are keyed instance_id = base|history_scope. The same
    # (symbol,date) is re-scored under many scopes/configs. Primary panel =
    # base_instance 'alpaca-main' (largest, longest), median across scopes.
    tc_main = tc[tc["base_inst"] == "alpaca-main"].copy()
    numcols = ["base_score", "raw_score", "nexus_score", "confidence",
               "f_base_raw", "f_direct_sent", "f_analog_ret", "f_impact",
               "f_npaths"]
    print(f"  TradeContexts rows                 {len(tc):,}")
    print(f"    alpaca-main rows                 {len(tc_main):,}")
    ev = (tc_main.groupby(["symbol", "date_key"])["event_type"]
          .agg(lambda x: x.mode().iat[0] if len(x.mode()) else None))
    tc_main = (tc_main.groupby(["symbol", "date_key"])[numcols]
               .median().reset_index())
    tc_main = tc_main.merge(ev.rename("event_type").reset_index(),
                            on=["symbol", "date_key"], how="left")
    print(f"    unique (symbol,date) after dedupe {len(tc_main):,}"
          f"  syms={tc_main.symbol.nunique():,}"
          f"  dates={tc_main.date_key.nunique()}")

    dcols = ["finbert_sent", "finbert_impulse", "impact", "relevance",
             "pos", "neg", "news_count", "gov_hits"]
    print(f"  NewsDayFeatures rows               {len(dfeat):,}")
    dfeat = (dfeat.groupby(["symbol", "date_key"])[dcols]
             .median().reset_index())
    print(f"    unique (symbol,date) after dedupe {len(dfeat):,}"
          f"  syms={dfeat.symbol.nunique():,}"
          f"  dates={dfeat.date_key.nunique()}")

    dfeat["net_news"] = ((dfeat["pos"] - dfeat["neg"]) /
                         dfeat["news_count"].replace(0, np.nan))
    dfeat["log_news"] = np.log1p(dfeat["news_count"])

    panel = tc_main.merge(dfeat, on=["symbol", "date_key"], how="outer")
    panel["date_key"] = pd.to_datetime(panel["date_key"])

    # universe filter
    cnt = panel.groupby("symbol")["date_key"].nunique()
    keep = cnt[cnt >= MIN_DATES_PER_SYM].index
    panel = panel[panel["symbol"].isin(keep)]
    print(f"  merged panel {len(panel):,} obs, "
          f"{panel.symbol.nunique():,} syms (>= {MIN_DATES_PER_SYM} dates), "
          f"{panel.date_key.nunique()} dates")

    # ---------------- stage 2 -------------------------------------------
    print("\n[2] prices")
    syms = sorted(panel["symbol"].unique())
    px = fetch_prices(syms, args.refetch_prices)
    got = [s for s in syms if s in px.columns]
    print(f"  yfinance returned {len(got)}/{len(syms)} symbols "
          f"({100*len(got)/max(len(syms),1):.1f}%) "
          f"- the {len(syms)-len(got)} missing are mostly delistings "
          f"=> SURVIVORSHIP BIAS in favour of the signal")
    px = px[got].sort_index()
    px = px.where(px > 0)

    fwd = build_returns(px)
    mom = build_momentum(px)

    # map each date_key to the first trading day >= date_key
    cal = px.index
    dk = pd.Index(sorted(panel["date_key"].unique()))
    pos = cal.searchsorted(dk, side="left")
    ok = pos < len(cal)
    dk_map = pd.Series(pos[ok], index=dk[ok])
    panel = panel[panel["date_key"].isin(dk_map.index)].copy()
    panel["ti"] = panel["date_key"].map(dk_map).astype(int)
    panel = panel[panel["symbol"].isin(got)]
    # weekend/holiday date_keys map onto the same first tradeable day; keep the
    # LATEST signal for each (trading day, symbol) so no forward window is
    # counted twice.
    n_before = len(panel)
    panel = (panel.sort_values("date_key")
                  .drop_duplicates(subset=["ti", "symbol"], keep="last"))
    print(f"  collapsed {n_before - len(panel):,} duplicate (trading-day,symbol) "
          f"rows from weekend/holiday date_keys")

    # attach returns + momentum by (trading-day index, symbol)
    def attach(mat: pd.DataFrame, name: str):
        m = mat.reset_index(drop=True)           # index = positional day
        stacked = m.stack(future_stack=True).rename(name)
        stacked.index.names = ["ti", "symbol"]
        return stacked

    keyed = panel.set_index(["ti", "symbol"])
    for (L, h), mat in fwd.items():
        keyed[f"fwd_L{L}_h{h}"] = attach(mat, "v")
    for k, mat in mom.items():
        keyed[k] = attach(mat, "v")
    panel = keyed.reset_index()

    print(f"  panel with prices: {len(panel):,} obs, "
          f"{panel.symbol.nunique()} syms, {panel.date_key.nunique()} dates")

    # ---------------- stage 3 -------------------------------------------
    signals = {
        "nexus_base_score":   "base_score",     # strategy's own continuous score
        "nexus_raw_score":    "raw_score",
        "nexus_action(-1/0/1)": "nexus_score",
        "finbert_sent":       "finbert_sent",
        "net_news_pn":        "net_news",
        "avg_impact":         "impact",
        "finbert_impulse":    "finbert_impulse",
        "log_news_count":     "log_news",
        "analog_avg_return":  "f_analog_ret",
        "MOM_21d (control)":  "mom_21",
        "REV_1d (control)":   "rev_1",
    }

    # ---- plumbing self-tests -------------------------------------------
    print("\n[SELF-TEST] machinery validation")
    perfect = cs_ic(panel.assign(_p=panel["fwd_L1_h21"]), "_p", "fwd_L1_h21")
    print(f"  perfect-foresight IC (must be 1.0)          "
          f"{perfect.mean():+.4f}")
    shifted = cs_ic(panel.assign(_p=-panel["fwd_L1_h21"]), "_p", "fwd_L1_h21")
    print(f"  inverted-foresight IC (must be -1.0)        "
          f"{shifted.mean():+.4f}")
    r1 = cs_ic(panel, "rev_1", "fwd_L0_h1")
    print(f"  1d reversal IC vs next-day ret (lit: < 0)   "
          f"{r1.mean():+.4f}   <- if this is not clearly negative the")
    print("                                                  panel alignment is wrong")

    print("\n" + "=" * 96)
    print("[3a] COVERAGE per signal (obs with a non-null signal AND a 21d fwd ret)")
    print("=" * 96)
    cov = []
    for lbl, col in signals.items():
        sub = panel[[col, "fwd_L1_h21", "ti"]].dropna()
        nz = (sub[col] != 0).sum() if len(sub) else 0
        cov.append({"signal": lbl, "obs": len(sub),
                    "nonzero": int(nz),
                    "dates": sub.ti.nunique(),
                    "names/date": round(len(sub) / max(sub.ti.nunique(), 1), 1)})
    print(pd.DataFrame(cov).to_string(index=False))

    # ---- main IC grid --------------------------------------------------
    rows = []
    for lbl, col in signals.items():
        if col not in panel.columns:
            continue
        for L in LAGS:
            for h in HORIZONS:
                rc = f"fwd_L{L}_h{h}"
                ic = cs_ic(panel, col, rc)
                s = summarise(ic, overlap=h, label=lbl)
                s.update({"lag": L, "horizon": h})
                rows.append(s)
    ictab = pd.DataFrame(rows)

    def show(tab, cols=None):
        c = cols or ["signal", "lag", "horizon", "n_dates", "mean_ic", "t",
                     "ci_lo", "ci_hi", "mde80", "hit", "n_indep", "t_indep"]
        t = tab[c].copy()
        for f in ("mean_ic", "ci_lo", "ci_hi", "mde80"):
            if f in t:
                t[f] = t[f].map(lambda v: f"{v:+.4f}" if pd.notna(v) else "  -  ")
        for f in ("t", "t_indep"):
            if f in t:
                t[f] = t[f].map(lambda v: f"{v:+.2f}" if pd.notna(v) else "  -  ")
        if "hit" in t:
            t["hit"] = t["hit"].map(lambda v: f"{v:.3f}" if pd.notna(v) else " - ")
        print(t.to_string(index=False))

    print("\n" + "=" * 96)
    print("[3b] SPEARMAN IC  signal(t) vs forward return entered at t+LAG, held HORIZON")
    print("     lag=0 is CONTAMINATED (date_key is the UTC publication date; US")
    print("     evening news lands after that day's close). lag=1 is the honest read.")
    print("=" * 96)
    for lbl in signals:
        sub = ictab[ictab.signal == lbl]
        if sub.empty or sub["mean_ic"].isna().all():
            continue
        print(f"\n--- {lbl} ---")
        show(sub)

    # ---- decile spreads -------------------------------------------------
    print("\n" + "=" * 96)
    print("[3c] TOP-DECILE minus BOTTOM-DECILE forward return (equal weight)")
    print("=" * 96)
    drows = []
    for lbl, col in signals.items():
        if col not in panel.columns:
            continue
        for L in (1, 5):
            for h in HORIZONS:
                sp = decile_spread(panel, col, f"fwd_L{L}_h{h}").dropna()
                if len(sp) < 8:
                    continue
                m = float(sp.mean())
                se = newey_west_se(sp.values, max(h - 1, 0))
                drows.append({
                    "signal": lbl, "lag": L, "horizon": h, "n_dates": len(sp),
                    "spread_bps": 1e4 * m,
                    "t": m / se if se == se and se > 0 else np.nan,
                    "ci_lo_bps": 1e4 * (m - 1.96 * se),
                    "ci_hi_bps": 1e4 * (m + 1.96 * se),
                    "hit": float((sp > 0).mean()),
                })
    dt = pd.DataFrame(drows)
    if not dt.empty:
        d = dt.copy()
        for f in ("spread_bps", "ci_lo_bps", "ci_hi_bps"):
            d[f] = d[f].map(lambda v: f"{v:+8.1f}")
        d["t"] = d["t"].map(lambda v: f"{v:+.2f}" if pd.notna(v) else " - ")
        d["hit"] = d["hit"].map(lambda v: f"{v:.3f}")
        print(d.to_string(index=False))

    # ---- momentum-orthogonalised IC ------------------------------------
    print("\n" + "=" * 96)
    print("[3d] MOMENTUM CONTROL - IC after residualising the signal's")
    print("     cross-sectional ranks on rank(mom_21) and rank(rev_1)")
    print("=" * 96)
    rrows = []
    for lbl, col in signals.items():
        if col in ("mom_21", "rev_1") or col not in panel.columns:
            continue
        for L in (1, 5):
            for h in (5, 21, 63):
                ic = cs_ic_resid(panel, col, ["mom_21", "rev_1"],
                                 f"fwd_L{L}_h{h}")
                s = summarise(ic, overlap=h, label=lbl)
                s.update({"lag": L, "horizon": h})
                rrows.append(s)
    rt = pd.DataFrame(rrows)
    if not rt.empty:
        show(rt)

    # ---- raw vs residual side-by-side for the headline signals ---------
    print("\n" + "=" * 96)
    print("[3e] HEADLINE COMPARISON (lag=1 = clean entry, lag=5 = skip-a-week)")
    print("=" * 96)
    head = ictab[(ictab.lag.isin([1, 5]))].pivot_table(
        index=["signal", "horizon"], columns="lag",
        values=["mean_ic", "t"], aggfunc="first")
    print(head.round(4).to_string())

    # ---- robustness: sub-periods, liquidity, nonzero-only, leg split ----
    HEAD = ["nexus_base_score", "nexus_raw_score", "nexus_action(-1/0/1)",
            "finbert_sent", "net_news_pn", "MOM_21d (control)"]

    print("\n" + "=" * 96)
    print("[3f] SUB-PERIOD STABILITY (dates split in half), lag=1")
    print("=" * 96)
    dts = np.sort(panel["ti"].unique())
    cut = dts[len(dts) // 2]
    srows = []
    for lbl in HEAD:
        col = signals[lbl]
        if col not in panel.columns:
            continue
        for h in (5, 21):
            for tag, sub in (("early", panel[panel.ti < cut]),
                             ("late", panel[panel.ti >= cut])):
                ic = cs_ic(sub, col, f"fwd_L1_h{h}")
                r = summarise(ic, overlap=h, label=lbl)
                r.update({"period": tag, "horizon": h, "lag": 1})
                srows.append(r)
    st = pd.DataFrame(srows)
    if not st.empty:
        show(st, ["signal", "period", "horizon", "n_dates", "mean_ic", "t",
                  "ci_lo", "ci_hi", "hit"])

    print("\n" + "=" * 96)
    print("[3g] LIQUIDITY SCREEN - names priced >= $5 only "
          "(the discovered universe is micro-cap heavy and much of it is")
    print("     not realistically tradeable), lag=1")
    print("=" * 96)
    pxi = px.reset_index(drop=True)
    lv = pxi.stack(future_stack=True).rename("px_level")
    lv.index.names = ["ti", "symbol"]
    panel = panel.merge(lv.reset_index(), on=["ti", "symbol"], how="left")
    liq = panel[panel["px_level"] >= 5.0]
    print(f"  obs kept {len(liq):,}/{len(panel):,} "
          f"({100*len(liq)/max(len(panel),1):.1f}%)")
    lrows = []
    for lbl in HEAD:
        col = signals[lbl]
        if col not in panel.columns:
            continue
        for h in (5, 21, 63):
            ic = cs_ic(liq, col, f"fwd_L1_h{h}")
            r = summarise(ic, overlap=h, label=lbl)
            r.update({"horizon": h, "lag": 1})
            lrows.append(r)
    lt = pd.DataFrame(lrows)
    if not lt.empty:
        show(lt, ["signal", "horizon", "n_dates", "mean_ic", "t",
                  "ci_lo", "ci_hi", "hit"])

    print("\n" + "=" * 96)
    print("[3h] NON-ZERO SIGNAL SUBSET - rank only the names the graph")
    print("     actually scored (drops the large tied-at-zero block), lag=1")
    print("=" * 96)
    nrows = []
    for lbl in HEAD:
        col = signals[lbl]
        if col not in panel.columns:
            continue
        sub = panel[panel[col].fillna(0) != 0]
        for h in (5, 21, 63):
            ic = cs_ic(sub, col, f"fwd_L1_h{h}")
            r = summarise(ic, overlap=h, label=lbl)
            r.update({"horizon": h, "lag": 1})
            nrows.append(r)
    nt = pd.DataFrame(nrows)
    if not nt.empty:
        show(nt, ["signal", "horizon", "n_dates", "mean_ic", "t",
                  "ci_lo", "ci_hi", "hit"])

    print("\n" + "=" * 96)
    print("[3i] LEG DECOMPOSITION - top/bottom decile vs that date's")
    print("     cross-sectional mean (bps), lag=1. Tells you whether any edge")
    print("     is on the long side, the short side, or neither.")
    print("=" * 96)

    def legs(pan, col, rc, min_names=MIN_NAMES):
        def _one(g):
            g = g[[col, rc]].dropna()
            if len(g) < min_names or g[col].nunique() < 5:
                return pd.Series({"top": np.nan, "bot": np.nan})
            k = max(int(len(g) * 0.10), 3)
            srt = g.sort_values(col)
            mu = srt[rc].mean()
            return pd.Series({"top": srt[rc].iloc[-k:].mean() - mu,
                              "bot": srt[rc].iloc[:k].mean() - mu})
        return pan.groupby("ti", sort=True).apply(_one)

    grows = []
    for lbl in HEAD:
        col = signals[lbl]
        if col not in panel.columns:
            continue
        for h in (5, 21):
            L = legs(panel, col, f"fwd_L1_h{h}").dropna()
            if len(L) < 8:
                continue
            for side in ("top", "bot"):
                v = L[side].values
                m = float(np.mean(v))
                se = newey_west_se(v, max(h - 1, 0))
                grows.append({"signal": lbl, "horizon": h, "leg": side,
                              "n_dates": len(v), "bps": 1e4 * m,
                              "t": m / se if se == se and se > 0 else np.nan,
                              "ci_lo_bps": 1e4 * (m - 1.96 * se),
                              "ci_hi_bps": 1e4 * (m + 1.96 * se)})
    gt = pd.DataFrame(grows)
    if not gt.empty:
        g = gt.copy()
        for f in ("bps", "ci_lo_bps", "ci_hi_bps"):
            g[f] = g[f].map(lambda v: f"{v:+8.1f}")
        g["t"] = g["t"].map(lambda v: f"{v:+.2f}" if pd.notna(v) else " - ")
        print(g.to_string(index=False))

    print("\n" + "=" * 96)
    print("[3j] EVENT-TYPE CONDITIONING - is the Nexus score a NEWS signal or a")
    print("     repackaged MOMENTUM signal?  'trend_momentum' is the strategy's")
    print("     own label for a price-momentum-driven score.  lag=1.")
    print("=" * 96)
    if "event_type" in panel.columns:
        et = panel["event_type"].fillna("")
        groups = {
            "trend_momentum": et.eq("trend_momentum"),
            "news/event (not trend_momentum, not general)":
                ~et.isin(["trend_momentum", "general", ""]) & ~et.str.startswith("conflicting"),
            "general": et.eq("general"),
        }
        erows = []
        for gname, mask in groups.items():
            sub = panel[mask]
            for h in (5, 21, 63):
                ic = cs_ic(sub, "base_score", f"fwd_L1_h{h}")
                r = summarise(ic, overlap=h, label=gname)
                r.update({"horizon": h, "lag": 1, "obs": int(mask.sum())})
                erows.append(r)
        et_tab = pd.DataFrame(erows)
        if not et_tab.empty:
            show(et_tab, ["signal", "obs", "horizon", "n_dates", "mean_ic",
                          "t", "ci_lo", "ci_hi", "mde80", "hit"])

        print("\n  correlation of the Nexus base_score with plain 21d momentum")
        print("  (per-date Spearman, mean across dates):")
        cc = panel.groupby("ti").apply(
            lambda g: spearman(g["base_score"], g["mom_21"])
            if g[["base_score", "mom_21"]].dropna().shape[0] >= MIN_NAMES
            else np.nan)
        cc = cc.dropna()
        print(f"    mean rho = {cc.mean():+.4f}  over {len(cc)} dates "
              f"(a high value means the 'signal' IS momentum)")

    print("\n" + "=" * 96)
    print("[3k] STRESS TEST of the only surviving positive:")
    print("     nexus_action restricted to names it actually flagged (+1/-1).")
    print("     A bull-market beta tilt would produce exactly this signature")
    print("     (IC growing with horizon, high hit rate), so control for vol.")
    print("=" * 96)
    nz = panel[panel["nexus_score"].fillna(0) != 0].copy()
    print(f"  obs {len(nz):,}  dates {nz.ti.nunique()}  "
          f"names/date {len(nz)/max(nz.ti.nunique(),1):.1f}")
    krows = []
    for h in (5, 21, 63):
        rc = f"fwd_L1_h{h}"
        raw = cs_ic(nz, "nexus_score", rc)
        r = summarise(raw, overlap=h, label="raw")
        r.update({"horizon": h, "variant": "raw"})
        krows.append(r)
        rv = cs_ic_resid(nz, "nexus_score", ["vol_20"], rc)
        r = summarise(rv, overlap=h, label="vol-neutral")
        r.update({"horizon": h, "variant": "resid vol_20"})
        krows.append(r)
        rm = cs_ic_resid(nz, "nexus_score", ["vol_20", "mom_21"], rc)
        r = summarise(rm, overlap=h, label="vol+mom-neutral")
        r.update({"horizon": h, "variant": "resid vol+mom"})
        krows.append(r)
    kt = pd.DataFrame(krows)
    show(kt, ["signal", "horizon", "n_dates", "mean_ic", "t", "ci_lo",
              "ci_hi", "hit", "n_indep", "mean_ic_indep", "t_indep"])

    print("\n  economic version: equal-weight BUY minus SELL forward return (bps),")
    print("  and each leg vs that date's cross-sectional mean:")
    for h in (5, 21, 63):
        rc = f"fwd_L1_h{h}"
        def _legs(g):
            g = g[["nexus_score", rc]].dropna()
            b = g[g.nexus_score > 0][rc]
            se_ = g[g.nexus_score < 0][rc]
            if len(b) < 3 or len(se_) < 3:
                return pd.Series({"ls": np.nan, "b": np.nan, "s": np.nan})
            mu = g[rc].mean()
            return pd.Series({"ls": b.mean() - se_.mean(),
                              "b": b.mean() - mu, "s": se_.mean() - mu})
        L = nz.groupby("ti").apply(_legs).dropna()
        if len(L) < 8:
            continue
        out = []
        for k, nm in (("ls", "BUY-SELL"), ("b", "BUY vs mean"), ("s", "SELL vs mean")):
            v = L[k].values
            m = float(np.mean(v)); se_ = newey_west_se(v, max(h - 1, 0))
            out.append(f"{nm}={1e4*m:+8.1f}bps (t={m/se_:+.2f})"
                       if se_ == se_ and se_ > 0 else f"{nm}=n/a")
        print(f"    h={h:<3} dates={len(L):<4} " + "  ".join(out))

    print("\n  sub-period split of the same cell (h=21, BUY-SELL IC):")
    cut2 = np.sort(nz["ti"].unique())[len(nz["ti"].unique()) // 2]
    for tag, sub in (("early", nz[nz.ti < cut2]), ("late", nz[nz.ti >= cut2])):
        ic = cs_ic(sub, "nexus_score", "fwd_L1_h21").dropna()
        if len(ic) < 8:
            continue
        m = float(ic.mean()); se_ = newey_west_se(ic.values, 20)
        print(f"    {tag:<6} dates={len(ic):<4} IC={m:+.4f} "
              f"t={m/se_:+.2f} hit={(ic>0).mean():.3f}")

    print("\n" + "=" * 96)
    print("[3l] PUBLICATION-TIME SPLIT - the decisive test of whether the large")
    print("     lag=0 IC is a forecast or just the market reacting to news that")
    print("     had not been published yet when you would have had to buy.")
    print("     US close = 20:00 UTC. An article stamped date_key=D but published")
    print("     at >= 20:00 UTC did NOT exist at D's close.")
    print("=" * 96)
    try:
        pt = fetch_pubtime()
    except Exception as e:  # noqa: BLE001
        print(f"  pubtime query failed: {e}")
        pt = None
    if pt is not None and len(pt):
        pt["date_key"] = pd.to_datetime(pt["date_key"])
        pn = panel.merge(pt, on=["symbol", "date_key"], how="inner")
        pn["clean"] = pn["n_after_close"] == 0
        pn["dirty"] = pn["n_after_close"] == pn["n_art"]
        print(f"  matched {len(pn):,} (symbol,date) obs to article timestamps")
        print(f"    all articles BEFORE the close: {int(pn['clean'].sum()):,}")
        print(f"    all articles AFTER  the close: {int(pn['dirty'].sum()):,}")
        prows = []
        for sname, col in (("finbert_sent", "finbert_sent"),
                           ("net_news_pn", "net_news"),
                           ("finbert_impulse", "finbert_impulse")):
            if col not in pn.columns:
                continue
            for tag, sub in (
                    ("pre-close  (entry at close D is REAL)", pn[pn["clean"]]),
                    ("post-close (entry at close D IMPOSSIBLE)", pn[pn["dirty"]])):
                for h in (1, 5):
                    ic = cs_ic(sub, col, f"fwd_L0_h{h}", min_names=15)
                    r = summarise(ic, overlap=h, label=f"{sname:<16}| {tag}")
                    r.update({"horizon": h, "lag": 0})
                    prows.append(r)
        ptab = pd.DataFrame(prows)
        if not ptab.empty:
            show(ptab, ["signal", "horizon", "n_dates", "mean_ic", "t",
                        "ci_lo", "ci_hi", "hit"])

    # save
    ictab.to_csv(CACHE / "ic_table.csv", index=False)
    if not dt.empty:
        dt.to_csv(CACHE / "decile_table.csv", index=False)
    if not rt.empty:
        rt.to_csv(CACHE / "resid_table.csv", index=False)
    print(f"\nsaved -> {CACHE}/ic_table.csv, decile_table.csv, resid_table.csv")


if __name__ == "__main__":
    main()
