"""EDGAR filings PIT excerpts (Task 4).

A filing reaches a prompt only once it was accepted: ``available_at`` is the
submissions index's ``acceptanceDateTime`` (UTC) and reads gate strictly
(``available_at < decision_time``). The fundamentals in the dossier are gated
on their ``filed`` date. No network: every request goes to a fake ``http_get``.
"""
from __future__ import annotations

import os
import sys
from datetime import date, datetime, timezone

import pytest

_backend = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _backend not in sys.path:
    sys.path.insert(0, _backend)

from db.fake import FakeStore  # noqa: E402
from trend_research import filings  # noqa: E402

CIK = 320193
SUB_URL = "https://data.sec.gov/submissions/CIK0000320193.json"


@pytest.fixture(autouse=True)
def _no_throttle(monkeypatch):
    monkeypatch.setattr(filings, "_throttle", lambda: None)


def utc(*a):
    return datetime(*a, tzinfo=timezone.utc)


class Resp:
    def __init__(self, status=200, payload=None, text=""):
        self.status_code = status
        self._payload = payload
        self.text = text
        self.headers = {}

    def json(self):
        if self._payload is None:
            raise ValueError("no json")
        return self._payload


class FakeHttp:
    """Serves a fixed url -> Resp map (a list is served in order); records calls."""

    def __init__(self, routes):
        self.routes = dict(routes)
        self.calls = []

    def __call__(self, url, *, headers, timeout):
        self.calls.append(url)
        assert headers.get("User-Agent"), "EDGAR requires a User-Agent"
        route = self.routes.get(url)
        if route is None:
            return Resp(404, text="not found")
        if isinstance(route, list):
            return route.pop(0) if len(route) > 1 else route[0]
        return route


def doc_url(accession, primary, cik=CIK):
    return "https://www.sec.gov/Archives/edgar/data/%d/%s/%s" % (
        cik, accession.replace("-", ""), primary)


def submissions(filings_list, files=()):
    """A submissions payload; each filing is (form, accession, acceptance, primary, items)."""
    cols = {k: [] for k in ("accessionNumber", "filingDate", "reportDate",
                            "acceptanceDateTime", "form", "primaryDocument", "items")}
    for form, acc, accepted, primary, items in filings_list:
        cols["accessionNumber"].append(acc)
        cols["filingDate"].append(accepted[:10])
        cols["reportDate"].append("")
        cols["acceptanceDateTime"].append(accepted)
        cols["form"].append(form)
        cols["primaryDocument"].append(primary)
        cols["items"].append(items)
    return {"cik": str(CIK), "name": "Test Co",
            "filings": {"recent": cols, "files": list(files)}}


def html(*blocks):
    return "<html><body>%s</body></html>" % "".join("<p>%s</p>" % b for b in blocks)


TEN_Q_DOC = html(
    "UNITED STATES SECURITIES AND EXCHANGE COMMISSION", "FORM 10-Q",
    "<b>Table of Contents</b>",
    "<table><tr><td>Item 2.</td><td>Management&#8217;s Discussion and Analysis</td>"
    "<td>12</td></tr><tr><td>Item 3.</td><td>Quantitative and Qualitative "
    "Disclosures About Market Risk</td><td>20</td></tr></table>",
    "PART I", "Item 1. Financial Statements", "Balance sheet numbers.",
    "<b>Item 2. Management&#8217;s Discussion and Analysis of Financial Condition</b>",
    "Revenue grew 12% on strong iPhone demand in Greater China.",
    "Item 3. Quantitative and Qualitative Disclosures About Market Risk",
    "Rates moved.", "Item 4. Controls and Procedures", "Controls are fine.",
    "PART II", "Item 1A. Risk Factors",
    "New export controls on advanced chips could reduce sales.",
    "Item 2. Unregistered Sales of Equity Securities", "Buybacks happened.",
)


