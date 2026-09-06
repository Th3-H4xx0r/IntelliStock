"""Outlier sleeve feature table: pure feature math and the point-in-time reader.

Rows are written ONLY by scripts/build_outlier_features.py. The reader is
handed a store (db.store in production, the FakeStore fixture in tests) so
nothing here opens a connection or imports the pool.
"""
from __future__ import annotations

from datetime import date as _date, timedelta

FEATURES_TABLE = "OutlierUniverseFeatures"
PEERS_TABLE = "OutlierGraphPeers"

HI_BARS = 252
RET_BARS = 126
ADV_BARS = 20
SMA_BARS = 200
SMA_MIN_BARS = 180


def feature_id(date, symbol, dataset="") -> str:
    prefix = f"{dataset}|" if dataset else ""
    return f"{prefix}{str(date)[:10]}|{str(symbol).strip().upper()}"


def compute_features(closes, volumes, dates) -> list:
    """Trailing features for ONE symbol, one row per session, oldest first.

    Every window INCLUDES the session itself; a row for date d uses closes
    through d only, so a reader that only touches dates < today is PIT-safe by
    construction.
    """
    out = []
    n = len(closes)
    for i in range(n):
        c = float(closes[i])
        lo = max(0, i - HI_BARS + 1)
        hi = max(closes[lo:i + 1])
        ret = ((c / float(closes[i - RET_BARS]) - 1.0)
               if i >= RET_BARS and closes[i - RET_BARS] else None)
        a_lo = max(0, i - ADV_BARS + 1)
        adv = (sum(float(closes[j]) * float(volumes[j]) for j in range(a_lo, i + 1))
               / (i + 1 - a_lo))
        sma = (sum(float(x) for x in closes[i - SMA_BARS + 1:i + 1]) / SMA_BARS
               if i + 1 >= SMA_BARS else None)
        if sma is None and i + 1 >= SMA_MIN_BARS:
            w = closes[:i + 1][-SMA_MIN_BARS:]
            sma = sum(float(x) for x in w) / len(w)
        out.append({"date": str(dates[i])[:10], "close": c, "hi252": float(hi),
                    "ret126": ret, "adv20": adv, "sma200": sma,
                    "first_bar": str(dates[0])[:10], "n_bars": i + 1})
    return out


def rank_cross_section(rows, adv_min) -> list:
    """Attach `rs_rank` (0..1 percentile of ret126) among rows liquid enough to
    be candidates; everything else gets None. Mutates and returns `rows`."""
    liquid = [r for r in rows
              if r.get("ret126") is not None and float(r.get("adv20") or 0.0) >= adv_min]
    liquid.sort(key=lambda r: float(r["ret126"]))
    m = len(liquid)
    for r in rows:
        r["rs_rank"] = None
    for k, r in enumerate(liquid):
        r["rs_rank"] = (k / (m - 1)) if m > 1 else 1.0
    return rows


def cross_section(store, date, dataset="") -> list:
    """Every row for `date`. `|` sorts below `~`, so `[date|, date|~)` is the
    prefix; bytewise (COLLATE "C") on both stores."""
    d = (f"{dataset}|" if dataset else "") + str(date)[:10]
    return list(store.run(store.between(FEATURES_TABLE, f"{d}|", f"{d}|~")))


def visible_dates(store, before_date, lookback_days=10, dataset="") -> list:
    """Distinct session dates in [before - lookback, before), ascending."""
    b = _date.fromisoformat(str(before_date)[:10])
    lo = (b - timedelta(days=lookback_days)).isoformat()
    prefix = f"{dataset}|" if dataset else ""
    rows = store.run(store.between(FEATURES_TABLE, f"{prefix}{lo}|", f"{prefix}{b.isoformat()}|"))
    return sorted({str(r.get("date") or str(r.get("id", ""))[:10]) for r in rows})


def peers_for(store, symbols) -> dict:
    out = {}
    for s in symbols:
        doc = store.get(PEERS_TABLE, str(s).upper())
        if doc and doc.get("peers"):
            out[str(s).upper()] = [str(p).upper() for p in doc["peers"]]
    return out
