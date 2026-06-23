"""Human-readable match + pick from a Kalshi soccer ticker (pure, tested).

A ticker like ``KXWCGAME-26JUN24CZEMEX-CZE`` encodes everything: after the date,
``CZEMEX`` is the two FIFA team codes (home+away) and the suffix ``CZE`` is the pick
(or ``TIE`` for the draw). We split it and map codes to country names so the UI can
show "Czechia vs Mexico — Czechia to win" instead of a raw ticker."""
from __future__ import annotations

import re

# FIFA/IOC 3-letter codes -> country name (World-Cup field; extend freely).
_CODE_TO_NAME = {
    "ARG": "Argentina", "AUT": "Austria", "FRA": "France", "BRA": "Brazil",
    "ENG": "England", "ESP": "Spain", "POR": "Portugal", "NED": "Netherlands",
    "BEL": "Belgium", "GER": "Germany", "ITA": "Italy", "CRO": "Croatia",
    "URU": "Uruguay", "COL": "Colombia", "MAR": "Morocco", "SUI": "Switzerland",
    "DEN": "Denmark", "MEX": "Mexico", "USA": "United States", "JPN": "Japan",
    "SEN": "Senegal", "SRB": "Serbia", "ECU": "Ecuador", "POL": "Poland",
    "NGA": "Nigeria", "EGY": "Egypt", "PER": "Peru", "KOR": "South Korea",
    "AUS": "Australia", "CAN": "Canada", "IRI": "Iran", "IRN": "Iran",
    "UKR": "Ukraine", "SWE": "Sweden", "WAL": "Wales", "SCO": "Scotland",
    "NOR": "Norway", "TUR": "Turkey", "GHA": "Ghana", "CMR": "Cameroon",
    "CIV": "Ivory Coast", "TUN": "Tunisia", "DZA": "Algeria", "ALG": "Algeria",
    "CRC": "Costa Rica", "PAR": "Paraguay", "CHI": "Chile", "KSA": "Saudi Arabia",
    "QAT": "Qatar", "IRQ": "Iraq", "GRE": "Greece", "CZE": "Czechia",
    "HUN": "Hungary", "ROU": "Romania", "SVK": "Slovakia", "SVN": "Slovenia",
    "NZL": "New Zealand", "COD": "Congo DR", "BIH": "Bosnia and Herzegovina",
    "UZB": "Uzbekistan", "CUW": "Curacao", "JOR": "Jordan", "PAN": "Panama",
    "HTI": "Haiti", "HAI": "Haiti", "RSA": "South Africa", "CPV": "Cape Verde",
    "UGA": "Uganda", "BOL": "Bolivia", "VEN": "Venezuela", "HON": "Honduras",
    "JAM": "Jamaica", "MLI": "Mali", "BFA": "Burkina Faso", "GUI": "Guinea",
    "GAB": "Gabon", "ALB": "Albania", "MKD": "North Macedonia", "GEO": "Georgia",
    "FIN": "Finland", "ISL": "Iceland", "IRL": "Ireland", "NIR": "Northern Ireland",
    "ISR": "Israel", "BUL": "Bulgaria", "IDN": "Indonesia", "THA": "Thailand",
    "CHN": "China", "OMA": "Oman", "BHR": "Bahrain", "UAE": "United Arab Emirates",
}

_DATE = re.compile(r"^\d{2}[A-Z]{3}\d{2}", re.I)