def ten_q_store(accepted="2023-05-24T20:05:12.000Z", start=date(2023, 5, 1),
                end=date(2023, 5, 31)):
    acc = "0000320193-23-000064"
    http = FakeHttp({
        SUB_URL: Resp(payload=submissions([("10-Q", acc, accepted, "q.htm", "")])),
        doc_url(acc, "q.htm"): Resp(text=TEN_Q_DOC),
    })
    st = FakeStore()
    n = filings.fetch_filings("AAPL", start, end, http_get=http, store=st,
                              resolve=lambda s: CIK)
    return st, http, n


def dossier(st, at, symbol="AAPL", **kw):
    kw.setdefault("resolve", lambda s: CIK)
    kw.setdefault("fetch_facts", lambda cik: {"facts": {}})
    return filings.filing_dossier(symbol, at, store=st, **kw)


# -- the three plan tests ---------------------------------------------------------

def test_acceptance_time_gates():
    st, _http, n = ten_q_store()
    assert n == 1
    (row,) = st.get_all("TrendFilingsPIT", "AAPL|2023-05-24T20:05:12Z|0000320193-23-000064")
    assert row["available_at"] == "2023-05-24T20:05:12Z"
    assert row["form"] == "10-Q" and row["accession"] == "0000320193-23-000064"

    assert dossier(st, utc(2023, 5, 24, 20, 0))["filings"] == []
    assert dossier(st, utc(2023, 5, 24, 20, 5, 12))["filings"] == []    # strict <
    seen = dossier(st, utc(2023, 5, 25, 13, 0))["filings"]
    assert [f["accession"] for f in seen] == ["0000320193-23-000064"]
    assert "Greater China" in seen[0]["excerpt"]

    assert not filings.has_operating_filings("AAPL", utc(2023, 5, 24, 20, 0), store=st)
    assert not filings.has_operating_filings("AAPL", utc(2023, 5, 24, 20, 5, 12), store=st)
    assert filings.has_operating_filings("AAPL", utc(2023, 5, 25, 13, 0), store=st)


def test_excerpt_capped_6000():
    acc_k, acc_8 = "0000320193-23-000106", "0000320193-23-000107"
    long_mdna = "Services margin expanded again this year. " * 600
    long_risk = "Supply chain concentration in Asia is a risk. " * 600
    ten_k = html("Item 1A. Risk Factors", long_risk, "Item 1B. Unresolved Staff Comments",
                 "None.", "Item 7. Management's Discussion and Analysis", long_mdna,
                 "Item 7A. Quantitative and Qualitative Disclosures", "Rates.")
    eight_k = html("FORM 8-K", "Item 2.02 Results of Operations and Financial Condition",
                   "Quarterly results beat guidance. " * 600,
                   "Item 9.01 Financial Statements and Exhibits", "Exhibit 99.1",
                   "SIGNATURES", "Signed.")
    http = FakeHttp({
        SUB_URL: Resp(payload=submissions([
            ("10-K", acc_k, "2023-11-02T22:04:00.000Z", "k.htm", ""),
            ("8-K", acc_8, "2023-11-02T20:30:00.000Z", "e.htm", "2.02,9.01")])),
        doc_url(acc_k, "k.htm"): Resp(text=ten_k),
        doc_url(acc_8, "e.htm"): Resp(text=eight_k),
    })
    st = FakeStore()
    assert filings.fetch_filings("AAPL", date(2023, 11, 1), date(2023, 11, 30),
                                 http_get=http, store=st, resolve=lambda s: CIK) == 2
    got = {f["form"]: f for f in dossier(st, utc(2023, 11, 3))["filings"]}
    k, e = got["10-K"]["excerpt"], got["8-K"]["excerpt"]
    assert 5000 < len(k) <= 6000 and 5000 < len(e) <= 6000
    assert "Services margin" in k and "Supply chain concentration" in k  # both sections
    assert "Quarterly results beat guidance" in e
    assert got["8-K"]["items"] == "2.02,9.01"


