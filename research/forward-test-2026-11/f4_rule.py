"""F4r2_stress_xs rule -- FORWARD-TEST COPY (no edits to rule code).
Helpers vol / sma_up / Tr / mom_vs_bill copied verbatim from hunt/f4/f4core.py (sha1 c149a7164b);
make_r2 copied verbatim from hunt/f4/f4core2.py (sha1 eec5ea1fb9); the spec constants from
hunt/f4/specs_r2.py (FIXED = {t: 8, vsn: 10}; P = {k .5, f .3, b .4, vs .30, sma 200}; xs = 1).

F4r2_stress_xs: expo = 1 + k*u - f*(1-u) on `market`; if 10-session realised vol > vs: leverage off and
exposure 1 - f; u = mean of the last 8 weekly samples of [market/tbill > its 200-session SMA];
+ b * [mean of last 8 weekly samples of (bond10 12m TR > T-bill 12m TR)] in bond10 (on margin).
Rebalance W-WED (decided on the last session of each Wednesday-ending week), fill lag 1.
"""
import math
import numpy as np


def vol(px, n):
    p = px.iloc[-(n + 1):]
    r = np.diff(np.log(p.values))
    r = r[np.isfinite(r)]
    if len(r) < max(10, n // 2):
        return None
    days = (p.index[-1] - p.index[0]).days
    per_year = len(r) / max(days / 365.25, 1e-9)
    return float(np.std(r, ddof=1) * math.sqrt(per_year))


def sma_up(px, n):
    if len(px) < n:
        return None
    return float(px.iloc[-1] > px.iloc[-n:].mean())


class Tr:
    def __init__(self, k):
        self.k = max(1, int(round(k))); self.s = []

    def push(self, x):
        if x is None:
            return None
        self.s.append(x); del self.s[:-self.k]
        return sum(self.s) / len(self.s)


def mom_vs_bill(hist, col, months=12):
    """Trailing `months` TR of `col` minus that of T-bills; None if not enough data."""
    s = hist[col].dropna()
    if len(s) < 30:
        return None
    t = s.index[-1]
    t0 = t - np.timedelta64(int(round(months * 30.44)), "D")
    if s.index[0] > t0:
        return None
    k = s.index.searchsorted(t0, side="right") - 1
    tb = hist["tbill"]
    kb = tb.index.searchsorted(s.index[k], side="right") - 1
    return float(s.iloc[-1] / s.iloc[k] - tb.iloc[-1] / tb.iloc[kb])


def make_r2(p):
    """expo = 1 + k*L - f*D (+ b*bond trend)
      u    = avg_t of the weekly trend samples (trend = market [or market/tbill if xs] > SMA_n)
      L    = leverage signal: asym=1 -> min(u, trend_now) (enter gradually, exit at once); else u
             times [10d vol < vb] if vb given (fast crash brake, untranched)
      D    = buffer signal: (1-u); if dimm=1 -> max(1-u, 1-trend_now) (engage at once)
    """
    k = p.get("k", 0.0); f_ = p.get("f", 0.0); b = p.get("b", 0.0)
    n = int(round(p.get("sma", 200))); t = p.get("t", 8)
    xs = p.get("xs", 0); asym = p.get("asym", 0); dimm = p.get("dimm", 0); vb = p.get("vb")
    bmon = p.get("bmon", 12)
    tr = Tr(t); trb = Tr(t)
    def w(hist):
        if xs:
            x = (hist["market"] / hist["tbill"]).iloc[-(n + 30):].dropna()
        else:
            x = hist["market"].iloc[-(n + 30):].dropna()
        s = sma_up(x, n)
        u = tr.push(s)
        if u is None:
            expo = 1.0
        else:
            L = min(u, s) if asym else u
            if vb is not None:
                v = vol(hist["market"].iloc[-15:].dropna(), 10)
                if v is not None and v > vb:
                    L = 0.0
            D = max(1.0 - u, 1.0 - s) if dimm else 1.0 - u
            if p.get("bfast"):
                D = min(1.0 - u, 1.0 - s)      # buffer ends as soon as the trend is back up
            vs = p.get("vs")
            if vs is not None:
                v2 = vol(hist["market"].iloc[-(int(p.get("vsn", 21)) + 5):].dropna(), int(p.get("vsn", 21)))
                if v2 is not None and v2 > vs:
                    L = 0.0; D = 1.0
            expo = 1.0 + k * L - f_ * D
        out = {"market": expo}
        if b > 0 and "bond10" in hist.columns and "bond10" in hist.attrs.get("tradable", set()):
            m = mom_vs_bill(hist, "bond10", bmon)
            bs = trb.push(None if m is None else float(m > 0))
            if bs:
                out["bond10"] = b * bs
        return out
    return w


FIXED = {"t": 8, "vsn": 10}
P = {"k": 0.5, "f": 0.3, "b": 0.4, "vs": 0.30, "sma": 200}
PARAMS = {**FIXED, **P, "xs": 1}
UNIVERSE = ["market", "bond10"]
REBALANCE = "W-WED"


def make():
    """A fresh F4r2_stress_xs weights_fn (specs_r2._xs)."""
    return make_r2(dict(PARAMS))