# FIFA/IOC code -> ISO 3166-1 alpha-2 (flagcdn key; gb-* for the home nations).
_CODE_TO_ISO = {
    "ARG": "ar", "AUT": "at", "FRA": "fr", "BRA": "br", "ENG": "gb-eng", "ESP": "es",
    "POR": "pt", "NED": "nl", "BEL": "be", "GER": "de", "ITA": "it", "CRO": "hr",
    "URU": "uy", "COL": "co", "MAR": "ma", "SUI": "ch", "DEN": "dk", "MEX": "mx",
    "USA": "us", "JPN": "jp", "SEN": "sn", "SRB": "rs", "ECU": "ec", "POL": "pl",
    "NGA": "ng", "EGY": "eg", "PER": "pe", "KOR": "kr", "AUS": "au", "CAN": "ca",
    "IRI": "ir", "IRN": "ir", "UKR": "ua", "SWE": "se", "WAL": "gb-wls", "SCO": "gb-sct",
    "NOR": "no", "TUR": "tr", "GHA": "gh", "CMR": "cm", "CIV": "ci", "TUN": "tn",
    "DZA": "dz", "ALG": "dz", "CRC": "cr", "PAR": "py", "CHI": "cl", "KSA": "sa",
    "QAT": "qa", "IRQ": "iq", "GRE": "gr", "CZE": "cz", "HUN": "hu", "ROU": "ro",
    "SVK": "sk", "SVN": "si", "NZL": "nz", "COD": "cd", "BIH": "ba", "UZB": "uz",
    "CUW": "cw", "JOR": "jo", "PAN": "pa", "HTI": "ht", "HAI": "ht", "RSA": "za",
    "CPV": "cv", "UGA": "ug", "BOL": "bo", "VEN": "ve", "HON": "hn", "JAM": "jm",
    "MLI": "ml", "BFA": "bf", "GUI": "gn", "GAB": "ga", "ALB": "al", "MKD": "mk",
    "GEO": "ge", "FIN": "fi", "ISL": "is", "IRL": "ie", "NIR": "gb-nir", "ISR": "il",
    "BUL": "bg", "IDN": "id", "THA": "th", "CHN": "cn", "OMA": "om", "BHR": "bh",
    "UAE": "ae",
}


def name_for_code(code: str) -> str:
    c = (code or "").upper()
    return _CODE_TO_NAME.get(c, c.title() if c else "")


def flag_url(code: str) -> str:
    """Country-flag image URL (flagcdn, no API key) for a FIFA/IOC code; '' if unknown."""
    iso = _CODE_TO_ISO.get((code or "").upper())
    return f"https://flagcdn.com/w80/{iso}.png" if iso else ""


def parse_market_ticker(ticker: str, side: str | None = None) -> dict:
    """``KXWCGAME-26JUN24CZEMEX-CZE`` + optional side -> {home, away, pick,
    pick_label, match}. Robust to variable code lengths (splits using the suffix
    code when possible). Falls back to the raw ticker when it can't parse."""
    raw = {"home": "", "away": "", "pick": "", "pick_label": "",
           "match": ticker or "", "home_flag": "", "away_flag": "", "pick_flag": ""}
    segs = (ticker or "").split("-")
    if len(segs) < 3:
        return raw
    suffix = segs[-1].upper()
    blob = segs[-2]
    m = _DATE.match(blob)
    teams = blob[m.end():].upper() if m else blob.upper()
    if len(teams) < 4:
        return raw

    is_draw = suffix in ("TIE", "DRAW") or (side or "").lower() == "draw"
    home_code = away_code = ""
    if not is_draw and teams.startswith(suffix) and (side or "home").lower() != "away":
        home_code, away_code = suffix, teams[len(suffix):]
    elif not is_draw and teams.endswith(suffix):
        away_code, home_code = suffix, teams[: len(teams) - len(suffix)]
    else:
        half = len(teams) // 2          # draw / unknown -> even split
        home_code, away_code = teams[:half], teams[half:]

    home, away = name_for_code(home_code), name_for_code(away_code)
    home_flag, away_flag = flag_url(home_code), flag_url(away_code)
    s = (side or "").lower()
    if is_draw or suffix in ("TIE", "DRAW"):
        pick, pick_label, pick_flag = "draw", "Draw", ""
    elif s == "away" or (not s and suffix == away_code):
        pick, pick_label, pick_flag = "away", f"{away} to win", away_flag
    else:
        pick, pick_label, pick_flag = "home", f"{home} to win", home_flag
    return {"home": home, "away": away, "pick": pick, "pick_label": pick_label,
            "match": f"{home} vs {away}" if home and away else (ticker or ""),
            "home_flag": home_flag, "away_flag": away_flag, "pick_flag": pick_flag}