def test_non_us_issuer_without_cik_returns_empty():
    http = FakeHttp({})
    st = FakeStore()
    assert filings.fetch_filings("SHEL.L", date(2023, 1, 1), date(2023, 12, 31),
                                 http_get=http, store=st, resolve=lambda s: None) == 0
    assert http.calls == []
    out = filings.filing_dossier("SHEL.L", utc(2023, 6, 5, 13), store=st,
                                 resolve=lambda s: None)
    assert out["filings"] == []
    assert out["factors"]["symbol"] == "SHEL.L"
    assert out["factors"]["gp_a"] is None
    assert not filings.has_operating_filings("SHEL.L", utc(2023, 6, 5, 13), store=st)


# -- R7: the operating-filings index -------------------------------------------------

def test_index_rows_for_every_operating_filing_even_without_excerpt():
    """A 6-K accepted before the excerpt range still gets an index row (no
    document fetch), so has_operating_filings answers from the store alone."""
    acc_6, acc_20 = "0001234567-21-000001", "0001234567-22-000002"
    http = FakeHttp({
        SUB_URL: Resp(payload=submissions([
            ("20-F", acc_20, "2022-04-28T10:00:00.000Z", "f.htm", ""),
            ("6-K", acc_6, "2021-08-02T11:00:00.000Z", "s.htm", ""),
            ("SC 13G", "0001234567-22-000009", "2022-02-01T10:00:00.000Z", "g.htm", ""),
        ])),
        doc_url(acc_20, "f.htm"): Resp(text=html(
            "Item 3. Key Information", "D. Risk Factors", "Currency risk is high.",
            "Item 4. Information on the Company", "We make chips.",
            "Item 5. Operating and Financial Review and Prospects",
            "Wafer revenue rose.", "Item 6. Directors, Senior Management and Employees")),
    })
    st = FakeStore()
    filings.fetch_filings("TSM", date(2022, 1, 1), date(2022, 12, 31), http_get=http,
                          store=st, resolve=lambda s: CIK)
    assert doc_url(acc_6, "s.htm") not in http.calls        # out of range: index only
    rows = st.run(st.between("TrendFilingsPIT", "TSM|", "TSM}"))
    assert sorted(r["form"] for r in rows) == ["20-F", "6-K"]   # SC 13G not stored
    assert filings.has_operating_filings("TSM", utc(2021, 9, 1), store=st)
    assert not filings.has_operating_filings("TSM", utc(2021, 8, 2, 10), store=st)

    (f20,) = [f for f in dossier(st, utc(2022, 5, 2), "TSM")["filings"]
              if f["form"] == "20-F"]
    assert "Wafer revenue rose" in f20["excerpt"] and "Currency risk" in f20["excerpt"]


def test_fund_filings_do_not_count_as_operating():
    http = FakeHttp({SUB_URL: Resp(payload=submissions([
        ("N-CSR", "0000000001-23-000001", "2023-03-01T10:00:00.000Z", "n.htm", ""),
        ("NPORT-P", "0000000001-23-000002", "2023-04-01T10:00:00.000Z", "p.xml", ""),
        ("485BPOS", "0000000001-23-000003", "2023-05-01T10:00:00.000Z", "b.htm", "")]))})
    st = FakeStore()
    assert filings.fetch_filings("ARKK", date(2023, 1, 1), date(2023, 12, 31),
                                 http_get=http, store=st, resolve=lambda s: CIK) == 0
    assert not filings.has_operating_filings("ARKK", utc(2024, 1, 1), store=st)


def test_has_operating_filings_does_not_match_a_longer_symbol():
    st, _http, _n = ten_q_store()
    assert not filings.has_operating_filings("AAP", utc(2024, 1, 1), store=st)
    assert filings.has_operating_filings("aapl", utc(2024, 1, 1), store=st)


