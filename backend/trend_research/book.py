"""TR book rules and trailing stop (spec §5.3-5.7, C3-C6) -- pure functions.

The research ledger row says what the research thinks (notes, a proposed book,
recommended names); this module turns it into target weights the lane can
trade. No DB, no network, no clock: the same row, holdings and bar check give
the same weights, which is what lets a backtest re-run with different rules
cost no AI calls (G5).

Rules, in the order ``target_book`` applies them:

1. Every symbol is normalized; one that will not parse, is an ETF outside the
   allowed sets, or has no bars at the decision date is dropped with a reason.
   A pick that is not an allowed ETF and has no research note this week is
   dropped too ("no research note"): only the research stage screens names
   (R7: the EDGAR "is it a stock?" check), so an unresearched pick -- a 2x ETF
   missing from ``KNOWN_THEME_ETFS``, a hallucinated ticker -- never reaches
   the book.
2. A holding is kept unless its latest note says ``thesis_status ==
   "broken"``, it is absent from the row's notes, recommended names and
   picks, or the proposal picks it at weight 0. A new pick at weight 0 is not
   a buy candidate.
3. Slots (``max_positions``, clamped to 8-12) go first to the proposal's
   defensive picks when ``defensive_share > 0``, then to kept holdings, then
   to new picks by conviction. A new pick displaces the weakest holding only
   when its conviction is at least the holding's plus ``swap_margin``. A new
   pick dropped for having no bars leaves its slot to the holdings: a holding
   that would otherwise leave only for being absent from the row or for want
   of a slot is kept instead (one per such pick). A broken thesis or a
   proposal weight of 0 still sells.
4. Weights: proposal target weights (a kept holding the proposal did not pick
   gets ``1 / max_positions``), each clipped to ``max_single_weight``; the
   non-defensive names are scaled into ``1 - defensive_share``; that share is
   split across the defensive picks pro-rata to their target weights (BIL if
   there are none); leveraged + inverse names together are scaled down to
   ``max_levered_inverse_weight``. BIL is the remainder, so the weights always
   sum to 1.0. Weights below ``WEIGHT_FLOOR`` are float residue, not targets:
   they are snapped to 0 (``snap_weights``).
"""
from __future__ import annotations

import math
import re
from typing import Callable, Iterable

BIL = "BIL"

COMMODITY_ETFS = frozenset({"GLD", "SLV", "USO", "XLE", "CPER", "URA"})
DEFENSIVE_ETFS = frozenset({"BIL", "TLT", "XLU", "XLP"})
INVERSE_ETFS = frozenset({"SH", "SQQQ"})
LEVERAGED_ETFS = frozenset({"TQQQ", "SOXL"})
ALL_ALLOWED_ETFS = COMMODITY_ETFS | DEFENSIVE_ETFS | INVERSE_ETFS | LEVERAGED_ETFS

