#!/usr/bin/env python3
"""Create the TR lab document and its backtest-only instance.

    python3 scripts/tr_lab_setup.py --chain honest-1 --model <model> --cutoff 2025-03-01
    python3 scripts/tr_lab_setup.py ... --prompt-version tr-v1

Doc "TR lab" holds one enabled ``strategy_trend_research`` lane: the lane's
own schema header (the first line of backend/strategies/strategy_trend_research.py)
with ``strategy_trend_research_enabled`` on and the ledger selector
(``tr_chain_id``, ``tr_model``, ``tr_prompt_version``) plus
``research_model_cutoff`` from the flags. ``--model`` is the model string the
ledger rows carry (the ``model`` field of TrendResearchLedger), not a Models
row id.

The instance is ``strategy-tr-lab`` at granularity "900", watchlist the
lane's fixed ETF set (the lane adds the ledger's names to the bar fetch
itself). A new instance takes ``strategy-eb``'s brokerage, an existing one
keeps its own; either way the brokerage must be provably a paper Alpaca
account that is not ``alpaca-main``'s, checked BEFORE anything is written,
or the script refuses.

Idempotent: a rerun finds the doc by name and PUTs the lane, and re-links an
existing instance. Docs 200 and 201 are REFUSED in every spelling, and the
only instance this script writes is ``strategy-tr-lab``; ``alpaca-main`` and
``strategy-eb`` are only read. Nothing is started here.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import date

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(_ROOT, "scripts"))
sys.path.insert(0, os.path.join(_ROOT, "backend"))

from strategies_meta import _parse_header_meta  # noqa: E402
from trend_research import book  # noqa: E402

DOC_NAME = "TR lab"
INSTANCE_ID = "strategy-tr-lab"
CLONE_FROM = "strategy-eb"
LIVE_INSTANCE_ID = "alpaca-main"
PROTECTED_DOC_IDS = frozenset({"200", "201"})
GRANULARITY = "900"
LANE_FILE = os.path.join(_ROOT, "backend", "strategies", "strategy_trend_research.py")
#: The lane's fixed ETF set. Ledger picks are added to the bar fetch by the
#: broker (strategy_trend_research.trend_research_universe), not listed here.
STOCKS = sorted(book.ALL_ALLOWED_ETFS)

#: `_http` formats an HTTPError as "HTTP <code> on <method> <url>\n<body>".
_DUPLICATE_CODES = ("400", "409", "422")


def _is_duplicate(error) -> bool:
    text = str(error).lower()
    return "already" in text and any(f"http {code}" in text for code in _DUPLICATE_CODES)


def _doc_number(doc_id) -> str:
    """"0200", 200.0, "200.0" and " 200 " are all "200"."""
    text = str(doc_id).strip()
    try:
        number = float(text)
    except ValueError:
        return text
    return str(int(number)) if number.is_integer() else text


def assert_writable(doc_id):
    if doc_id in (None, ""):
        raise SystemExit("REFUSING: the API returned no document id")
    if _doc_number(doc_id) in PROTECTED_DOC_IDS:
        raise SystemExit(
            f"REFUSING to write doc {doc_id}: 200 is the live champion and 201 is the "
            "EB lab. TR runs in its own document or not at all.")
    return doc_id


def lane_schema(path: str = LANE_FILE) -> dict:
    """The lane's INTELLISTOCK_SCHEMA header, parsed the way the API parses it."""
    with open(path, encoding="utf-8") as fh:
        schema, _desc = _parse_header_meta(fh.read())
    if not isinstance(schema, dict) or not isinstance(schema.get("config"), dict):
        raise SystemExit(f"no INTELLISTOCK_SCHEMA header in {path}")
    return schema


def lane(*, chain: str, model: str, prompt_version: str, cutoff: str) -> dict:
    spec = json.loads(json.dumps(lane_schema()))          # a private deep copy
    spec["config"].update({
        "strategy_trend_research_enabled": True,
        "tr_chain_id": chain,
        "tr_model": model,
        "tr_prompt_version": prompt_version,
        "research_model_cutoff": cutoff,
    })
    return spec


def doc_payload(**selector) -> dict:
    return {"name": DOC_NAME, "strategies": [lane(**selector)]}


def _rows(payload):
    if isinstance(payload, list):
        return payload
    for key in ("strategies", "accounts", "items", "rows"):
        if isinstance(payload, dict) and isinstance(payload.get(key), list):
            return payload[key]
    return []


def _safe_get(call, path):
    try:
        return call("GET", path)
    except (SystemExit, Exception) as error:  # _api.call SystemExits on 4xx
        return (404 if "404" in str(error) else 500), None


