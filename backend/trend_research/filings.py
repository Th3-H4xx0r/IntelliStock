"""SEC EDGAR filings into TrendFilingsPIT, and the per-symbol filing dossier
(spec §7.1, plan Task 4).

Rows. One per filing, id ``SYMBOL|acceptance_iso|accession``. Availability is
the submissions index's ``acceptanceDateTime``, which is UTC (checked against
live EDGAR: AAPL's 4:30 pm ET earnings 8-Ks read ``20:30Z`` in summer and
``21:30Z`` in winter); a fractional second rounds UP, so rounding can only
delay availability. A filing without a parseable acceptance stamp is skipped.

Two kinds of filing are stored:

* an index row -- form, accession, dates, ``available_at`` -- for every 10-K,
  10-Q, 20-F, 40-F, 6-K and 8-K accepted from ``INDEX_LOOKBACK_DAYS`` before
  ``start`` through ``end``. ``has_operating_filings`` (ruling R7) answers from
  these rows alone, with no network.
* an excerpt, attached to the index rows of filings accepted in
  ``start..end``: MD&A plus risk factors for 10-K/10-Q/20-F/40-F, the item
  text for 8-K/6-K, at most ``EXCERPT_CAP`` characters, from the primary
  document. A filing's documents are fixed at acceptance (a change is a new
  accession), so attaching its excerpt to an already-stored row adds nothing
  that was not public at ``available_at``. An excerpted row is never
  re-fetched; a run is resumable.

Amendments (``10-K/A`` and so on) are not stored.

Issuer records. Every fetch refreshes ``edgar_cik|<CIK>`` in
TrendIngestProgress with the issuer's SIC code and name. A commodity pool
(SIC 6221: GLD, USO, the ProShares Trust II leveraged ETPs) files 10-K/10-Q
like a company, so ``has_operating_filings`` excludes it (ruling R7b).

Fundamentals in the dossier come from ``edgar_factors.factor_set``, gated on
each fact's ``filed`` DATE. A fact filed on the decision's own (New York) day
may have been filed after the decision, so the dossier asks for the factors as
of the day before. companyfacts is fetched through the same retrying client
and cached per CIK; ``factors_status`` (ok / no_facts / fetch_failed) tells a
real "no data" apart from a fetch that kept failing.

EDGAR fair access: a declared User-Agent (``edgar_fundamentals.user_agent``)
and at most 10 requests/second (``edgar_fundamentals._throttle``).

CLI (run from ``backend/``)::

    python3 -m trend_research.filings --symbols AAPL,NVDA --start 2021-01-01 --end 2026-11-05
"""
from __future__ import annotations

import argparse
import dataclasses
import html as _html
import logging
import re
import threading
import time
from collections import OrderedDict
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import edgar_factors
import edgar_fundamentals
from edgar_factors import FactorSet, factor_set
from edgar_fundamentals import _throttle, user_agent
from trend_research.pit_store import (
    PROGRESS_TABLE,
    mark_done,
    parse_available_at,
    put_rows,
    read_available,
)

__all__ = ["fetch_filings", "filing_dossier", "has_operating_filings", "html_to_text",
           "extract_excerpt", "FilingsFetchError", "OPERATING_FORMS", "main"]

log = logging.getLogger(__name__)

TABLE = "TrendFilingsPIT"
SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{cik:010d}.json"
SUBMISSIONS_PAGE_URL = "https://data.sec.gov/submissions/{name}"
DOCUMENT_URL = "https://www.sec.gov/Archives/edgar/data/{cik}/{accession}/{document}"
TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"

#: Forms that only an operating company files (ETFs file N-CSR / N-PORT).
OPERATING_FORMS = frozenset({"10-K", "10-Q", "20-F", "40-F", "6-K"})
_REPORT_FORMS = frozenset({"10-K", "10-Q", "20-F", "40-F"})   # MD&A + risk factors
_EVENT_FORMS = frozenset({"8-K", "6-K"})                      # item text
STORED_FORMS = _REPORT_FORMS | _EVENT_FORMS