# ETFs the research may name but the lane must not buy (§5.3 "other theme ETFs
# are rejected"). A static list: a ticker that is an ETF but missing here would
# be screened as a stock, so the list leans broad -- index, sector, theme,
# country, bond, commodity look-alikes, and leveraged/inverse products outside
# the allowed sets (those would otherwise slip past the 20% cap).
KNOWN_THEME_ETFS = frozenset({
    # broad index / style
    "SPY", "VOO", "IVV", "VTI", "ITOT", "QQQ", "QQQM", "DIA", "IWM", "IWB", "IWF",
    "IWD", "IWN", "IWO", "IJH", "IJR", "MDY", "RSP", "VTV", "VUG", "VB", "VO",
    "VEA", "VWO", "EFA", "EEM", "IEFA", "IEMG", "ACWI", "VT", "VXUS", "SCHB",
    "SCHX", "SCHG", "SCHD", "VYM", "DVY", "VIG", "NOBL", "USMV", "SPLV", "MTUM",
    "QUAL", "SPHB", "SPYG", "SPYV", "OEF", "XMHQ",
    # sectors
    "XLK", "XLF", "XLV", "XLI", "XLB", "XLY", "XLC", "XLRE", "VGT", "VHT",
    "VFH", "VDE", "VNQ", "IYR", "IYT", "IYW", "IGV", "KRE", "KBE", "KBWB",
    "XHB", "ITB", "XRT", "XME", "XOP", "OIH", "XAR", "PPA", "ITA", "XTN",
    # themes
    "SMH", "SOXX", "XSD", "IBB", "XBI", "ARKK", "ARKG", "ARKW", "ARKQ",
    "ARKF", "ARKX", "TAN", "ICLN", "QCLN", "PBW", "LIT", "BATT", "BOTZ",
    "ROBO", "AIQ", "IRBO", "HACK", "CIBR", "BUG", "SKYY", "CLOU", "WCLD",
    "FINX", "IPAY", "JETS", "PAVE", "UFO", "BLOK", "MSOS", "ESPO", "HERO",
    "DRIV", "IDRV", "KWEB", "CQQQ", "FDN", "PEJ", "PBJ", "MOO", "GRID",
    "NLR", "URNM", "REMX", "COPX", "PICK", "GDX", "GDXJ", "SIL", "SILJ",
    "XLG", "MAGS", "IGM", "CHAT", "BITQ",
    # country
    "FXI", "MCHI", "ASHR", "EWJ", "EWZ", "EWW", "INDA", "EPI", "EWT", "EWY",
    "EWG", "EWU", "EWC", "EWA", "EWH", "EWS", "EZA", "TUR", "ARGT", "VGK",
    "EZU", "HEDJ", "DXJ",
    # bonds / cash look-alikes
    "IEF", "SHY", "IEI", "AGG", "BND", "LQD", "HYG", "JNK", "TIP", "SGOV",
    "SHV", "GOVT", "VGLT", "VGIT", "VGSH", "EDV", "ZROZ", "TLH", "MUB",
    "BNDX", "EMB", "USFR", "TFLO", "JPST", "MINT",
    # commodity look-alikes outside the allowed set
    "IAU", "SGOL", "GLDM", "PPLT", "PALL", "SIVR", "UNG", "USL", "BNO",
    "DBA", "DBC", "PDBC", "GSG", "DBO", "CORN", "WEAT", "SOYB", "UUP",
    "FXE", "FXY",
    # crypto
    "BITO", "IBIT", "FBTC", "GBTC", "ETHE", "ETHA", "ARKB", "BITB",
    # leveraged / inverse / volatility outside the allowed sets
    "UPRO", "SPXL", "SSO", "QLD", "TNA", "TZA", "SPXU", "SPXS", "SDS", "PSQ",
    "QID", "SDOW", "UDOW", "DOG", "DXD", "RWM", "SOXS", "TECL", "TECS", "FAS",
    "FAZ", "LABU", "LABD", "NUGT", "DUST", "JNUG", "JDST", "ERX", "ERY",
    "GUSH", "DRIP", "BOIL", "KOLD", "UCO", "SCO", "UVXY", "SVXY", "VXX",
    "VIXY", "SVIX", "YINN", "YANG", "FNGU", "FNGD", "BULZ", "NVDL", "NVDU",
    "TSLL", "TSLQ", "CURE", "DFEN", "UGL", "GLL", "AGQ", "ZSL", "TMF", "TMV",
    "TBT", "TBF", "UBT", "UST", "DPST", "WEBL", "WEBS", "HIBL", "HIBS",
    "MIDU", "URTY", "SRTY", "SPDN", "QQQU", "CONL", "MSTU", "MSTX",
})

DEFAULTS = {
    "max_positions": 12,
    "swap_margin": 2.0,
    "max_single_weight": 0.25,
    "max_levered_inverse_weight": 0.20,
}
MIN_POSITIONS, MAX_POSITIONS = 8, 12
#: A target weight below this is float residue (``1.0 - sum(...)`` reads
#: ~1e-16, not 0). Kept as a target it is a TRIM, not an exit, and leaves
#: dust the emulator can never sell (its $1 order minimum).
WEIGHT_FLOOR = 1e-6