def assert_paper_brokerage(call, brokerage_id) -> dict:
    """Refuse unless `brokerage_id` is a paper Alpaca account that is not
    alpaca-main's. Returns the brokerage row (credentials masked by the API)."""
    if not brokerage_id:
        raise SystemExit("REFUSING: no brokerage to link")
    code, live = _safe_get(call, f"/instances/{LIVE_INSTANCE_ID}")
    if not live:
        raise SystemExit(f"REFUSING: {LIVE_INSTANCE_ID} could not be read (HTTP {code}), "
                         "so this cannot prove the brokerage is not the real-money account")
    if str(live.get("brokerage_id") or "") == str(brokerage_id):
        raise SystemExit(f"REFUSING: brokerage {brokerage_id} is {LIVE_INSTANCE_ID}'s, "
                         "the real-money account")
    _, listing = call("GET", "/brokerages")
    row = next((b for b in _rows(listing) if str(b.get("id")) == str(brokerage_id)), None)
    if row is None:
        raise SystemExit(f"REFUSING: brokerage {brokerage_id} not found")
    if row.get("alpaca_paper") is not True:
        raise SystemExit(f"REFUSING: brokerage {brokerage_id} is not marked as an Alpaca "
                         f"paper account (alpaca_paper={row.get('alpaca_paper')!r})")
    return row


def _upsert_doc(call, payload):
    _, docs = call("GET", "/strategies")
    existing = next((d for d in _rows(docs) if d.get("name") == DOC_NAME), None)
    if existing:
        doc_id = assert_writable(existing.get("id"))
        call("PUT", f"/strategies/{doc_id}", payload)
        print("lab doc updated:", doc_id)
        return doc_id
    _, created = call("POST", "/strategies", payload)
    created = created or {}
    doc_id = assert_writable(created.get("id") or created.get("strategy_id")
                             or created.get("new_id"))
    print("lab doc created:", doc_id)
    return doc_id


def _cutoff(text: str) -> str:
    try:
        return date.fromisoformat(text).isoformat()
    except ValueError:
        raise argparse.ArgumentTypeError(f"--cutoff must be YYYY-MM-DD, got {text!r}")


def _selector_part(text: str) -> str:
    value = (text or "").strip()
    if not value or "|" in value:
        raise argparse.ArgumentTypeError("must be a non-empty string without '|'")
    return value


def parse_args(argv=None):
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--chain", required=True, type=_selector_part,
                    help="tr_chain_id: the ledger chain the lane replays")
    ap.add_argument("--model", required=True, type=_selector_part,
                    help="tr_model: the model string on the chain's ledger rows")
    ap.add_argument("--cutoff", required=True, type=_cutoff,
                    help="research_model_cutoff YYYY-MM-DD (scorecard honest/optimistic split)")
    ap.add_argument("--prompt-version", default="tr-v1", type=_selector_part,
                    help="tr_prompt_version (default tr-v1)")
    return ap.parse_args(argv)


def main(argv=None, *, call=None) -> int:
    args = parse_args(argv)
    if call is None:
        from _api import call as call  # noqa: PLW0127
    payload = doc_payload(chain=args.chain, model=args.model,
                          prompt_version=args.prompt_version, cutoff=args.cutoff)

    # Every check runs before the first write: a refusal leaves nothing behind.
    code, inst = _safe_get(call, f"/instances/{INSTANCE_ID}")
    if code not in (200, 404) and not inst:
        raise SystemExit(f"REFUSING: {INSTANCE_ID} could not be read (HTTP {code})")
    if inst:
        brokerage_id = inst.get("brokerage_id")
        source = f"{INSTANCE_ID}'s own"
    else:
        _ccode, clone = _safe_get(call, f"/instances/{CLONE_FROM}")
        if not clone:
            raise SystemExit(f"REFUSING: {CLONE_FROM} could not be read (HTTP {_ccode}), "
                             "so there is no brokerage to clone")
        brokerage_id = clone.get("brokerage_id")
        source = f"cloned from {CLONE_FROM}"
    broker_row = assert_paper_brokerage(call, brokerage_id)
    print(f"brokerage {brokerage_id} ({source}): paper account "
          f"{broker_row.get('account_name')!r}")

    doc_id = _upsert_doc(call, payload)
    if inst:
        # PATCH /instances/{id} silently drops strategy_id; link-strategy is the route.
        call("POST", f"/instances/{INSTANCE_ID}/link-strategy", {"strategy_id": int(doc_id)})
        print("instance exists; strategy linked to", doc_id)
    else:
        # `granularity`, not `granularity_time_increment`: CreateInstanceBody's
        # field. Under any other name it is dropped and the instance is created
        # at the 60 s default.
        call("POST", "/instances", {"id": INSTANCE_ID, "name": "TR lab (backtest only)",
                                    "strategy_id": int(doc_id), "granularity": GRANULARITY,
                                    "brokerage_id": brokerage_id, "stocks": list(STOCKS)})
        print("created instance", INSTANCE_ID)

    for symbol in STOCKS:
        try:
            call("POST", f"/instances/{INSTANCE_ID}/stocks", {"symbol": symbol})
        except SystemExit as error:
            if not _is_duplicate(error):
                raise

    _, check = call("GET", f"/instances/{INSTANCE_ID}")
    check = check or {}
    print("instance:", json.dumps({k: check.get(k) for k in (
        "id", "strategy_id", "runCommand", "granularity_time_increment", "brokerage_id")}))
    if str(check.get("granularity_time_increment") or "") not in ("", GRANULARITY):
        print(f"WARNING: granularity is {check.get('granularity_time_increment')}, not "
              f"{GRANULARITY}; tr_backtest.py passes 900 per POST regardless.")
    print("NOT started. Backtest with scripts/tr_backtest.py (one run at a time).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
