#!/usr/bin/env python3
"""Export one TR research chain's ledger as a readable Markdown journal.

    python3 scripts/tr_journal.py --chain honest-1
    python3 scripts/tr_journal.py --chain honest-1 --model <model> --start 2025-01-06 \\
        --end 2025-12-29 --out /tmp/tr-honest-1.md

One section per ledger week (TrendResearchLedger, key
``chain|YYYY-MM-DD|model|prompt_version``): status and LLM calls used, the
regime and its reasons, the trends (state, confidence), the picks with
conviction, weight, thesis and breaks-if, the names that left the book since
the chain's previous ``ok`` week (the research's sells; the engine's trailing
stops are not in the ledger), and every dropped candidate with its stage and
reason. A failed week shows its failed stage and error.

Read-only. There is no API endpoint for ledger rows, so this reads through
``db.store`` on a connection whose session has
``default_transaction_read_only=on``: any write the process attempted would
be refused by Postgres. (``PGOPTIONS`` cannot do it: ``db.pool`` passes an
explicit ``options`` that overrides the environment.) Credentials come from
the primary checkout's .env (``POSTGRES_*``), host defaulting to server7;
nothing is printed but the journal.
"""
from __future__ import annotations

import argparse
import os
import sys
from datetime import date
from pathlib import Path

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_PRIMARY = Path("/Users/pranavkrishna/PranavFiles/coding-projects/IntelliStock")
sys.path.insert(0, os.path.join(_ROOT, "scripts"))
sys.path.insert(0, os.path.join(_ROOT, "backend"))

from trend_research import ledger  # noqa: E402

READ_ONLY_OPTION = "-c default_transaction_read_only=on"


def read_only_store(*, load_env: bool = True):
    """``db.store`` with every pooled connection in a read-only session."""
    if load_env:
        sys.path.insert(0, str(_PRIMARY / "scripts"))
        from pull_backtest_logs import _load_dotenv

        _load_dotenv(_PRIMARY)
        _load_dotenv(Path(_ROOT))
    os.environ.setdefault("POSTGRES_HOST", "server7")
    from db import pool, store

    if pool._pool is not None:
        raise SystemExit("REFUSING: the store pool is already open, so it cannot be made "
                         "read-only")
    base = pool._options
    pool._options = lambda: base() + " " + READ_ONLY_OPTION
    return store


# -- reading ---------------------------------------------------------------------------


def chain_rows(chain: str, *, model=None, prompt_version=None, start=None, end=None,
               store) -> list:
    """The chain's rows (any status), oldest week first; `start`/`end` are
    inclusive week-start dates."""
    lo = chain + "|" + (start.isoformat() if start else "")
    hi = chain + "|" + (end.isoformat() + "|￿" if end else "￿")
    rows = []
    for row in store.iter(store.between(ledger.TABLE, lo, hi)):
        if row.get("chain_id") != chain:
            continue
        if model and row.get("model") != model:
            continue
        if prompt_version and row.get("prompt_version") != prompt_version:
            continue
        rows.append(row)
    rows.sort(key=lambda r: (str(r.get("week_start")), str(r.get("model")),
                             str(r.get("prompt_version"))))
    return rows


# -- rendering -------------------------------------------------------------------------


def _cell(value) -> str:
    text = "" if value is None else str(value)
    return " ".join(text.split()).replace("|", "\\|")


def _num(value, fmt="{:.2f}") -> str:
    try:
        return fmt.format(float(value))
    except (TypeError, ValueError):
        return "-"


def _picks(row) -> list:
    return [p for p in ((row.get("book") or {}).get("picks") or []) if isinstance(p, dict)]


def _pick_symbols(row) -> set:
    return {str(p.get("symbol") or "").upper() for p in _picks(row) if p.get("symbol")}