# Venue tags the research may append or prepend ("TSM.US", "NASDAQ:NVDA").
# Single letters are NOT here: ".A"/".B" are share classes.
_VENUES = frozenset({"US", "NYSE", "NYS", "NASDAQ", "NAS", "NQ", "OQ", "ARCA",
                     "NYSEARCA", "AMEX", "BATS", "CBOE", "XNYS", "XNAS", "OTC"})
_BASE = re.compile(r"[A-Z]{1,5}\Z")
_TICKER = re.compile(r"[A-Z]{1,5}(\.[A-Z])?\Z")
_EPS = 1e-12


# --- symbols ----------------------------------------------------------------


def normalize_symbol(raw) -> str | None:
    """Canonical US ticker for ``raw``, or None if it is not one.

    Uppercases, drops a leading ``$`` and a venue tag (``TSM.US``,
    ``NASDAQ:NVDA``), writes share classes as ``BRK.B`` (from ``brk-b``,
    ``BRK/B``, ``BRK B``). Foreign listings (``SAP.DE``) and free text give None.
    """
    if not isinstance(raw, str):
        return None
    s = raw.strip().upper().lstrip("$")
    if ":" in s:
        left, _, right = s.partition(":")
        if left in _VENUES:
            s = right
        elif right in _VENUES:
            s = left
        else:
            return None
    s = re.sub(r"[-/ ]", ".", s.strip())
    parts = s.split(".")
    if len(parts) > 1 and parts[-1] in _VENUES:
        parts = parts[:-1]
    if len(parts) == 1 and _BASE.match(parts[0]):
        return parts[0]
    if len(parts) == 2 and _BASE.match(parts[0]) and re.fullmatch(r"[A-Z]", parts[1]):
        return parts[0] + "." + parts[1]
    return None


def is_etf_symbol(sym: str) -> bool:
    """True when ``sym`` is a known ETF -- allowed or not."""
    return sym in ALL_ALLOWED_ETFS or sym in KNOWN_THEME_ETFS


def is_allowed_symbol(sym: str, *, is_etf: bool) -> bool:
    """May the lane hold ``sym`` (already normalized)? ETFs only from the
    allowed sets; a stock is any well-formed ticker that is not a known ETF."""
    if not isinstance(sym, str) or not _TICKER.match(sym):
        return False
    if is_etf:
        return sym in ALL_ALLOWED_ETFS
    return sym not in KNOWN_THEME_ETFS


# --- book -------------------------------------------------------------------


def _num(value, default: float) -> float:
    try:
        out = float(value)
    except (TypeError, ValueError):
        return default
    return out if out == out else default          # NaN -> default


def _is_levered_or_inverse(sym: str) -> bool:
    return sym in LEVERAGED_ETFS or sym in INVERSE_ETFS


def snap_weights(weights: dict[str, float]) -> dict[str, float]:
    """``weights`` with every non-BIL weight below ``WEIGHT_FLOOR`` removed
    and BIL re-set to the remainder (0.0 when that remainder is residue).
    BIL is always present."""
    out = {s: float(w) for s, w in (weights or {}).items()
           if s != BIL and float(w) >= WEIGHT_FLOOR}
    rest = 1.0 - sum(out.values())
    out[BIL] = rest if rest >= WEIGHT_FLOOR else 0.0
    return out


