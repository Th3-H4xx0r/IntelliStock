"""FORWARD-TEST COPY of hunt/f14/engine.py (sha1 a514d70a22), rule code UNCHANGED. Edits (FROZEN.md A3):
  * `import lib` -> the frozen harness (decision_days only);
  * MOM, VALS slot names = the real ETFs F14 held in VAL: "MTUM" (VAL used MTUM from its 2013-04-18
    launch), "IWD" (VAL's value slot for the whole span);
  * _calendar_for: the calendar must be set with set_calendar() (the HIST / INTL_OLD lookups are gone).
The calendar passed in is the data's sessions extended with expected NYSE sessions (FROZEN.md A1).

--- original docstring ---
F14 engine = the F11 engine (f11/engine.py sha1 e036423d0a, copied verbatim; itself F9's engine + OFF-by-default
repairs, of which F14 uses only tc = cost-aware TOM) plus ONE new stream and a gross-priority rule:
  S  TSMOM / managed-futures stream: hold the column "fund" (f14/fund.py: the F13 overlay run as a fund with no
     core, i.e. a DBMF-like managed-futures ETF NAV) at unit weight xs, on margin. Shadow record: xs x (r_fund -
     r_cash) - (cash + 0.5%) spread on xs - 5 bps per unit of change, exactly as the other margin streams.
     Exists only where the fund column has data ("fund" in tradable).
  sa 1 -> S is ALWAYS on at unit size (not momentum-gated); otherwise S obeys the meta-rule like every stream.
  Gross priority (cap g = 2.0, C6): core equity E_core = 1 + sum of non-T equity overlays (L, D), clipped to
     [0, g]; then the fund min(w_S xs, g - E_core); then the TOM bump min(w_T tx, g - E_core - fund); then the
     bond trend min(bond, g - E - fund). With xs = 0 this is identical to F9/F11's combine (E clipped, bond
     trimmed), so xs = 0, tc = 0 reproduces F9 exactly.
Turning T off: uT = 0.

--- F11 docstring follows ---
F11 engine = the F9 engine (f9/engine.py sha1 9adfd098eb, copied verbatim) plus the round-7 repairs
declared in PREREG_F11.md. Every repair is OFF by default, so default params reproduce F9 exactly.

Round-7 repairs (p keys; default = off):
  bs  1 -> BOOK-LEVEL stress buffer (F4's): at a book decision, if 10-session realised vol of the market
         TR > vs (monthly rows: 3-month vol, the F8 translation), the WHOLE stacked book is replaced by
         sx x market (no leverage, no bond, no tilts, no TOM). sx default .7 (F4: 1 - f, f = .3).
  wk  1 -> weekly stress check: on W-WED sample sessions that are NOT book decision dates, if stress is on
         and the book is not already in stress mode, the book de-risks to sx x market. It never re-risks
         between decision dates (re-risk waits for the next TOM decision). Shadow records are untouched.
  g      gross cap on the combined book (market + tilts + bond); default 2.0 = F9. Bond trimmed first.
  dd  >0 -> overlay drawdown brake: at a book decision, if the market TR is more than dd below its
         trailing 252-session high (monthly: 12 rows), every POSITIVE overlay is removed (T bump, D, positive
         L, bond, tilts): exposure = 1 + w_L x min(dm_L, 0) in the index, no bond, no tilts.
  tx     TOM bump size (F7 x); default .5 = F9.
  tc  >0 -> cost-aware TOM: T is on (w_T = 1) iff its trailing lb-month shadow excess, re-charged at tc
         bps per unit of overlay change (shadow is kept at 5 bps; the extra (tc - 5) bps x units is
         deducted), is > 0. Applies under meta 1 (sign), and is the T rule even while T is immature
         (immature -> off). tc = 23 = R10's stress cost (a protocol constant, not tuned).

--- F9 docstring follows ---
F9 engine: era-adaptive ensemble of excess-return streams ("stream momentum").

One weights_fn, called on EVERY session (rebalance "D"), that
  1. keeps each excess stream in its already-published locked form, updated row by row from `hist`
     only (incremental state; on the first call it replays the history already in `hist`, so streams
     and their shadow track records warm up on pre-start data where a set has any);
  2. keeps a SHADOW track record of each stream at its locked unit size: the excess return the stream's
     overlay would have earned vs the 1.0x index core, decided on the book's decision dates, filled one
     session later (lag 1), financed at cash + 0.5%/yr on borrowed overlay, charged SHADOW_BPS per unit
     of overlay change;
  3. on the book's decision dates sets each stream's multiplier w_s from the stream's OWN trailing shadow
     record (the meta-rule) and returns the combined book.

Book decision dates (daily rows): the F7 turn-of-the-month dates only, day -3 (TOM bump on) and day +2
(bump off), i.e. at most 2 trade dates a month (C6 as scored for F7 under R9). Monthly rows (INTL_OLD_M):
every row. First call: always (empty book).

Streams (locked forms; constants, never perturbed):
  L  trend leverage + buffer + stress brake, F4r2_stress_xs equity part (= F8_A): u = mean of the last 8
     weekly (W-WED) samples of [market/tbill TR > its 200-session SMA]; overlay dm = .5u - .3(1-u);
     if 10-session realised vol > 30%: dm = -.3.
  B  bond trend, F4 form: +.4 x mean of last 8 weekly samples of [bond10 12m TR > tbill 12m TR] in bond10
     (on margin). Only where bond10 exists and is tradable.
  T  turn of the month, F7 form: dm = +.5 from the close of day -2 to the close of day +3 (decided day -3 /
     day +2); 0 under the stress brake (10-session vol > 30%). Daily rows only.
  D  crash-speed dip re-leverage, F8_E form: deep = market TR >= 20% below its 91-day high at some close in
     the last 182 days; turn = 30-day vol < 91-day vol AND a 30-day closing high; deep & turn on a weekly
     sample -> ON for 26 weekly samples, OFF early on a close below the lowest close of the 91 days before
     the trigger. Overlay = the increment over L: dm = max(0, (1 + .25) - (1 + dm_L)).
  M  crash-scaled momentum tilt, F6 form (tau .04): share = min(.5, .04 / TE_126(mom - market)) of the
     equity sleeve moved to the momentum portfolio. Only where the MOM slot exists.
  V  value tilt, F6 form (tau .04): share .04/.124 = .3226 of the equity sleeve moved to the value portfolio.
Monthly rows (INTL_OLD_M) use the F8 amendment translation for L and D (SMA 10 rows, 2 monthly tranches,
3-month stress vol, 3- vs 9-month vol, month-over-month high, 6 monthly ON rows); T, B, M, V do not exist.

Meta-rules (p["meta"]):
  0 static   w_s = 1 for every existing stream (control: the plain stack).
  1 sign     w_s = 1 if the stream's trailing `lb`-month shadow excess (sum of daily excess) > 0, else 0.
  2 ir       raw_s = max(0, IR_s) / TE_s over the trailing `lb` months (mean-variance weights for
             uncorrelated streams: risk allocated in proportion to IR); scaled by one constant so the
             combined shadow overlay's trailing TE equals `tau`; each w_s capped at `wmax`.
  3 erp      as ir but raw_s = 1 / TE_s for every mature stream (equal risk, no performance information):
             the control that isolates the performance-chasing part of `ir`.
  4 sign+erp as erp but only streams whose trailing `lb`-month shadow excess > 0 (raw_s = [sum > 0] / TE_s):
             binary factor momentum inside the same tau budget.
Stress threshold p["vs"] (default .30) is shared by L's brake and T's switch-off (the only stream constant
exposed as a free parameter, because it was the most sensitive one in F4/F6/F8).
A stream is MATURE once it has >= 12 months of shadow record. Immature streams ride at w = 1 (the locked
stack); `sign`/`ir`/`erp` act on mature streams only.
Combination: E = 1 + sum w_s dm_s, clipped to [0, 2]; tilt shares sum w_s*share_s (<= 1) carved out of E;
bond = w_B * b; gross = E + bond trimmed to <= 2.0 by cutting the bond first.
"""
from __future__ import annotations