# -- excerpt extraction ----------------------------------------------------------------

def test_ten_q_excerpt_is_the_body_not_the_table_of_contents():
    st, _http, _n = ten_q_store()
    (f,) = dossier(st, utc(2023, 6, 1))["filings"]
    ex = f["excerpt"]
    assert "Revenue grew 12%" in ex
    assert "New export controls" in ex                 # Part II risk-factor changes
    assert "Balance sheet numbers" not in ex
    assert "Controls are fine" not in ex
    assert "Buybacks happened" not in ex


def test_eight_k_excerpt_stops_before_exhibits_and_signatures():
    acc = "0000320193-23-000075"
    doc = html("FORM 8-K", "Check the appropriate box below", "Item 8.01 Other Events",
               "The company announced a $90B buyback.",
               "Item 9.01 Financial Statements and Exhibits", "Exhibit 99.1 Press release",
               "SIGNATURES", "Pursuant to the requirements")
    http = FakeHttp({
        SUB_URL: Resp(payload=submissions([("8-K", acc, "2023-05-10T20:31:27.000Z",
                                            "e.htm", "8.01,9.01")])),
        doc_url(acc, "e.htm"): Resp(text=doc)})
    st = FakeStore()
    filings.fetch_filings("AAPL", date(2023, 5, 1), date(2023, 5, 31), http_get=http,
                          store=st, resolve=lambda s: CIK)
    (f,) = dossier(st, utc(2023, 5, 11))["filings"]
    assert "$90B buyback" in f["excerpt"]
    assert "Exhibit 99.1" not in f["excerpt"]
    assert "Pursuant to" not in f["excerpt"]


def test_twenty_f_risk_factors_under_a_bare_heading():
    """TSMC's 20-F titles Item 3.D just "Risk Factors"; mid-sentence
    cross-references to it are not headings."""
    doc = html("Please see \u201cItem 3. Key Information \u2013 Risk Factors\u201d.",
               "ITEM 3. KEY INFORMATION", "Capitalization and Indebtedness",
               "Not applicable.", "Risk Factors", "Customer concentration is high.",
               "ITEM 4. INFORMATION ON THE COMPANY", "We make wafers.")
    ex = filings.extract_excerpt("20-F", doc)
    assert ex == "[Risk factors] Customer concentration is high."


def test_six_k_report_after_a_signature_first_cover():
    doc = html("FORM 6-K", "REPORT OF FOREIGN PRIVATE ISSUER",
               "Indicate by check mark whether the registrant files or will file annual "
               "reports under cover of Form 20-F or Form 40-F.",
               "Form 20-F x Form 40-F o",
               "Indicate by check mark if the registrant is submitting the Form 6-K in "
               "paper as permitted by Regulation S-T Rule 101(b)(1):o",
               "SIGNATURES", "Pursuant to the requirements of the Exchange Act.",
               "Date: April 10, 2024 By /s/ Jane Roe", "Jane Roe", "Chief Financial Officer",
               "March 2024 Revenue Report",
               "Revenue for March 2024 rose 34.3 percent year over year.")
    ex = filings.extract_excerpt("6-K", doc)
    assert ex.startswith("March 2024 Revenue Report")
    assert "34.3 percent" in ex and "Jane Roe" not in ex and "check mark" not in ex


def test_six_k_report_before_its_signatures():
    body = "The board approved a dividend of 4.5 per share payable in July. " * 5
    doc = html("Form 20-F x Form 40-F o", body, "SIGNATURES", "By /s/ Jane Roe")
    assert filings.extract_excerpt("6-K", doc) == body.strip()


def test_html_is_reduced_to_text():
    text = filings.html_to_text(
        "<html><head><style>p{color:red}</style><script>x=1</script></head><body>"
        "<ix:header><ix:hidden>dei:Secret</ix:hidden></ix:header>"
        "<div>Net&nbsp;sales &amp; margin</div><p>Line&#8217;two</p></body></html>")
    assert "color" not in text and "x=1" not in text and "Secret" not in text
    assert "Net sales & margin" in text
    assert "Line’two" in text