def target_book(row: dict, *, held: dict[str, float],
                has_bars: Callable[[str], bool], cfg: dict,
                stats: dict | None = None
                ) -> tuple[dict[str, float], list[str]]:
    """Target weights (always including ``"BIL"``, summing to 1.0) and the
    reason log for one weekly decision.

    ``row``: a ledger row (``notes``, ``book``, ``recommended``). ``held``:
    the lane's current holdings, symbol -> weight; only the keys are read.
    ``has_bars(sym)``: whether ``sym`` has bars at the decision date. ``cfg``:
    the rule parameters (see ``DEFAULTS``). ``stats``, when given, is filled
    with ``stock_picks`` (picks that are researched stocks, not ETFs) and
    ``picks_without_bars`` (those of them with no bars), so the caller can
    tell one bad ticker from a week whose bars were never fetched.
    """
    cfg = cfg or {}
    log: list[str] = []

    want = int(_num(cfg.get("max_positions"), DEFAULTS["max_positions"]))
    max_pos = min(MAX_POSITIONS, max(MIN_POSITIONS, want))
    if max_pos != want:
        log.append("max_positions %d clamped to %d (book is %d-%d names)"
                   % (want, max_pos, MIN_POSITIONS, MAX_POSITIONS))
    margin = _num(cfg.get("swap_margin"), DEFAULTS["swap_margin"])
    single_cap = _num(cfg.get("max_single_weight"), DEFAULTS["max_single_weight"])
    lev_cap = _num(cfg.get("max_levered_inverse_weight"),
                   DEFAULTS["max_levered_inverse_weight"])
    default_weight = 1.0 / max_pos

    bars_seen: dict[str, bool] = {}
    rejected: set = set()

    def screen(raw, where: str) -> str | None:
        sym = normalize_symbol(raw)
        if sym is None:
            if (where, raw) not in rejected:
                rejected.add((where, raw))
                log.append("drop %r (%s): not a US-listed symbol" % (raw, where))
            return None
        if sym == BIL:
            return sym
        if not is_allowed_symbol(sym, is_etf=is_etf_symbol(sym)):
            if (where, sym) not in rejected:
                rejected.add((where, sym))
                log.append("drop %s (%s): ETF outside the allowed universe" % (sym, where))
            return None
        if sym not in bars_seen:
            bars_seen[sym] = bool(has_bars(sym))
        if not bars_seen[sym]:
            if (where, sym) not in rejected:
                rejected.add((where, sym))
                log.append("drop %s (%s): no bars at the decision date" % (sym, where))
            return None
        return sym

    # Latest note per symbol (a later note in the list wins).
    notes: dict[str, dict] = {}
    for n in row.get("notes") or ():
        sym = screen((n or {}).get("symbol"), "note")
        if sym:
            notes[sym] = n
    recommended = set()
    for r in row.get("recommended") or ():
        sym = screen((r or {}).get("symbol"), "recommended")
        if sym:
            recommended.add(sym)

    # Every symbol the research wrote a note for this week, bars or not.
    researched = {normalize_symbol((n or {}).get("symbol")): (n or {})
                  for n in row.get("notes") or ()}
    held_syms = {normalize_symbol(h) for h in (held or {})}

    proposal = row.get("book") or {}
    picks: dict[str, dict] = {}                     # first pick per symbol wins
    stock_picks: list[str] = []
    no_bar_picks: list[str] = []
    for p in proposal.get("picks") or ():
        raw = (p or {}).get("symbol")
        sym = normalize_symbol(raw)
        if (sym is not None and sym not in ALL_ALLOWED_ETFS
                and is_allowed_symbol(sym, is_etf=is_etf_symbol(sym))
                and sym not in researched):
            if ("pick", sym) not in rejected:
                rejected.add(("pick", sym))
                log.append("drop %s (pick): no research note this week" % sym)
            continue
        if sym is not None and sym not in ALL_ALLOWED_ETFS and sym not in stock_picks \
                and is_allowed_symbol(sym, is_etf=is_etf_symbol(sym)):
            stock_picks.append(sym)
        screened = screen(raw, "pick")
        if screened is None:
            if sym in stock_picks and not bars_seen.get(sym, True) \
                    and sym not in no_bar_picks:
                no_bar_picks.append(sym)
            continue
        if screened not in picks:
            picks[screened] = p
    if stats is not None:
        stats["stock_picks"] = list(stock_picks)
        stats["picks_without_bars"] = list(no_bar_picks)
    # A NEW pick that would have been bought but has no bars hands its slot
    # back to the holdings (a held one is screened out like any holding).
    first_pick: dict[str, dict] = {}
    for p in proposal.get("picks") or ():
        first_pick.setdefault(normalize_symbol((p or {}).get("symbol")), p or {})

    def would_buy(sym: str) -> bool:
        p = first_pick.get(sym) or {}
        w = p.get("target_weight")
        return (sym not in held_syms and p.get("role") != "defensive"
                and (w is None or _num(w, 1.0) > 0)
                and researched.get(sym, {}).get("thesis_status") != "broken")

    spare = sum(1 for s in no_bar_picks if would_buy(s))
    defensive_share = min(1.0, max(0.0, _num(proposal.get("defensive_share"), 0.0)))

    def conviction(sym: str) -> float:
        if sym in notes and notes[sym].get("conviction") is not None:
            return _num(notes[sym].get("conviction"), 0.0)
        if sym in picks:
            return _num(picks[sym].get("conviction"), 0.0)
        return 0.0

    def broken(sym: str) -> bool:
        return sym in notes and notes[sym].get("thesis_status") == "broken"

    def target_weight(sym: str) -> float:
        p = picks.get(sym)
        if p is None or p.get("target_weight") is None:
            return default_weight
        return max(0.0, _num(p.get("target_weight"), default_weight))

    def zero_weight(sym: str) -> bool:
        """The proposal picks ``sym`` but at weight 0 (or below): not a buy."""
        p = picks.get(sym)
        return (p is not None and p.get("target_weight") is not None
                and target_weight(sym) <= 0.0)

    # Defensive picks take slots first, when the regime call wants a defensive share.
    defensive = [s for s, p in picks.items() if p.get("role") == "defensive"]
    if defensive_share <= 0.0:
        for s in defensive:
            if s in held_syms:
                log.append("%s: defensive_share 0, judged as a holding" % s)
            else:
                log.append("skip %s: defensive pick with defensive_share 0" % s)
        defensive = []
    for s in [s for s in defensive if zero_weight(s)]:
        defensive.remove(s)
        if s not in held_syms:                  # a held one exits below, with its own line
            log.append("skip %s: proposal weight 0" % s)
    defensive_names = sorted((s for s in defensive if s != BIL),
                             key=lambda s: -conviction(s))[:max_pos]
    slots = max_pos - len(defensive_names)

    # Holdings: keep unless broken or absent.
    kept: list[str] = []
    absent: list[str] = []
    for raw in sorted(held or {}):
        sym = screen(raw, "holding")
        if sym is None or sym == BIL or sym in defensive_names:
            continue
        if broken(sym):
            log.append("exit %s: thesis broken" % sym)
        elif sym not in notes and sym not in recommended and sym not in picks:
            absent.append(sym)
        elif zero_weight(sym):
            log.append("exit %s: proposal weight 0" % sym)
        else:
            kept.append(sym)
    kept.sort(key=lambda s: (-conviction(s), s))
    while len(kept) > slots:
        out = kept.pop()
        log.append("exit %s: no slot left (conviction %.1f)" % (out, conviction(out)))

    # New picks by conviction (proposal order breaks ties).
    new = [s for s, p in picks.items()
           if s != BIL and p.get("role") != "defensive" and s not in kept]
    candidates = []
    for s in new:
        if broken(s):
            log.append("skip %s: thesis broken" % s)
        elif zero_weight(s):
            log.append("skip %s: proposal weight 0" % s)
        else:
            candidates.append(s)
    candidates.sort(key=lambda s: -conviction(s))

    chosen = list(kept)
    holdings = set(kept)
    queue = list(candidates)
    while queue and len(chosen) < slots:
        s = queue.pop(0)
        chosen.append(s)
        log.append("add %s (conviction %.1f)" % (s, conviction(s)))
    # A slot left empty because its new pick had no bars keeps a holding the
    # row left out, rather than liquidating it into BIL.
    for s in absent:
        if spare > 0 and len(chosen) < slots:
            spare -= 1
            chosen.append(s)
            holdings.add(s)
            log.append("keep %s: absent from the row, but a new pick has no bars" % s)
        else:
            log.append("exit %s: absent from notes, recommended and picks" % s)
    for s in queue:
        if not holdings:
            break
        weakest = min(holdings, key=lambda h: (conviction(h), h))
        if conviction(s) + _EPS < conviction(weakest) + margin:
            break
        holdings.discard(weakest)
        chosen[chosen.index(weakest)] = s
        log.append("swap %s (%.1f) -> %s (%.1f)"
                   % (weakest, conviction(weakest), s, conviction(s)))

    # Weights.
    weights: dict[str, float] = {}
    for s in chosen:
        w = target_weight(s)
        if w > single_cap:
            log.append("cap %s %.4f -> %.2f" % (s, w, single_cap))
            w = single_cap
        if w > 0:
            weights[s] = w
    core_room = 1.0 - defensive_share
    core_total = sum(weights.values())
    if core_total > core_room + _EPS:
        k = core_room / core_total
        log.append("scale core %.4f -> %.4f (defensive_share %.2f)"
                   % (core_total, core_room, defensive_share))
        weights = {s: w * k for s, w in weights.items()}

    if defensive_share > 0.0:
        pool = [s for s in defensive if s == BIL or s in defensive_names]
        if not pool:
            log.append("defensive_share %.2f -> BIL (no defensive picks)" % defensive_share)
        else:
            raw_w = {s: target_weight(s) for s in pool}
            total = sum(raw_w.values())
            for s in pool:
                share = defensive_share * (raw_w[s] / total if total > 0 else 1.0 / len(pool))
                if s == BIL:
                    continue                         # stays in the remainder
                if share > single_cap:
                    log.append("cap %s %.4f -> %.2f (defensive)" % (s, share, single_cap))
                    share = single_cap
                if share > 0:
                    weights[s] = weights.get(s, 0.0) + share

    lev = {s: w for s, w in weights.items() if _is_levered_or_inverse(s)}
    lev_total = sum(lev.values())
    if lev_total > lev_cap + _EPS:
        k = lev_cap / lev_total
        log.append("scale leveraged+inverse %.4f -> %.2f" % (lev_total, lev_cap))
        for s, w in lev.items():
            weights[s] = w * k

    return snap_weights(weights), log