import bisect
import math

import numpy as np
import pandas as pd

from harness import decision_days

SHADOW_BPS = 5.0          # R10 primary cost, used inside the shadow records
SPREAD = 0.005            # financing spread on borrowed overlay (lib.FIN_SPREAD)
MOM, VALS = "MTUM", "IWD"
STREAMS = ("L", "B", "T", "D", "M", "V", "S")
NCOL = 6                  # market, tbill, bond10, MOM, VALS, fund
MATURE_MONTHS = 12

# ---------------------------------------------------------------- calendar registry (as f7/tom.py)
_CAL = {"idx": None}


def cal_positions(idx):
    idx = pd.DatetimeIndex(idx)
    last = idx[-1]
    me = last + pd.offsets.MonthEnd(0)
    if last < me:
        idx = idx.append(pd.bdate_range(last + pd.Timedelta(days=1), me))
    per = idx.to_period("M")
    s = pd.Series(1, index=idx)
    ks = s.groupby(per).cumsum().values
    ke = s.iloc[::-1].groupby(per[::-1]).cumsum().iloc[::-1].values
    return pd.DataFrame({"ks": ks, "ke": ke}, index=idx)


def set_calendar(idx):
    _CAL["idx"] = None if idx is None else pd.DatetimeIndex(idx)