# -- fetch behaviour ---------------------------------------------------------------------

def test_rerun_fetches_no_documents_and_writes_nothing():
    st, http, _n = ten_q_store()
    before = len(http.calls)
    http2 = FakeHttp(http.routes)
    assert filings.fetch_filings("AAPL", date(2023, 5, 1), date(2023, 5, 31),
                                 http_get=http2, store=st, resolve=lambda s: CIK) == 0
    assert http2.calls == [SUB_URL]
    assert before == 2


def test_index_only_row_gets_its_excerpt_when_a_later_run_covers_it():
    # First run: the excerpt range ends before the filing, so only an index row.
    st, http, n = ten_q_store(start=date(2023, 6, 1), end=date(2023, 6, 30))
    assert n == 1
    assert dossier(st, utc(2023, 7, 1))["filings"] == []      # no excerpt yet
    assert filings.has_operating_filings("AAPL", utc(2023, 7, 1), store=st)
    # Second run covers May: the stored row gains its excerpt in place.
    n2 = filings.fetch_filings("AAPL", date(2023, 5, 1), date(2023, 5, 31),
                               http_get=FakeHttp(http.routes), store=st,
                               resolve=lambda s: CIK)
    assert n2 == 1
    (f,) = dossier(st, utc(2023, 7, 1))["filings"]
    assert f["available_at"] == "2023-05-24T20:05:12Z"
    assert "Greater China" in f["excerpt"]


def test_older_submission_pages_are_read_when_they_overlap_the_range():
    old_url = "https://data.sec.gov/submissions/CIK0000320193-submissions-001.json"
    ancient_url = "https://data.sec.gov/submissions/CIK0000320193-submissions-002.json"
    page = submissions([("10-K", "0000320193-20-000096", "2020-10-29T22:00:00.000Z",
                         "k.htm", "")])["filings"]["recent"]
    http = FakeHttp({
        SUB_URL: Resp(payload=submissions([], files=[
            {"name": "CIK0000320193-submissions-001.json", "filingFrom": "2019-01-01",
             "filingTo": "2021-03-01"},
            {"name": "CIK0000320193-submissions-002.json", "filingFrom": "1994-01-01",
             "filingTo": "2010-01-01"}])),
        old_url: Resp(payload=page),
    })
    st = FakeStore()
    filings.fetch_filings("AAPL", date(2021, 1, 1), date(2021, 12, 31), http_get=http,
                          store=st, resolve=lambda s: CIK)
    assert old_url in http.calls and ancient_url not in http.calls
    # accepted before the range: index row only, but it answers R7
    assert filings.has_operating_filings("AAPL", utc(2021, 1, 4, 14), store=st)


def test_transient_errors_are_retried():
    acc = "0000320193-23-000064"
    sleeps = []
    http = FakeHttp({
        SUB_URL: [Resp(503), Resp(429), Resp(payload=submissions(
            [("10-Q", acc, "2023-05-24T20:05:12.000Z", "q.htm", "")]))],
        doc_url(acc, "q.htm"): [Resp(500), Resp(text=TEN_Q_DOC)],
    })
    st = FakeStore()
    assert filings.fetch_filings("AAPL", date(2023, 5, 1), date(2023, 5, 31),
                                 http_get=http, store=st, resolve=lambda s: CIK,
                                 sleep=sleeps.append) == 1
    assert len(sleeps) == 3


def test_unknown_cik_on_edgar_is_empty_not_an_error():
    st = FakeStore()
    assert filings.fetch_filings("AAPL", date(2023, 5, 1), date(2023, 5, 31),
                                 http_get=FakeHttp({}), store=st,
                                 resolve=lambda s: CIK) == 0