def render_week(row: dict, previous_ok: dict | None = None) -> str:
    scan = row.get("scan") or {}
    notes = {str(n.get("symbol") or "").upper(): n
             for n in row.get("notes") or [] if isinstance(n, dict)}
    out = [f"## Week of {row.get('week_start')}", ""]
    out.append(f"- Status: **{row.get('status')}**"
               + (f" (failed at `{row.get('failed_stage')}`: {_cell(row.get('error'))})"
                  if row.get("status") != "ok" else ""))
    out.append(f"- As of: {row.get('as_of')} | model `{row.get('model')}` "
               f"({row.get('provider')}) | prompt `{row.get('prompt_version')}`")
    out.append(f"- LLM calls used: {row.get('calls_used', '-')}")
    if scan:
        out.append(f"- Regime: **{scan.get('regime')}** - {_cell(scan.get('regime_reasons'))}")
    out.append("")

    trends = [t for t in scan.get("trends") or [] if isinstance(t, dict)]
    if trends:
        out += ["### Trends", "", "| Trend | Category | State | Confidence | Summary |",
                "|---|---|---|---|---|"]
        for t in trends:
            out.append(f"| {_cell(t.get('name'))} | {_cell(t.get('category'))} | "
                       f"{_cell(t.get('state'))} | {_num(t.get('confidence'))} | "
                       f"{_cell(t.get('summary'))} |")
        out.append("")

    picks = _picks(row)
    if picks:
        out += ["### Picks", ""]
        for p in picks:
            sym = str(p.get("symbol") or "").upper()
            note = notes.get(sym) or {}
            out.append(f"- **{sym}** - conviction {_num(p.get('conviction'), '{:.1f}')}, "
                       f"weight {_num(p.get('target_weight'), '{:.1%}')}, role "
                       f"{p.get('role')}"
                       + (f", thesis {note.get('thesis_status')}, priced in "
                          f"{note.get('priced_in')}" if note else ""))
            if note.get("thesis"):
                out.append(f"  - Thesis: {_cell(note['thesis'])}")
            breaks = note.get("breaks_if") or []
            if breaks:
                out.append("  - Breaks if: " + "; ".join(_cell(b) for b in breaks))
            if note.get("risks"):
                out.append(f"  - Risks: {_cell(note['risks'])}")
        book = row.get("book") or {}
        out.append("")
        out.append(f"Defensive share {_num(book.get('defensive_share'), '{:.0%}')}. "
                   f"{_cell(book.get('notes'))}".rstrip())
        out.append("")

    if previous_ok is not None and row.get("status") == "ok":
        left = sorted(_pick_symbols(previous_ok) - _pick_symbols(row))
        if left:
            out += ["### Left the book", ""]
            for sym in left:
                note = notes.get(sym)
                why = (f"thesis {note.get('thesis_status')}: {_cell(note.get('thesis'))}"
                       if note else "not picked this week")
                out.append(f"- **{sym}** (held after week {previous_ok.get('week_start')}) "
                           f"- {why}")
            out.append("")

    dropped = [d for d in row.get("dropped") or [] if isinstance(d, dict)]
    if dropped:
        out += ["### Dropped candidates", "", "| Symbol | Raw | Stage | Reason |",
                "|---|---|---|---|"]
        for d in dropped:
            out.append(f"| {_cell(d.get('symbol'))} | {_cell(d.get('raw'))} | "
                       f"{_cell(d.get('stage'))} | {_cell(d.get('reason'))} |")
        out.append("")
    return "\n".join(out)


def render_journal(rows: list, *, chain: str) -> str:
    ok = sum(1 for r in rows if r.get("status") == "ok")
    calls = sum(int(r.get("calls_used") or 0) for r in rows)
    head = [f"# TR research journal - chain `{chain}`", "",
            f"{len(rows)} week(s): {ok} ok, {len(rows) - ok} failed; {calls} LLM calls.",
            "Sells listed here are the research's (names that left the book); the engine's "
            "20% trailing stops happen in the backtest, not in the ledger.", ""]
    body, previous_ok = [], {}
    for row in rows:
        series = (row.get("model"), row.get("prompt_version"))
        body.append(render_week(row, previous_ok.get(series)))
        if row.get("status") == "ok":
            previous_ok[series] = row
    return "\n".join(head + body).rstrip() + "\n"


def parse_args(argv=None):
    def day(text):
        try:
            return date.fromisoformat(text)
        except ValueError:
            raise argparse.ArgumentTypeError(f"expected YYYY-MM-DD, got {text!r}")

    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--chain", required=True)
    ap.add_argument("--model", help="only rows of this model string")
    ap.add_argument("--prompt-version", help="only rows of this prompt version")
    ap.add_argument("--start", type=day, help="first week start (inclusive)")
    ap.add_argument("--end", type=day, help="last week start (inclusive)")
    ap.add_argument("--out", help="write here instead of stdout")
    args = ap.parse_args(argv)
    if not args.chain.strip() or "|" in args.chain:
        ap.error("--chain must be non-empty and contain no '|'")
    return args


def main(argv=None, *, store=None) -> int:
    args = parse_args(argv)
    if store is None:
        store = read_only_store()
    rows = chain_rows(args.chain.strip(), model=args.model,
                      prompt_version=args.prompt_version, start=args.start, end=args.end,
                      store=store)
    text = render_journal(rows, chain=args.chain.strip())
    if args.out:
        Path(args.out).write_text(text, encoding="utf-8")
        print(f"wrote {len(rows)} week(s) to {args.out}", file=sys.stderr)
    else:
        sys.stdout.write(text)
    return 0 if rows else 1


if __name__ == "__main__":
    raise SystemExit(main())
