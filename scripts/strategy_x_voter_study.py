#!/usr/bin/env python3
"""PRE-REGISTERED go/no-go for Strategy X.

Question: do the five proposed council voters have any directional skill on
forward QQQ returns?

Pre-registered threshold, fixed BEFORE looking at any result:
    a voter (or the weighted council) is USEFUL iff its directional hit rate on
    NON-OVERLAPPING 5-day forward QQQ returns is >= 0.58.

Why 0.58: beating buy-and-hold TQQQ requires accuracy above the fraction of
up-swings (~0.55 for NDX 2015-2025), plus a margin for the ~0.74% of NAV that
each direction change costs in fees alone. Below that the levered core is
arithmetically worse than holding TQQQ.

Voters measured:
    trend         ret20 / ret5 / MA50 / MA200 - from price only, full history
    vol           20d realised vol vs trailing median - price only
    macro_llm     GraphNexusNewsLLMMacro impact_direction x impact_strength
    news_breadth  GraphNexusNewsDayFeatures positive/negative counts
    (graph_breadth needs Neo4j as_of traversal - measured separately)

Everything here reads STORED data. No deploy, no backtest slot, no API calls
into the trading path.
"""
import math
import os
import sys
from pathlib import Path

_PRIMARY = Path("/Users/pranavkrishna/PranavFiles/coding-projects/IntelliStock")
sys.path.insert(0, str(_PRIMARY / "scripts"))
from pull_backtest_logs import _load_dotenv  # noqa: E402

_load_dotenv(_PRIMARY)

import pandas as pd  # noqa: E402
import psycopg2  # noqa: E402
import yfinance as yf  # noqa: E402

HORIZON = 5           # trading days forward
THRESHOLD = 0.58      # pre-registered


def pg():
    return psycopg2.connect(
        host=os.environ.get("POSTGRES_HOST", "server7"),
        port=int(os.environ.get("POSTGRES_PORT", "5432")),
        user=os.environ.get("POSTGRES_USER", "intellistock"),
        dbname=os.environ.get("POSTGRES_DB", "IntelliStock"),
        password=os.environ.get("POSTGRES_PASSWORD"),
        connect_timeout=20,
    )


def wilson(k, n, z=1.96):
    """Wilson score interval - honest small-sample bounds on a hit rate."""
    if n == 0:
        return (0.0, 0.0)
    p = k / n
    d = 1 + z * z / n
    c = p + z * z / (2 * n)
    m = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))
    return ((c - m) / d, (c + m) / d)


def score(name, votes: pd.Series, fwd: pd.Series):
    """Hit rate on NON-OVERLAPPING forward windows only."""
    df = pd.DataFrame({"v": votes, "f": fwd}).dropna()
    df = df[df["v"] != 0]
    if df.empty:
        return None
    # de-overlap: keep every HORIZON-th observation
    df = df.iloc[::HORIZON]
    n = len(df)
    if n < 20:
        return None
    hits = int(((df["v"] > 0) == (df["f"] > 0)).sum())
    p = hits / n
    lo, hi = wilson(hits, n)
    return {"voter": name, "n": n, "hits": hits, "p": p, "lo": lo, "hi": hi}