#: Issuer records (SIC, name) in TrendIngestProgress, id "edgar_cik|<CIK>".
CIK_SOURCE = "edgar_cik"
#: SIC "commodity contracts brokers & dealers": commodity pools -- GLD, SLV,
#: USO, UNG and the ProShares Trust II leveraged ETPs -- file 10-K/10-Q, but
#: they are not operating companies (ruling R7b).
COMMODITY_POOL_SICS = frozenset({"6221"})

FACTORS_OK = "ok"
FACTORS_NO_FACTS = "no_facts"
FACTORS_FETCH_FAILED = "fetch_failed"

EXCERPT_CAP = 6000
#: Index rows reach this far before ``start``, so a decision early in the
#: range still sees the issuer's last annual/quarterly report.
INDEX_LOOKBACK_DAYS = 400
#: An operating filing must be accepted this many days before a decision for
#: the symbol to count as a stock (ruling R27).
OPERATING_FILING_WINDOW_DAYS = 400

MAX_ATTEMPTS = 6
BACKOFF_BASE = 2.0
BACKOFF_CAP = 60.0
REQUEST_TIMEOUT = 30
_RETRY_STATUSES = frozenset({429, 500, 502, 503, 504})
_DOC_SUFFIXES = (".htm", ".html", ".txt")

_ET = ZoneInfo("America/New_York")
_ACCESSION_RE = re.compile(r"^\d{10}-\d{2}-\d{6}$")
_SYMBOL_RE = re.compile(r"^[A-Z0-9][A-Z0-9.\-/]*$")


class FilingsFetchError(RuntimeError):
    """EDGAR refused a request or kept failing after retries."""


# -- small helpers ----------------------------------------------------------------

def _norm_symbol(symbol) -> str:
    sym = str(symbol or "").strip().upper()
    return sym if _SYMBOL_RE.match(sym) else ""