def test_hard_http_error_raises():
    http = FakeHttp({SUB_URL: Resp(403, text="Request Rate Threshold Exceeded")})
    with pytest.raises(filings.FilingsFetchError):
        filings.fetch_filings("AAPL", date(2023, 5, 1), date(2023, 5, 31), http_get=http,
                              store=FakeStore(), resolve=lambda s: CIK)


def test_a_missing_document_is_recorded_and_not_refetched():
    acc = "0000320193-23-000064"
    http = FakeHttp({SUB_URL: Resp(payload=submissions(
        [("10-Q", acc, "2023-05-24T20:05:12.000Z", "q.htm", "")]))})
    st = FakeStore()
    filings.fetch_filings("AAPL", date(2023, 5, 1), date(2023, 5, 31), http_get=http,
                          store=st, resolve=lambda s: CIK)
    assert doc_url(acc, "q.htm") in http.calls
    http2 = FakeHttp(http.routes)
    filings.fetch_filings("AAPL", date(2023, 5, 1), date(2023, 5, 31), http_get=http2,
                          store=st, resolve=lambda s: CIK)
    assert http2.calls == [SUB_URL]
    assert dossier(st, utc(2023, 6, 1))["filings"] == []


# -- dossier -------------------------------------------------------------------------------

def _f(start, end, val, filed):
    return {"start": start, "end": end, "val": val, "filed": filed, "form": "10-K"}


def _i(end, val, filed):
    return {"end": end, "val": val, "filed": filed, "form": "10-K"}


FACTS = {"facts": {"us-gaap": {
    "Assets": {"units": {"USD": [_i("2021-12-31", 1000, "2022-02-10"),
                                 _i("2022-12-31", 1200, "2023-05-22")]}},
    "GrossProfit": {"units": {"USD": [_f("2021-01-01", "2021-12-31", 300, "2022-02-10"),
                                      _f("2022-01-01", "2022-12-31", 400, "2023-05-22")]}},
}}}


def test_dossier_factors_are_a_plain_dict_gated_before_the_decision_day():
    st = FakeStore()
    # Monday 2023-05-22 09:25 ET: FY2022 was filed that same day, so it is not
    # provably public yet -- the factors are FY2021's.
    out = filings.filing_dossier("AAPL", utc(2023, 5, 22, 13, 25), store=st,
                                 resolve=lambda s: CIK, fetch_facts=lambda cik: FACTS)
    fac = out["factors"]
    assert isinstance(fac, dict)
    assert fac["period_end"] == "2021-12-31" and fac["gp_a"] == 300 / 1000
    later = filings.filing_dossier("AAPL", utc(2023, 5, 23, 13, 25), store=st,
                                   resolve=lambda s: CIK, fetch_facts=lambda cik: FACTS)
    assert later["factors"]["period_end"] == "2022-12-31"
    assert later["factors"]["gp_a"] == 400 / 1200


def test_dossier_keeps_the_newest_excerpted_filings_newest_first():
    rows = []
    for i, day in enumerate((3, 10, 17, 24)):
        at = "2023-05-%02dT20:00:00Z" % day
        rows.append({"id": "AAPL|%s|acc-%d" % (at, i), "symbol": "AAPL", "form": "8-K",
                     "accession": "acc-%d" % i, "available_at": at,
                     "excerpt": "news %d" % i, "excerpt_status": "ok"})
    rows.append({"id": "AAPL|2023-05-25T20:00:00Z|acc-x", "symbol": "AAPL", "form": "10-Q",
                 "accession": "acc-x", "available_at": "2023-05-25T20:00:00Z"})
    st = FakeStore()
    st.insert("TrendFilingsPIT", rows)
    out = filings.filing_dossier("AAPL", utc(2023, 5, 26), store=st, max_filings=3,
                                 resolve=lambda s: None)
    assert [f["accession"] for f in out["filings"]] == ["acc-3", "acc-2", "acc-1"]