# --- trailing stop ------------------------------------------------------------


def _finite(value) -> float | None:
    """``value`` as a float, or None if it is missing, non-numeric, NaN or
    infinite -- a bad print must never become a peak or a stop decision."""
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    return out if math.isfinite(out) else None


def update_peaks(peaks: dict[str, float], closes: dict[str, float],
                 held: Iterable[str]) -> dict[str, float]:
    """Highest close since purchase for every held name (a new dict).

    A name no longer held is forgotten, so a re-buy starts a fresh peak. A
    held name with no usable close today (missing, NaN, infinite) keeps its
    peak; a newly held one, or one whose stored peak is not finite, starts at
    the next usable close.
    """
    out: dict[str, float] = {}
    for sym in held:
        close = _finite(closes.get(sym))
        prev = _finite(peaks.get(sym))
        if close is None:
            if prev is not None:
                out[sym] = prev
            continue
        out[sym] = close if prev is None else max(prev, close)
    return out


def trailing_stop_hits(peaks: dict[str, float], closes: dict[str, float], *,
                       stop_pct: float = 0.20) -> list[str]:
    """Names whose close is at or below ``(1 - stop_pct)`` of their peak, sorted.
    BIL (cash) is never stopped."""
    if not 0.0 < stop_pct < 1.0:
        raise ValueError("stop_pct must be in (0, 1): %r" % (stop_pct,))
    hits = []
    for sym, peak in peaks.items():
        close, peak = _finite(closes.get(sym)), _finite(peak)
        if sym == BIL or close is None or peak is None or peak <= 0:
            continue
        if close <= peak * (1.0 - stop_pct) * (1.0 + _EPS):
            hits.append(sym)
    return sorted(hits)