def _parse_utc(value) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        return None
    return parsed.astimezone(timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _iso_ceil(dt: datetime) -> str:
    if dt.microsecond:
        dt = dt.replace(microsecond=0) + timedelta(seconds=1)
    return _iso(dt)


def _check_decision_time(decision_time) -> None:
    if (not isinstance(decision_time, datetime) or decision_time.tzinfo is None
            or decision_time.utcoffset() is None):
        raise ValueError("decision_time must be a tz-aware datetime, got %r"
                         % (decision_time,))


def _store(store):
    # The real store gets the TR tables ensured first (pit_store.ensure_tables).
    from trend_research import pit_store
    return pit_store._store(store)


def _cik_for(sym: str, resolve) -> int | None:
    """The CIK for ``sym``; SEC spells share classes with a dash (BRK-B)."""
    for candidate in dict.fromkeys((sym, sym.replace(".", "-"), sym.replace("/", "-"))):
        cik = resolve(candidate)
        if cik is not None:
            return int(cik)
    return None


# -- HTTP -------------------------------------------------------------------------

def _default_http_get(url, *, headers, timeout):
    import requests
    return requests.get(url, headers=headers, timeout=timeout)


class _Client:
    """Throttled EDGAR GETs with bounded retry on 429/5xx/network errors."""

    def __init__(self, http_get, sleep):
        self._http_get = http_get
        self._sleep = sleep
        self.requests = 0

    def _get(self, url: str, parse):
        """``parse(resp)`` of the 200 response, or None on 404."""
        headers = {"User-Agent": user_agent(), "Accept-Encoding": "gzip, deflate"}
        problem = ""
        for attempt in range(MAX_ATTEMPTS):
            _throttle()
            self.requests += 1
            try:
                resp = self._http_get(url, headers=headers, timeout=REQUEST_TIMEOUT)
            except OSError as exc:          # requests' errors subclass IOError
                problem = "network error %s" % type(exc).__name__
            else:
                status = getattr(resp, "status_code", None)
                if status == 200:
                    try:
                        return parse(resp)
                    except ValueError:      # truncated/garbled body: retry it
                        problem = "HTTP 200 with a malformed body"
                elif status == 404:
                    return None
                elif status in _RETRY_STATUSES:
                    problem = "HTTP %s" % status
                else:
                    body = str(getattr(resp, "text", "") or "")[:200]
                    raise FilingsFetchError("%s -> HTTP %s: %s" % (url, status, body))
            if attempt + 1 == MAX_ATTEMPTS:
                break
            delay = min(BACKOFF_BASE * (2 ** attempt), BACKOFF_CAP)
            log.warning("edgar %s (%s); retry %d/%d in %.1fs", problem, url,
                        attempt + 1, MAX_ATTEMPTS - 1, delay)
            self._sleep(delay)
        raise FilingsFetchError("%s: gave up after %d attempts (%s)"
                                % (url, MAX_ATTEMPTS, problem))

    def get_json(self, url: str):
        def parse(resp):
            payload = resp.json()
            if not isinstance(payload, dict):
                raise ValueError("not a JSON object")
            return payload
        return self._get(url, parse)

    def get_text(self, url: str):
        return self._get(url, lambda resp: str(resp.text or ""))


# -- text extraction ----------------------------------------------------------------

_DROP_RE = re.compile(r"<!--.*?-->|<(script|style|ix:header|head)\b.*?</\1\s*>",
                      re.I | re.S)
_BLOCK_RE = re.compile(r"</?(?:p|div|br|tr|li|ul|ol|h[1-6]|table|thead|tbody|section|"
                       r"center|blockquote|pre|hr|page)\b[^>]*>", re.I)
_CELL_RE = re.compile(r"</t[dh]\s*>", re.I)
_TAG_RE = re.compile(r"<[^>]*>")
_SPACE_RE = re.compile(r"[ \t\f\v\u00a0\u2002\u2003\u2009\u200b]+")


def html_to_text(doc: str) -> str:
    """Visible text of an EDGAR HTML (or plain-text) document, one block per
    line; table cells of one row share a line."""
    text = _DROP_RE.sub(" ", doc or "")
    text = _BLOCK_RE.sub("\n", text)
    text = _CELL_RE.sub(" ", text)
    text = _TAG_RE.sub("", text)
    text = _html.unescape(text)
    lines = (_SPACE_RE.sub(" ", line).strip() for line in text.splitlines())
    return "\n".join(line for line in lines if line)


_SEP = r"\s*[.:\-\u2013\u2014]?\s*"
_MGMT = r"management\S{0,3}\s+discussion"


def _line(pattern: str):
    return re.compile(r"^[ ]*" + pattern, re.I | re.M)


#: form -> [(label, start heading, end heading)]; both headings must start a
#: line, so a cross-reference inside a sentence is not a heading.
_SECTIONS = {
    "10-K": [("MD&A", _line(r"item\s*7" + _SEP + _MGMT), _line(r"item\s*(?:7a|8)\b")),
             ("Risk factors", _line(r"item\s*1a" + _SEP + r"risk\s+factors"),
              _line(r"item\s*(?:1b|1c|2)\b"))],
    "10-Q": [("MD&A", _line(r"item\s*2" + _SEP + _MGMT), _line(r"item\s*[34]\b")),
             ("Risk factors", _line(r"item\s*1a" + _SEP + r"risk\s+factors"),
              _line(r"item\s*[2-6]\b"))],
    "20-F": [("Operating review", _line(r"item\s*5" + _SEP + r"operating\s+and\s+financial"),
              _line(r"item\s*6\b")),
             ("Risk factors", _line(r"(?:item\s*3" + _SEP + r")?(?:d" + _SEP + r")?"
                                    r"risk\s+factors"), _line(r"item\s*4\b"))],
    "40-F": [("MD&A", _line(_MGMT + r"\s+and\s+analysis"), None),
             ("Risk factors", _line(r"risk\s+factors\b"), None)],
}
_EVENT_ITEM_RE = _line(r"item\s*(\d{1,2}\.\d{2})\b")
_EVENT_END_RE = _line(r"(?:item\s*9\.01\b|signatures?[ ]*$)")
_COVER_END_RE = re.compile(r"form\s*40-f", re.I)
_CHECK_MARK_RE = _line(r"indicate\s+by\s+check\s+mark\b.*$")
_SIGNED_RE = _line(r".*/s/.*$")
#: A 6-K body shorter than this before its signatures is a cover sheet; the
#: report itself follows the signature block.
_SIX_K_MIN_BODY = 200
_NOISE_LINE_RE = re.compile(r"^(?:table\s+of\s+contents|page\s+\d+|\d{1,3}|[ivxlc]{1,6})$",
                            re.I)


def _clean(span: str) -> str:
    lines = (ln for ln in span.splitlines() if not _NOISE_LINE_RE.match(ln.strip()))
    return _SPACE_RE.sub(" ", " ".join(lines)).strip()


def _after_heading(text: str, m, limit: int) -> int:
    """Where a section's body starts: the line after its heading."""
    nl = text.find("\n", m.end())
    return nl + 1 if 0 <= nl < limit else m.end()


def _section(text: str, start_re, end_re) -> str:
    """The longest heading-to-next-heading span (a table-of-contents entry is
    a short one); with no end heading, the last heading to the end."""
    if end_re is None:
        matches = list(start_re.finditer(text))
        if not matches:
            return ""
        m = matches[-1]
        return _clean(text[_after_heading(text, m, len(text)):])
    best = ""
    for m in start_re.finditer(text):
        e = end_re.search(text, m.end())
        if e is None:
            continue
        span = text[_after_heading(text, m, e.start()):e.start()]
        if len(span) > len(best):
            best = span
    return _clean(best)


def _compose(parts, cap: int = EXCERPT_CAP) -> str:
    """Join labelled sections within ``cap`` characters, sharing the budget
    evenly and handing what a short section leaves to the longer ones."""
    parts = [(label, body) for label, body in parts if body]
    if not parts:
        return ""
    heads = ["[%s] " % label if label else "" for label, _ in parts]
    budget = cap - sum(len(h) for h in heads) - 2 * (len(parts) - 1)
    share = {}
    left = len(parts)
    for i in sorted(range(len(parts)), key=lambda i: len(parts[i][1])):
        share[i] = min(len(parts[i][1]), max(budget, 0) // left)
        budget -= share[i]
        left -= 1
    out = "\n\n".join(heads[i] + parts[i][1][:share[i]] for i in range(len(parts)))
    return out[:cap]


def _event_text(form: str, text: str) -> str:
    if form == "8-K":
        items = [m for m in _EVENT_ITEM_RE.finditer(text) if m.group(1) != "9.01"]
        items = items or list(_EVENT_ITEM_RE.finditer(text))
        if not items:
            return _clean(text)
        start = items[0].start()
        end = _EVENT_END_RE.search(text, start + 1)
        return _clean(text[start:end.start() if end else len(text)])
    return _six_k_text(text)


def _six_k_text(text: str) -> str:
    """A 6-K's report: after the cover page's check-mark lines, and after the
    signature block when the signatures come first (they often do)."""
    covers = list(_COVER_END_RE.finditer(text, 0, 6000))
    start = _after_heading(text, covers[-1], len(text)) if covers else 0
    for m in _CHECK_MARK_RE.finditer(text, start):
        if text[start:m.start()].strip():
            break
        start = m.end()
    end = _EVENT_END_RE.search(text, start)
    if end is None:
        return _clean(text[start:])
    body = _clean(text[start:end.start()])
    if len(body) >= _SIX_K_MIN_BODY:
        return body
    signed = _SIGNED_RE.search(text, end.end())
    if signed is None:
        return body
    after = signed.end()
    for _ in range(2):                      # the signer's name and title lines
        nl = text.find("\n", after + 1)
        line = text[after:nl if nl >= 0 else len(text)].strip()
        if not line or len(line) > 80:
            break
        after = nl if nl >= 0 else len(text)
    return _clean(text[after:])


def extract_excerpt(form: str, doc: str) -> str:
    """The prompt excerpt of one primary document (``""`` if none found)."""
    text = html_to_text(doc)
    if form in _EVENT_FORMS:
        return _compose([("", _event_text(form, text))])
    parts = [(label, _section(text, start, end))
             for label, start, end in _SECTIONS.get(form, ())]
    if form == "40-F" and not any(body for _, body in parts):
        parts = [("", _clean(text))]        # a 40-F wrapper rarely has headings
    return _compose(parts)


# -- submissions index -----------------------------------------------------------

def _columns(page) -> list:
    """Submissions columns (``recent`` or an older page) as filing dicts."""
    if not isinstance(page, dict):
        return []
    accs = page.get("accessionNumber") or []
    out = []
    for i in range(len(accs)):
        def col(name, i=i):
            values = page.get(name) or []
            value = values[i] if i < len(values) else ""
            return value if isinstance(value, str) else ""
        out.append({"accession": col("accessionNumber"), "form": col("form"),
                    "filing_date": col("filingDate"), "report_date": col("reportDate"),
                    "accepted": col("acceptanceDateTime"),
                    "primary_document": col("primaryDocument"), "items": col("items")})
    return out


def _page_overlaps(meta, lo: date, hi: date) -> bool:
    try:
        first = date.fromisoformat(str(meta.get("filingFrom"))[:10])
        last = date.fromisoformat(str(meta.get("filingTo"))[:10])
    except (TypeError, ValueError):
        return True                         # undated page: read it
    return first <= hi and last >= lo


def _index_row(sym: str, cik: int, f: dict, accepted: datetime) -> dict:
    available_at = _iso_ceil(accepted)
    return {
        "id": "%s|%s|%s" % (sym, available_at, f["accession"]),
        "symbol": sym, "cik": cik, "form": f["form"], "accession": f["accession"],
        "filing_date": f["filing_date"], "report_date": f["report_date"],
        "available_at": available_at, "items": f["items"],
        "primary_document": f["primary_document"],
    }


def _excerpt_fields(client, cik: int, row: dict) -> dict:
    document = row.get("primary_document") or ""
    if "/" in document or not document.lower().endswith(_DOC_SUFFIXES):
        return {"excerpt": "", "excerpt_status": "unsupported"}
    url = DOCUMENT_URL.format(cik=cik, accession=row["accession"].replace("-", ""),
                              document=document)
    doc = client.get_text(url)
    if doc is None:
        return {"excerpt": "", "excerpt_status": "not_found"}
    excerpt = extract_excerpt(row["form"], doc)
    return {"excerpt": excerpt, "excerpt_status": "ok" if excerpt else "no_sections"}


def fetch_filings(symbol: str, start: date, end: date, *, http_get=None, store=None,
                  resolve=None, sleep=time.sleep) -> int:
    """Index ``symbol``'s 10-K/10-Q/20-F/40-F/6-K/8-K filings into
    TrendFilingsPIT and attach excerpts to those accepted on the UTC days
    ``start..end`` (inclusive).

    Returns how many filings were newly stored or newly excerpted. A symbol
    with no CIK (a non-US listing, say) or unknown to EDGAR returns 0 without
    error. Raises ``FilingsFetchError`` when EDGAR refuses or keeps failing.
    """
    if not isinstance(start, date) or not isinstance(end, date):
        raise ValueError("start and end must be dates")
    if end < start:
        raise ValueError("end %s is before start %s" % (end, start))
    sym = _norm_symbol(symbol)
    if not sym:
        log.info("edgar filings: unusable symbol %r", symbol)
        return 0
    st = _store(store)
    client = _Client(http_get or _default_http_get, sleep)
    if resolve is None:
        def resolve(s):
            return edgar_fundamentals.resolve_cik(
                s, _fetch=lambda: client.get_json(TICKERS_URL))
    cik = _cik_for(sym, resolve)
    if cik is None:
        log.info("edgar filings %s: no CIK", sym)
        return 0
    payload = client.get_json(SUBMISSIONS_URL.format(cik=cik))
    if payload is None:
        log.info("edgar filings %s: CIK %d not found on EDGAR", sym, cik)
        return 0
    _record_cik(st, cik, payload)

    index_lo = start - timedelta(days=INDEX_LOOKBACK_DAYS)
    block = payload.get("filings") or {}
    entries = _columns(block.get("recent"))
    for meta in block.get("files") or ():
        if isinstance(meta, dict) and meta.get("name") and _page_overlaps(meta, index_lo, end):
            entries += _columns(client.get_json(
                SUBMISSIONS_PAGE_URL.format(name=meta["name"])))

    rows: dict = {}
    unstamped = 0
    for f in entries:
        if f["form"] not in STORED_FORMS or not _ACCESSION_RE.match(f["accession"]):
            continue
        accepted = _parse_utc(f["accepted"])
        if accepted is None:
            unstamped += 1                  # unprovable availability
            continue
        if not index_lo <= accepted.date() <= end:
            continue
        row = _index_row(sym, cik, f, accepted)
        rows[row["id"]] = row
    if unstamped:
        log.warning("edgar filings %s: %d filings without a usable acceptance time skipped",
                    sym, unstamped)
    if not rows:
        return 0

    ids = list(rows)
    stored = {}
    for k in range(0, len(ids), 500):
        for doc in st.get_all(TABLE, *ids[k:k + 500]):
            stored[doc["id"]] = doc
    new = [rows[i] for i in ids if i not in stored]
    if new:
        put_rows(TABLE, new, store=st)
    touched = {row["id"] for row in new}

    for rid in ids:
        row = stored.get(rid) or rows[rid]
        if row.get("excerpt_status"):       # excerpted (or found unusable) before
            continue
        if not start <= _parse_utc(rows[rid]["available_at"]).date() <= end:
            continue
        st.update(TABLE, rid, _excerpt_fields(client, cik, row))
        touched.add(rid)
    log.info("edgar filings %s %s..%s: %d stored or excerpted, %d requests",
             sym, start, end, len(touched), client.requests)
    return len(touched)


# -- reads --------------------------------------------------------------------------

def _visible_rows(sym: str, decision_time: datetime, store) -> list:
    """``sym``'s rows accepted strictly before ``decision_time``, oldest first."""
    rows = read_available(TABLE, lo_id=sym + "|", hi_id=sym + "|" + _iso_ceil(decision_time),
                          decision_time=decision_time, store=_store(store))
    return [r for r in rows if r.get("symbol") == sym]


def _record_cik(st, cik: int, payload: dict) -> None:
    """Refresh the issuer record (SIC, name) of ``cik``. Today's value, not a
    point-in-time one: an issuer's SIC code almost never changes."""
    mark_done(CIK_SOURCE, str(cik), meta={
        "sic": str(payload.get("sic") or "").strip(),
        "name": str(payload.get("name") or ""),
        "fetched_at": _iso(datetime.now(timezone.utc)),
    }, store=st)


def _cik_sic(st, cik) -> str | None:
    """The stored SIC code of ``cik``; None if no record (a row indexed before
    issuer records existed, until its symbol is fetched again)."""
    if cik is None:
        return None
    rec = st.get(PROGRESS_TABLE, "%s|%s" % (CIK_SOURCE, cik))
    if not isinstance(rec, dict):
        return None
    return str((rec.get("meta") or {}).get("sic") or "").strip()


def has_operating_filings(symbol: str, decision_time: datetime, *, store=None) -> bool:
    """Whether a 10-K, 10-Q, 20-F, 40-F or 6-K of ``symbol`` was accepted in
    the ``OPERATING_FILING_WINDOW_DAYS`` before ``decision_time`` (ruling R7:
    what makes an unlisted symbol a stock), by an issuer that is not a
    commodity pool (R7b: SIC 6221 -- GLD, USO, the ProShares Trust II
    leveraged ETPs -- files 10-K/10-Q too).

    The window (ruling R27) filters reassigned tickers: the ticker->CIK map
    is today's, so a ticker handed to a new issuer maps to that issuer, whose
    filings either start after the decision or -- for a long-dormant old
    one -- end long before it. Either way there is no recent operating
    filing and the symbol is not treated as a stock. A name acquired or
    delisted since the decision has no CIK today and is dropped the same
    way (a documented survivorship limit). Reads stored rows only; a symbol
    never fetched is False."""
    _check_decision_time(decision_time)
    sym = _norm_symbol(symbol)
    if not sym:
        return False
    st = _store(store)
    window_start = decision_time - timedelta(days=OPERATING_FILING_WINDOW_DAYS)
    sel = st.between(TABLE, sym + "|" + _iso(window_start),
                     sym + "|" + _iso_ceil(decision_time))
    sic_by_cik: dict = {}
    for row in st.iter(sel, batch=50):
        if row.get("symbol") != sym or row.get("form") not in OPERATING_FORMS:
            continue
        try:
            at = parse_available_at(row.get("available_at"))
        except ValueError:
            continue                        # unprovable availability
        if not window_start <= at < decision_time:
            continue
        cik = row.get("cik")
        if cik not in sic_by_cik:
            sic_by_cik[cik] = _cik_sic(st, cik)
        if sic_by_cik[cik] not in COMMODITY_POOL_SICS:
            return True
    return False


_FACT_TAGS = frozenset(
    edgar_factors._TAG_ASSETS + edgar_factors._TAG_NET_INCOME + edgar_factors._TAG_CFO
    + edgar_factors._TAG_SHARES + edgar_factors._TAG_GROSS_PROFIT
    + edgar_factors._TAG_REVENUE + edgar_factors._TAG_COGS)


_FACTS_CACHE_SIZE = 256
_facts_cache: OrderedDict = OrderedDict()
_facts_lock = threading.Lock()


def _company_facts(cik: int, http_get=None, sleep=time.sleep) -> dict | None:
    """companyfacts trimmed to the tags ``factor_set`` reads, or None when
    EDGAR has none (404, cached like a hit). Every vintage keeps its ``filed``
    date, so one fetch serves every decision date. Cached per CIK for the
    process (LRU); a fetch that fails after retries raises
    ``FilingsFetchError`` and is not cached."""
    with _facts_lock:
        if cik in _facts_cache:
            _facts_cache.move_to_end(cik)
            return _facts_cache[cik]
    client = _Client(http_get or _default_http_get, sleep)
    facts = client.get_json(edgar_factors._FACTS_URL.format(cik=cik))
    trimmed = None
    if facts is not None:
        gaap = (facts.get("facts") or {}).get("us-gaap") or {}
        trimmed = {"facts": {"us-gaap": {t: v for t, v in gaap.items() if t in _FACT_TAGS}}}
    with _facts_lock:
        _facts_cache[cik] = trimmed
        _facts_cache.move_to_end(cik)
        while len(_facts_cache) > _FACTS_CACHE_SIZE:
            _facts_cache.popitem(last=False)
    return trimmed


def clear_facts_cache() -> None:
    with _facts_lock:
        _facts_cache.clear()


def _factor_dict(fs: FactorSet) -> dict:
    return {k: (v.isoformat() if isinstance(v, date) else v)
            for k, v in dataclasses.asdict(fs).items()}


def _factors(sym: str, decision_time: datetime, resolve, fetch_facts, http_get,
             sleep) -> tuple:
    """``(factors dict, status)``; status is ``ok`` (companyfacts read),
    ``no_facts`` (no CIK, or EDGAR has no companyfacts) or ``fetch_failed``
    (EDGAR kept failing: the factors are unknown, not empty)."""
    as_of = decision_time.astimezone(_ET).date() - timedelta(days=1)
    empty = _factor_dict(FactorSet(symbol=sym, as_of=as_of))
    if not _norm_symbol(sym):
        return empty, FACTORS_NO_FACTS
    if resolve is None:
        def resolve(s):
            return edgar_fundamentals.resolve_cik(s, _fetch=lambda: _Client(
                http_get or _default_http_get, sleep).get_json(TICKERS_URL))
    if fetch_facts is None:
        def fetch_facts(cik):
            return _company_facts(cik, http_get, sleep)
    try:
        cik = _cik_for(sym, resolve)
        facts = fetch_facts(cik) if cik is not None else None
    except Exception as exc:                # noqa: BLE001 -- reported, not raised
        log.warning("edgar facts %s: %s: %s", sym, type(exc).__name__, exc)
        return empty, FACTORS_FETCH_FAILED
    if facts is None:
        return empty, FACTORS_NO_FACTS
    return _factor_dict(factor_set(sym, as_of, facts=facts)), FACTORS_OK


_DOSSIER_FIELDS = ("form", "accession", "filing_date", "report_date", "available_at",
                   "items", "excerpt")


def filing_dossier(symbol: str, decision_time: datetime, *, max_filings: int = 3,
                   store=None, resolve=None, fetch_facts=None, http_get=None,
                   sleep=time.sleep) -> dict:
    """``{"factors": FactorSet-as-dict, "factors_status": ..., "filings": [...]}``
    for a decision at ``decision_time``: the newest ``max_filings`` excerpted
    filings accepted strictly before it (newest first), and the fundamentals
    filed before the decision's New York day.

    ``factors_status`` is ``ok``, ``no_facts`` (no CIK -- a non-US listing --
    or no companyfacts on EDGAR; never an exception) or ``fetch_failed``
    (EDGAR kept failing after retries; the caller must not read the all-None
    factors as "no data")."""
    _check_decision_time(decision_time)
    sym = _norm_symbol(symbol) or str(symbol or "").strip().upper()
    factors, factors_status = _factors(sym, decision_time, resolve, fetch_facts,
                                       http_get, sleep)
    picked = []
    if _norm_symbol(sym) and max_filings > 0:
        for row in reversed(_visible_rows(sym, decision_time, store)):
            if row.get("excerpt_status") == "ok" and row.get("excerpt"):
                picked.append({k: row.get(k, "") for k in _DOSSIER_FIELDS})
                if len(picked) == max_filings:
                    break
    return {"factors": factors, "factors_status": factors_status, "filings": picked}


# -- CLI ------------------------------------------------------------------------------

def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        prog="python3 -m trend_research.filings",
        description="Backfill EDGAR filing index rows and excerpts into TrendFilingsPIT.")
    parser.add_argument("--symbols", required=True,
                        help="comma-separated tickers, e.g. AAPL,NVDA")
    parser.add_argument("--start", required=True, type=date.fromisoformat,
                        help="first UTC day of excerpts, YYYY-MM-DD")
    parser.add_argument("--end", required=True, type=date.fromisoformat,
                        help="last UTC day of excerpts (inclusive), YYYY-MM-DD")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    failed = []
    total = 0
    for sym in [s.strip() for s in args.symbols.split(",") if s.strip()]:
        try:
            total += fetch_filings(sym, args.start, args.end)
        except FilingsFetchError as exc:
            log.error("edgar filings %s failed: %s", sym, exc)
            failed.append(sym)
    print("edgar filings %s..%s: %d filings stored or excerpted; failed: %s"
          % (args.start, args.end, total, ",".join(failed) or "none"))
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