def _calendar_for(hist):
    if _CAL["idx"] is not None:
        return _CAL["idx"]
    raise RuntimeError("set_calendar() must be called first (forward-test copy)")


_POS_CACHE = {}


def _sched(cal):
    key = (len(cal), cal[0], cal[-1])
    if key not in _POS_CACHE:
        pos = cal_positions(cal)
        wk = decision_days(cal, "W-WED")
        _POS_CACHE[key] = (pos, set(cal[wk]))
    return _POS_CACHE[key]


# ---------------------------------------------------------------- helpers
def _vol_log(p, ann):
    r = np.diff(np.log(p))
    r = r[np.isfinite(r)]
    if len(r) < 3:
        return None
    return float(np.std(r, ddof=1) * math.sqrt(ann))


class Engine:
    def __init__(self, p):
        self.p = p
        self.meta = int(p.get("meta", 0))
        self.lb = float(p.get("lb", 36))
        self.tau = float(p.get("tau", 0.06))
        self.wmax = float(p.get("wmax", 2.0))
        self.vs = float(p.get("vs", 0.30))
        self.use = {s for s in STREAMS if int(p.get("u" + s, 1))}
        # F11 repairs
        self.bs = int(p.get("bs", 0))
        self.wkchk = int(p.get("wk", 0))
        self.sx = float(p.get("sx", 0.7))
        self.g = float(p.get("g", 2.0))
        self.dd = float(p.get("dd", 0.0))
        self.tx = float(p.get("tx", 0.5))
        self.tc = float(p.get("tc", 0.0))
        # F14
        self.xs = float(p.get("xs", 0.0))
        self.sa = int(p.get("sa", 0))
        self.stress_book = False
        self.cu = {s: [] for s in STREAMS}       # cost units charged on each shadow row
        self.book_log = []                       # (date, kind) kind in {normal, stress, wkstress, ddbrake}
        self._fill_cost = {}
        self.n = 0
        cap = 4096
        self.D = np.empty(cap, dtype="datetime64[ns]")
        self.X = np.full((cap, NCOL), np.nan)     # market, tbill, bond10, MOM, VALS, fund
        self.cols = None
        self.monthly = None
        # stream state
        self.uL = []; self.uB = []
        self.dip_left = 0; self.dip_stop = None
        self.in_tom = False
        # shadow
        self.ov_hist = []          # (row index of decision, {stream: overlay dict})
        self.cur_ov = {}           # overlay active for shadow (decided <= j-2)
        self.pending = []          # decisions waiting to become active
        self.sh = {s: [] for s in STREAMS}     # shadow daily excess (np.nan when stream absent)
        self.sh_d = []             # dates (int64 ns) of shadow rows
        self.weights_log = []      # (date, {s: w}) at book decisions

    # ------------------------------------------------------------ storage
    def _grow(self):
        cap = len(self.D) * 2
        D = np.empty(cap, dtype="datetime64[ns]"); D[:self.n] = self.D[:self.n]
        X = np.full((cap, NCOL), np.nan); X[:self.n] = self.X[:self.n]
        self.D, self.X = D, X

    def _append(self, dates, vals):
        for d, v in zip(dates, vals):
            if self.n >= len(self.D):
                self._grow()
            self.D[self.n] = d
            self.X[self.n] = v
            self.n += 1

    # ------------------------------------------------------------ per-row quantities
    def _ppy(self, i):
        k = min(i, 600)
        if k < 2:
            return 252.0
        days = (self.D[i] - self.D[i - k]) / np.timedelta64(1, "D")
        return k / max(days / 365.25, 1e-9)

    def _ret_over(self, c, i, months):
        """Trailing calendar-month return of column c at row i (F6 ret_over)."""
        t0 = self.D[i] - np.timedelta64(int(round(months * 30.44)), "D")
        k = int(np.searchsorted(self.D[:i + 1], t0, side="right")) - 1
        if k < 0 or not np.isfinite(self.X[k, c]) or not np.isfinite(self.X[i, c]):
            return None
        return self.X[i, c] / self.X[k, c] - 1.0

    def _stress(self, i):
        if self.monthly:
            p = self.X[max(0, i - 3):i + 1, 0]
            if len(p) < 4:
                return False
            v = _vol_log(p, 12.0)
        else:
            p = self.X[max(0, i - 10):i + 1, 0]
            if len(p) < 6:
                return False
            days = (self.D[i] - self.D[max(0, i - 10)]) / np.timedelta64(1, "D")
            v = _vol_log(p, (len(p) - 1) / max(days / 365.25, 1e-9))
        return v is not None and v > self.vs

    def _sample_L(self, i):
        n = 10 if self.monthly else 200
        t = 2 if self.monthly else 8
        if i + 1 < n:
            return
        x = self.X[i - n + 1:i + 1, 0] / self.X[i - n + 1:i + 1, 1]
        if not np.all(np.isfinite(x)):
            return
        self.uL.append(float(x[-1] > x.mean())); del self.uL[:-t]

    def _sample_B(self, i):
        if self.cols[2] is None or not np.isfinite(self.X[i, 2]):
            return
        rb = self._ret_over(2, i, 12)
        rt = self._ret_over(1, i, 12)
        if rb is None or rt is None:
            return
        self.uB.append(float(rb > rt)); del self.uB[:-8]

    def _dm_L(self, i, stress):
        if not self.uL:
            return 0.0
        u = sum(self.uL) / len(self.uL)
        if stress:
            return -0.3
        return 0.5 * u - 0.3 * (1.0 - u)

    def _sample_D(self, i):
        """F8_E dip state machine, one weekly sample (or one monthly row)."""
        now = self.D[i]
        lo = int(np.searchsorted(self.D[:i + 1], now - np.timedelta64(365 + 182 + 7, "D")))
        px = self.X[lo:i + 1, 0]; dd = self.D[lo:i + 1]
        ok = np.isfinite(px)
        px, dd = px[ok], dd[ok]
        if self.monthly:
            if len(px) < 13 or i < 9:
                return
        else:
            if len(px) <= 30 or (dd[-1] - dd[0]) / np.timedelta64(1, "D") < 365 + 150:
                return
        s = pd.Series(px, index=pd.DatetimeIndex(dd))
        rel = s / s.rolling("91D").max() - 1.0
        deep = float(rel[rel.index >= pd.Timestamp(now) - pd.Timedelta(days=182)].min()) <= -0.20
        if self.monthly:
            m = self.X[:i + 1, 0]
            hi = m[-1] > m[-2]
            def sd(k):
                r = np.diff(np.log(m[-(k + 1):]))
                return float(np.std(r, ddof=1) * math.sqrt(12)) if len(r) == k else None
            v3, v9 = sd(3), sd(9)
            vf = v3 is not None and v9 is not None and v3 < v9
        else:
            l30 = s[s.index >= pd.Timestamp(now) - pd.Timedelta(days=30)]
            hi = s.iloc[-1] >= l30.max() - 1e-12
            def vd(days):
                q = s[s.index >= pd.Timestamp(now) - pd.Timedelta(days=days)]
                if len(q) < 8:
                    return None
                r = np.diff(np.log(q.values)); r = r[np.isfinite(r)]
                span = (q.index[-1] - q.index[0]).days
                if len(r) < 5 or span <= 0:
                    return None
                return float(np.std(r, ddof=1) * math.sqrt(len(r) / (span / 365.25)))
            v30, v91 = vd(30), vd(91)
            vf = v30 is not None and v91 is not None and v30 < v91
        turn = vf and hi
        if self.dip_left > 0:
            self.dip_left -= 1
            if self.dip_stop is not None and s.iloc[-1] < self.dip_stop:
                self.dip_left = 0
        if deep and turn:
            if self.dip_left <= 0:
                self.dip_stop = float(s[s.index >= pd.Timestamp(now) - pd.Timedelta(days=91)].min())
            self.dip_left = 6 if self.monthly else 26

    def _mom_share(self, i):
        nn = 6 if self.monthly else 126
        if i < nn + 1:
            return None
        a = self.X[i - nn:i + 1, 3]; m = self.X[i - nn:i + 1, 0]
        if not (np.all(np.isfinite(a)) and np.all(np.isfinite(m))):
            return None
        rel = np.diff(a) / a[:-1] - np.diff(m) / m[:-1]
        te = float(np.std(rel, ddof=1) * math.sqrt(self._ppy(i)))
        return min(0.5, 0.04 / te) if te > 0 else 0.5

    # ------------------------------------------------------------ overlays at a decision row
    def _overlays(self, i, tradable):
        st = self._stress(i)
        out = {}
        dmL = self._dm_L(i, st)
        if "L" in self.use and self.uL:
            out["L"] = {"m": dmL}
        if "B" in self.use and self.cols[2] is not None and "bond10" in tradable and self.uB \
                and np.isfinite(self.X[i, 2]):
            out["B"] = {"b": 0.4 * sum(self.uB) / len(self.uB)}
        if "T" in self.use and not self.monthly:
            out["T"] = {"m": self.tx if (self.in_tom and not st) else 0.0}
        if "D" in self.use and self.dip_left is not None:
            on = self.dip_left > 0
            out["D"] = {"m": max(0.0, 0.25 - dmL) if on else 0.0}
        if "M" in self.use and self.cols[3] is not None and self.cols[3] in tradable:
            sh = self._mom_share(i)
            if sh is not None:
                out["M"] = {"mom": sh}
        if "V" in self.use and self.cols[4] is not None and self.cols[4] in tradable \
                and np.isfinite(self.X[i, 4]):
            out["V"] = {"val": 0.04 / 0.124}
        if "S" in self.use and self.xs > 0 and self.cols[5] is not None and "fund" in tradable \
                and np.isfinite(self.X[i, 5]):
            out["S"] = {"f": self.xs}
        return out

    # ------------------------------------------------------------ shadow accounting for row j
    def _shadow_row(self, j):
        while self.pending and self.pending[0][0] <= j - 2:
            _, ov = self.pending.pop(0)
            self.cur_ov = ov
        gap = 1.0
        r = np.full(NCOL, np.nan)
        if j > 0:
            gap = (self.D[j] - self.D[j - 1]) / np.timedelta64(1, "D")
            r = self.X[j] / self.X[j - 1] - 1.0
        sp = SPREAD * gap / 365.0
        self.sh_d.append(self.D[j].astype("int64"))
        c = SHADOW_BPS / 1e4
        for s in STREAMS:
            fc = self._fill_cost.get(j)
            self.cu[s].append(fc.get(s, 0.0) if fc is not None else 0.0)
            o = self.cur_ov.get(s)
            if o is None or j == 0:
                self.sh[s].append(np.nan); continue
            e = 0.0
            if "m" in o:
                e += o["m"] * (r[0] - r[1]) - sp * max(o["m"], 0.0)
            if "b" in o:
                e += o["b"] * (r[2] - r[1]) - sp * o["b"] if o["b"] > 0 else 0.0
            if "mom" in o:
                e += o["mom"] * (r[3] - r[0])
            if "val" in o:
                e += o["val"] * (r[4] - r[0])
            if "f" in o:
                e += o["f"] * (r[5] - r[1]) - sp * o["f"]
            # cost on the fill row (decision row + 1)
            if self._fill_cost.get(j) is not None:
                e -= c * self._fill_cost[j].get(s, 0.0)
            self.sh[s].append(e if np.isfinite(e) else np.nan)

    def _record_decision(self, i, ov):
        prev = self.ov_hist[-1][1] if self.ov_hist else {}
        chg = {}
        for s in STREAMS:
            a, b = prev.get(s, {}), ov.get(s, {})
            chg[s] = sum(abs(b.get(k, 0.0) - a.get(k, 0.0)) for k in set(a) | set(b))
        self._fill_cost[i + 1] = chg
        self.ov_hist.append((i, ov))
        self.pending.append((i, ov))

    # ------------------------------------------------------------ meta-rule
    def _trailing(self, s, i):
        d = self.sh_d
        t_end = self.D[i].astype("int64")
        t0 = (self.D[i] - np.timedelta64(int(round(self.lb * 30.44)), "D")).astype("int64")
        k = bisect.bisect_right(d, t0)
        x = np.asarray(self.sh[s][k:], dtype=float)
        return x, d[k:]

    def _t_costaware(self, i):
        """1.0 iff T's trailing lb-month shadow excess, re-charged at tc bps per unit of change, > 0."""
        x, _ = self._trailing("T", i)
        k = len(self.sh["T"]) - len(x)
        u = np.asarray(self.cu["T"][k:], dtype=float)
        ok = np.isfinite(x)
        if not ok.any():
            return 0.0
        net = float(x[ok].sum() - (self.tc - SHADOW_BPS) / 1e4 * u[ok].sum())
        return 1.0 if net > 0 else 0.0

    def _dd_brake(self, i):
        if self.dd <= 0:
            return False
        n = 12 if self.monthly else 252
        px = self.X[max(0, i - n + 1):i + 1, 0]
        px = px[np.isfinite(px)]
        if len(px) < 2:
            return False
        return px[-1] / px.max() - 1.0 < -self.dd

    def _mature(self, s, i):
        x = self.sh[s]
        n_ok = 0
        # months of non-NaN record (count rows, convert with ppy)
        arr = np.asarray(x, dtype=float)
        n_ok = int(np.isfinite(arr).sum())
        return n_ok / max(self._ppy(i), 1.0) * 12.0 >= MATURE_MONTHS

    def _meta(self, i, ov):
        names = list(ov)
        w = {s: 1.0 for s in names}
        if self.meta == 0:
            return w
        mature = [s for s in names if self._mature(s, i)]
        if self.meta == 1 and self.tc > 0 and "T" in names and "T" not in mature:
            w["T"] = 0.0
        if not mature:
            return w          # immature streams ride at w = 1 (S included; sa keeps S at 1 anyway)
        ppy = self._ppy(i)
        if self.meta == 1:
            for s in mature:
                x, _ = self._trailing(s, i)
                x = x[np.isfinite(x)]
                w[s] = 1.0 if (len(x) and x.sum() > 0) else 0.0
            if self.tc > 0 and "T" in names:
                w["T"] = self._t_costaware(i) if "T" in mature else 0.0
            if self.sa and "S" in names:
                w["S"] = 1.0
            return w
        # ir / erp: risk budget tau over mature streams
        series, raw = {}, {}
        for s in mature:
            x, _ = self._trailing(s, i)
            series[s] = x
            xf = x[np.isfinite(x)]
            if len(xf) < 20:
                raw[s] = 0.0; continue
            te = float(np.std(xf, ddof=1) * math.sqrt(ppy))
            if te <= 1e-9:
                raw[s] = 0.0; continue
            if self.meta == 2:
                irs = float(np.mean(xf) * ppy / te)
                raw[s] = max(0.0, irs) / te
            elif self.meta == 4:
                raw[s] = (1.0 / te) if xf.sum() > 0 else 0.0
            else:
                raw[s] = 1.0 / te
        if sum(raw.values()) <= 0:
            for s in mature:
                w[s] = 0.0
            return w
        L = min(len(v) for v in series.values())
        M = np.column_stack([series[s][-L:] for s in mature])
        comb = np.nan_to_num(M) @ np.array([raw[s] for s in mature])
        te_c = float(np.std(comb, ddof=1) * math.sqrt(ppy)) if L > 20 else 0.0
        scale = self.tau / te_c if te_c > 1e-12 else 0.0
        for s in mature:
            w[s] = min(self.wmax, raw[s] * scale)
        return w

    def _combine(self, ov, w, tradable, brake=False):
        E = 1.0
        bond = 0.0; ms = 0.0; vs = 0.0
        for s, o in ov.items():
            ws = w.get(s, 0.0)
            E += ws * o.get("m", 0.0)
            bond += ws * o.get("b", 0.0)
            ms += ws * o.get("mom", 0.0)
            vs += ws * o.get("val", 0.0)
        if brake:
            # overlay brake: drop every positive overlay (T bump, D, positive L, bond, tilts);
            # keep only the negative trend overlay
            E = 1.0 + sum(w.get(s, 0.0) * min(o.get("m", 0.0), 0.0) for s, o in ov.items())
            bond = 0.0; ms = 0.0; vs = 0.0
        gcap = min(2.0, self.g)
        # F14 gross priority: E_core (all equity overlays except T), fund, T bump, bond
        tb = w.get("T", 0.0) * ov["T"].get("m", 0.0) if ("T" in ov and not brake) else 0.0
        fund = w.get("S", 0.0) * ov["S"].get("f", 0.0) if ("S" in ov and not brake) else 0.0
        E_core = min(max(E - tb, 0.0), gcap)
        fund = max(0.0, min(fund, gcap - E_core))
        tb = max(0.0, min(tb, gcap - E_core - fund))
        E = E_core + tb
        tot = ms + vs
        if tot > 1.0:
            ms, vs = ms / tot, vs / tot
        bond = max(0.0, min(bond, gcap - E - fund))
        out = {"market": max(0.0, E * (1.0 - ms - vs))}
        if ms > 0:
            out[self.cols[3]] = E * ms
        if vs > 0:
            out[self.cols[4]] = E * vs
        if bond > 1e-9:
            out["bond10"] = bond
        if fund > 1e-9:
            out["fund"] = fund
        return out

    # ------------------------------------------------------------ main
    def __call__(self, hist):
        first = self.cols is None
        if first:
            cols = list(hist.columns)
            self.cols = ["market", "tbill",
                         "bond10" if "bond10" in cols else None,
                         MOM if MOM in cols else None,
                         VALS if VALS in cols else None,
                         "fund" if "fund" in cols else None]
            self.monthly = str(hist.attrs.get("freq", "D")).upper().startswith("M")
            if not self.monthly:
                cal = _calendar_for(hist)
                self.pos, self.wk = _sched(cal)
        new = hist.iloc[self.n:]
        vals = np.full((len(new), NCOL), np.nan)
        for k, c in enumerate(self.cols):
            if c is not None:
                vals[:, k] = new[c].to_numpy(dtype=float)
        start = self.n
        self._append(new.index.values, vals)
        tradable = hist.attrs.get("tradable", set())
        decision = None
        wk_live = False
        for i in range(start, self.n):
            live = (i == self.n - 1)
            self._shadow_row(i)
            # stream state updates on native sample days
            d = pd.Timestamp(self.D[i])
            if self.monthly:
                samp, dec = True, True
            else:
                samp = d in self.wk
                try:
                    ks, ke = self.pos.at[d, "ks"], self.pos.at[d, "ke"]
                except KeyError:
                    ks = ke = -1
                dec = False
                if ke == 3:
                    self.in_tom = True; dec = True
                elif ks == 2:
                    self.in_tom = False; dec = True
                if first and live and not dec:
                    self.in_tom = (ke <= 2 and ke > 0) or (0 < ks <= 1)
                    dec = True
            if samp:
                self._sample_L(i)
                self._sample_B(i)
                if "D" in self.use:
                    self._sample_D(i)
            if self.monthly and first and live:
                dec = True
            if dec:
                trad_i = tradable if live else self._tradable_hist(i)
                ov = self._overlays(i, trad_i)
                self._record_decision(i, ov)
                if live:
                    decision = (i, ov, trad_i)
            elif live and samp and self.wkchk and not self.monthly:
                wk_live = True
        if decision is None:
            # F11 weekly stress check (de-risk only; never re-risks between decision dates)
            if wk_live and self.bs and not self.stress_book and self._stress(self.n - 1):
                self.stress_book = True
                self.book_log.append((pd.Timestamp(self.D[self.n - 1]), "wkstress"))
                return {"market": self.sx}
            return None
        i, ov, trad = decision
        w = self._meta(i, ov)
        self.weights_log.append((pd.Timestamp(self.D[i]), dict(w)))
        if self.bs and self._stress(i):
            self.stress_book = True
            self.book_log.append((pd.Timestamp(self.D[i]), "stress"))
            return {"market": self.sx}
        self.stress_book = False
        brake = self._dd_brake(i)
        self.book_log.append((pd.Timestamp(self.D[i]), "ddbrake" if brake else "normal"))
        return self._combine(ov, w, trad, brake=brake)

    def _tradable_hist(self, i):
        """Tradability for a replayed (pre-first-call) decision: a column with data at that row.
        Only used for the shadow record. DEV/VAL frames: the MOM/VALS slots and bond10 are holdable
        whenever they have data (R1/R6 stand-ins); market is SPY_ext (holdable from 1993-01-29, but a
        shadow overlay on the index before that is the index itself: allowed for the record)."""
        out = {"market", "tbill"}
        for k, c in enumerate(self.cols[2:], start=2):
            if c is not None and np.isfinite(self.X[i, k]):
                out.add(c)
        return out


def make_f9(p):
    return Engine(dict(p))
