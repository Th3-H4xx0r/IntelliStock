"""Symbol to its industry or sector fund, for the sector part of the split,
and the peers and company name the explanation step uses."""
from __future__ import annotations

import threading
from typing import Callable, Optional

#: yfinance sector names, normalised (lower case, spaces to underscores), to
#: the SPDR sector fund that tracks them.
SECTOR_ETFS = {
    "technology": "XLK",
    "financial_services": "XLF",
    "financial": "XLF",
    "healthcare": "XLV",
    "consumer_cyclical": "XLY",
    "consumer_defensive": "XLP",
    "energy": "XLE",
    "industrials": "XLI",
    "basic_materials": "XLB",
    "utilities": "XLU",
    "real_estate": "XLRE",
    "communication_services": "XLC",
}

#: Funds that track an index other than the S&P 500. Their "sector" series is
#: the plain index fund, so a Nasdaq-driven day in TQQQ is not read as
#: something specific to TQQQ.
FUND_BENCHMARKS = {
    "TQQQ": ("nasdaq_100", "QQQ"), "SQQQ": ("nasdaq_100", "QQQ"),
    "QLD": ("nasdaq_100", "QQQ"), "QQQM": ("nasdaq_100", "QQQ"),
    "SOXL": ("semiconductors", "SOXX"), "SOXS": ("semiconductors", "SOXX"),
    "SMH": ("semiconductors", "SOXX"),
    "TNA": ("small_caps", "IWM"), "TZA": ("small_caps", "IWM"),
    "GDX": ("gold", "GLD"), "GDXJ": ("gold", "GLD"), "NUGT": ("gold", "GLD"),
    "IAU": ("gold", "GLD"),
}

#: yfinance industry keys to a fund that tracks that industry. Used ahead of
#: the sector fund where the sector fund is a poor match: XLK is mostly chips
#: and megacaps, so a software stock measured against it shows software-wide
#: days as its own.
INDUSTRY_ETFS = {
    "software-application": ("software", "IGV"),
    "software-infrastructure": ("software", "IGV"),
    "semiconductors": ("semiconductors", "SOXX"),
    "semiconductor-equipment-materials": ("semiconductors", "SOXX"),
    "biotechnology": ("biotechnology", "XBI"),
    "banks-regional": ("regional_banks", "KRE"),
    "aerospace-defense": ("aerospace_defense", "ITA"),
    "medical-devices": ("medical_devices", "IHI"),
    "oil-gas-e-p": ("oil_gas_exploration", "XOP"),
    "gold": ("gold_miners", "GDX"),
    "airlines": ("airlines", "JETS"),
    "residential-construction": ("homebuilders", "ITB"),
    "solar": ("solar", "TAN"),
}

MAX_PEERS = 8

_CACHE: dict = {}
_PROFILES: dict = {}
_PEERS: dict = {}
_LOCK = threading.Lock()


def _profile_lookup(symbol: str) -> dict:
    import yfinance as yf

    info = yf.Ticker(symbol).info or {}
    return {"sector": info.get("sector") or info.get("sectorKey") or "",
            "industry": info.get("industryKey") or "",
            "name": info.get("shortName") or info.get("longName") or ""}


def profile_for(symbol: str, *, lookup: Optional[Callable] = None) -> dict:
    """``{"sector", "industry", "name"}`` as normalised strings; empty when
    the provider does not know the symbol. A failed lookup is not cached."""
    if lookup is None:
        with _LOCK:
            if symbol in _PROFILES:
                return dict(_PROFILES[symbol])
    try:
        raw = (lookup or _profile_lookup)(symbol)
    except Exception:
        raw = None
    if isinstance(raw, str):          # a plain sector name
        raw = {"sector": raw}
    raw = raw if isinstance(raw, dict) else {}
    profile = {
        "sector": str(raw.get("sector") or "").strip().lower().replace(" ", "_").replace("-", "_"),
        "industry": str(raw.get("industry") or "").strip().lower(),
        "name": " ".join(str(raw.get("name") or "").split())[:80],
    }
    if lookup is None and profile["sector"] and profile["sector"] != "unknown":
        with _LOCK:
            _PROFILES[symbol] = dict(profile)
    return profile


def sector_for(symbol: str, *, lookup: Optional[Callable] = None) -> tuple:
    """``(group, etf)``: the industry fund where one is mapped, else the
    sector fund. Both None for a fund, an index product or a symbol the
    provider does not classify."""
    if symbol in FUND_BENCHMARKS:
        return FUND_BENCHMARKS[symbol]
    profile = profile_for(symbol, lookup=lookup)
    industry = INDUSTRY_ETFS.get(profile["industry"])
    if industry and industry[1] != symbol:
        return industry
    etf = SECTOR_ETFS.get(profile["sector"])
    return (profile["sector"], etf) if etf and etf != symbol else (None, None)


def company_name(symbol: str) -> str:
    """The provider's short name with the legal suffix dropped, for a news
    search; empty when unknown."""
    name = profile_for(symbol)["name"]
    for suffix in (", Inc.", " Inc.", " Corporation", " Corp.", " Holdings",
                   " plc", " PLC", " Ltd.", " Limited", " N.V.", " S.A.", ", Inc"):
        if name.endswith(suffix):
            name = name[: -len(suffix)]
    return name.strip(" ,")


def _holdings_lookup(etf: str) -> list:
    import yfinance as yf

    return [str(s) for s in yf.Ticker(etf).funds_data.top_holdings.index]


def peers_for(symbol: str, etf: Optional[str], *,
              lookup: Optional[Callable] = None) -> list:
    """The largest holdings of the benchmark fund other than the symbol, as
    the stocks to compare it with. Empty when there is no fund or the
    provider has no holdings; a failed lookup is not cached."""
    if not etf:
        return []
    if lookup is None:
        with _LOCK:
            cached = _PEERS.get(etf)
    else:
        cached = None
    if cached is None:
        try:
            cached = [s.strip().upper().replace(".", "-")
                      for s in (lookup or _holdings_lookup)(etf) if str(s).strip()]
        except Exception:
            return []
        if lookup is None and cached:
            with _LOCK:
                _PEERS[etf] = list(cached)
    return [s for s in cached if s != symbol][:MAX_PEERS]