def main():
    print("=" * 78)
    print("PRE-REGISTERED VOTER HIT-RATE STUDY")
    print(f"horizon={HORIZON}d  threshold p>={THRESHOLD}  (non-overlapping windows)")
    print("=" * 78)

    # ---- price history -----------------------------------------------------
    px = yf.download(["QQQ", "TQQQ", "SQQQ", "SPY"], start="2014-01-01",
                     auto_adjust=True, progress=False)["Close"]
    px = px.dropna(how="all")
    qqq = px["QQQ"].dropna()
    print(f"\nQQQ daily closes: {len(qqq)}  {qqq.index[0].date()} -> "
          f"{qqq.index[-1].date()}")

    fwd = qqq.shift(-HORIZON) / qqq - 1.0
    up_frac = float((fwd.dropna() > 0).mean())
    print(f"fraction of {HORIZON}d forward windows that are UP: {up_frac:.4f}")
    print("  -> beating buy-and-hold TQQQ requires p > this number")

    # ---- baselines ---------------------------------------------------------
    print("\nBUY-AND-HOLD BASELINES (calendar year, auto-adjusted)")
    print("-" * 78)
    yrs = sorted({d.year for d in px.index})
    hdr = f"  {'yr':<6}" + "".join(f"{s:>10}" for s in ("QQQ", "TQQQ", "SQQQ"))
    print(hdr)
    n100 = 0
    for y in yrs:
        row = f"  {y:<6}"
        for s in ("QQQ", "TQQQ", "SQQQ"):
            ser = px[s].dropna()
            ser = ser[ser.index.year == y]
            if len(ser) < 100:
                row += f"{'-':>10}"
                continue
            r = (ser.iloc[-1] / ser.iloc[0] - 1) * 100
            row += f"{r:>9.1f}%"
            if s == "TQQQ" and r >= 100:
                n100 += 1
        print(row)
    print(f"\n  full years where buy-and-hold TQQQ cleared +100%: {n100}")

    # ---- voters from price -------------------------------------------------
    ret20 = qqq / qqq.shift(20) - 1.0
    ret5 = qqq / qqq.shift(5) - 1.0
    ma50 = qqq.rolling(50).mean()
    ma200 = qqq.rolling(200).mean()
    rvol = (qqq.pct_change().rolling(20).std() * math.sqrt(252))

    results = []

    # trend voter: the repo's own _detect_market_regime logic, as a signed vote
    trend = pd.Series(0.0, index=qqq.index)
    trend[(ret20 > 0) & (qqq > ma50)] = 1.0
    trend[(ret20 < -0.05) | (qqq < ma200)] = -1.0
    results.append(score("trend (repo regime rule)", trend, fwd))

    # simpler momentum variants, to see if ANY price rule clears the bar
    results.append(score("ret20 sign", ret20.apply(lambda x: 1 if x > 0 else -1), fwd))
    results.append(score("ret5 sign", ret5.apply(lambda x: 1 if x > 0 else -1), fwd))
    results.append(score("above MA200", (qqq > ma200).map({True: 1, False: -1}), fwd))
    results.append(score("above MA50", (qqq > ma50).map({True: 1, False: -1}), fwd))

    # vol voter: high realised vol -> bearish (the design's mapping)
    volmed = rvol.rolling(252).median()
    volvote = pd.Series(0.0, index=qqq.index)
    volvote[rvol > volmed * 1.2] = -1.0
    volvote[rvol < volmed * 0.8] = 1.0
    results.append(score("vol (high=bearish)", volvote, fwd))

    # ---- voters from stored LLM data --------------------------------------
    conn = pg()
    macro = pd.read_sql("""
        select doc->>'date_key' as date_key,
               doc->>'impact_direction' as dir,
               coalesce((doc->>'impact_strength')::float, 0) as strength
        from "GraphNexusNewsLLMMacro"
        where doc->>'date_key' is not null
    """, conn)
    if not macro.empty:
        macro["s"] = macro["dir"].map({"bullish": 1.0, "bearish": -1.0}).fillna(0.0)
        agg = macro.groupby("date_key").apply(
            lambda g: (g["s"] * g["strength"]).sum() / max(1e-9, g["strength"].sum()),
            include_groups=False)
        agg.index = pd.to_datetime(agg.index, errors="coerce")
        agg = agg[~agg.index.isna()].sort_index()
        agg = agg.reindex(qqq.index).ffill(limit=3)
        print(f"\nmacro_llm: {len(macro)} rows -> {agg.notna().sum()} aligned days")
        results.append(score("macro_llm (LLM direction)", agg, fwd))

    news = pd.read_sql("""
        select doc->>'date_key' as date_key,
               sum(coalesce((doc->>'positive_count')::float,0)) as pos,
               sum(coalesce((doc->>'negative_count')::float,0)) as neg
        from "GraphNexusNewsDayFeatures"
        where doc->>'date_key' is not null
        group by 1
    """, conn)
    conn.close()
    if not news.empty:
        news["b"] = (news["pos"] - news["neg"]) / (news["pos"] + news["neg"]).clip(lower=1)
        nb = news.set_index(pd.to_datetime(news["date_key"], errors="coerce"))["b"]
        nb = nb[~nb.index.isna()].sort_index().reindex(qqq.index).ffill(limit=3)
        print(f"news_breadth: {len(news)} day-rows -> {nb.notna().sum()} aligned days")
        results.append(score("news_breadth (pos-neg)", nb, fwd))

    # ---- verdict -----------------------------------------------------------
    print("\n" + "=" * 78)
    print("HIT RATES (non-overlapping, 95% Wilson interval)")
    print("=" * 78)
    print(f"  {'voter':<28}{'n':>6}{'p':>9}{'95% CI':>18}   verdict")
    print("  " + "-" * 74)
    passed = []
    for r in results:
        if r is None:
            continue
        ok = r["lo"] > THRESHOLD
        near = r["p"] >= THRESHOLD
        verdict = "PASS" if ok else ("p>=thr, CI overlaps" if near else "fail")
        if ok:
            passed.append(r["voter"])
        print(f"  {r['voter']:<28}{r['n']:>6}{r['p']:>9.4f}"
              f"   [{r['lo']:.3f}, {r['hi']:.3f}]   {verdict}")

    print("\n  baseline to beat (up-fraction): {:.4f}".format(up_frac))
    print("  pre-registered threshold:       {:.4f}".format(THRESHOLD))
    print("\n" + "=" * 78)
    if passed:
        print(f"RESULT: {len(passed)} voter(s) clear the bar with the CI entirely "
              f"above it:\n  " + "\n  ".join(passed))
    else:
        print("RESULT: NO voter clears p >= 0.58 with statistical confidence.")
        print("Per the pre-registered kill condition, the levered directional")
        print("council is not supported by the data.")
    print("=" * 78)


if __name__ == "__main__":
    main()
