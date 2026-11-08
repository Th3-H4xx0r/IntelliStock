"""Free-source data for the forward test (no keys, no DB, no API of this repo).

  yfinance (auto_adjust=False):  SPY, IEF, MTUM, IWD, DBMF  -> "Adj Close" (total-return level)
                                 ^IRX                       -> "Close" (13-week T-bill discount yield, %)
  FRED via curl:                 DTB3 (fills ^IRX gaps), as the hunt's build_data.py did

Cash = the hunt's CASH_US construction: rate = ^IRX combine_first DTB3, floored at 0; session t earns the
rate published at close t-1, / 252.

    python3 fetch_data.py                 # refresh ./cache (gitignored), used by score_forward.py
    python3 fetch_data.py --out snapshot --end 2026-11-06    # the frozen reproduction snapshot

build_frame(dir) -> DataFrame on SPY's sessions from 2003-01-02, columns
    market (SPY), bond10 (IEF), MTUM, IWD, fund (DBMF), tbill (cash level)
truncated at the last session on which EVERY ETF has a real (not forward-filled) close.
"""
from __future__ import annotations

import argparse
import datetime as dt
import os
import subprocess
import sys

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
ETFS = {"SPY": "market", "IEF": "bond10", "MTUM": "MTUM", "IWD": "IWD", "DBMF": "fund"}
DL_START = "2002-01-01"
FRAME_START = "2003-01-02"


def _ny_now():
    try:
        from zoneinfo import ZoneInfo
        return dt.datetime.now(ZoneInfo("America/New_York"))
    except Exception:                                   # pragma: no cover
        return dt.datetime.utcnow() - dt.timedelta(hours=5)


def _drop_unsettled(df: pd.DataFrame) -> pd.DataFrame:
    """Drop a row dated today (New York) before 18:00 ET: an intraday quote, not a close."""
    now = _ny_now()
    today = pd.Timestamp(now.date())
    if now.hour < 18:
        df = df[df.index < today]
    return df


def fetch(out_dir: str, end: str | None = None) -> None:
    import yfinance as yf
    os.makedirs(out_dir, exist_ok=True)
    for t in list(ETFS) + ["^IRX"]:
        df = yf.download(t, start=DL_START, auto_adjust=False, actions=True, progress=False, threads=False)
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(0)
        if df is None or not len(df):
            raise RuntimeError(f"yfinance returned nothing for {t}")
        df.index = pd.DatetimeIndex(df.index).tz_localize(None).normalize()
        df = _drop_unsettled(df)
        if end is not None:
            df = df.loc[:pd.Timestamp(end)]
        keep = ["Close"] if t == "^IRX" else ["Adj Close", "Close"]
        df[keep].to_csv(os.path.join(out_dir, f"{t.replace('^', '')}.csv"), float_format="%.10g")
        print(f"yf {t}: {len(df)} rows {df.index[0].date()}..{df.index[-1].date()}", flush=True)
    path = os.path.join(out_dir, "DTB3.csv")
    tmp = path + ".tmp"
    subprocess.run(["curl", "-sS", "-m", "90", "--retry", "3", "-o", tmp,
                    "https://fred.stlouisfed.org/graph/fredgraph.csv?id=DTB3"], check=True)
    d = pd.read_csv(tmp, index_col=0, parse_dates=True)
    d = d.loc[DL_START:]
    if end is not None:
        d = d.loc[:pd.Timestamp(end)]
    d.to_csv(path)
    os.remove(tmp)
    print(f"fred DTB3: {len(d)} rows ..{d.index[-1].date()}", flush=True)


def _col(path, col):
    d = pd.read_csv(path, index_col=0, parse_dates=True)
    return pd.to_numeric(d[col], errors="coerce").dropna()


def build_frame(data_dir: str, end: str | None = None) -> pd.DataFrame:
    raw = {t: _col(os.path.join(data_dir, f"{t}.csv"), "Adj Close") for t in ETFS}
    cal = raw["SPY"].index
    cal = cal[cal >= pd.Timestamp(FRAME_START)]
    last_real = min(s.index[-1] for s in raw.values())          # every ETF has a real close here
    cal = cal[cal <= last_real]
    if end is not None:
        cal = cal[cal <= pd.Timestamp(end)]
    cols = {}
    for t, s in raw.items():                                    # lib.load: ffill(limit=5), NaN before first
        x = s.reindex(s.index.union(cal)).ffill(limit=5).reindex(cal)
        x[x.index < s.index[0]] = np.nan
        cols[ETFS[t]] = x
    # cash (hunt build_data.py CASH_US)
    irx = _col(os.path.join(data_dir, "IRX.csv"), "Close") / 100.0
    d = pd.read_csv(os.path.join(data_dir, "DTB3.csv"), index_col=0, parse_dates=True)
    dtb3 = pd.to_numeric(d.iloc[:, 0], errors="coerce").dropna() / 100.0
    rate = irx.combine_first(dtb3).clip(lower=0.0)
    r_on_cal = rate.reindex(rate.index.union(cal)).ffill().reindex(cal)
    cash_ret = (r_on_cal.shift(1) / 252.0).iloc[1:]
    tb = (1.0 + cash_ret.fillna(0.0)).cumprod()
    cols["tbill"] = pd.concat([pd.Series([1.0], index=cal[:1]), tb])
    P = pd.DataFrame(cols, index=cal)[["market", "bond10", "MTUM", "IWD", "fund", "tbill"]]
    for c in ("market", "bond10", "IWD", "tbill"):
        if P[c].isna().any():
            raise ValueError(f"{c} has gaps in the frame")
    return P


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=os.path.join(HERE, "cache"))
    ap.add_argument("--end", default=None, help="last date to keep (snapshot)")
    a = ap.parse_args()
    out = a.out if os.path.isabs(a.out) else os.path.join(HERE, a.out)
    fetch(out, a.end)
    P = build_frame(out)
    print(f"frame {P.index[0].date()}..{P.index[-1].date()} rows {len(P)}", file=sys.stderr)
