from __future__ import annotations


def _normalize_strategy_name(value) -> str:
    return str(value or "").strip().lower()


def build_nexus_buy_guard(
    strategy_summary: list[dict] | None,
    symbol: str,
    decision: int,
    nexus_executable_buys,
    nexus_position_sizes: dict | None,
) -> dict:
    summary = list(strategy_summary or [])
    buy_entries = []
    for entry in summary:
        try:
            entry_decision = int(entry.get("decision", 0))
        except Exception:
            entry_decision = 0
        if entry_decision == 1:
            buy_entries.append(entry)

    nexus_buy = any(_normalize_strategy_name(entry.get("strategy")) == "graph_nexus_analysis" for entry in buy_entries)
    other_buy = any(_normalize_strategy_name(entry.get("strategy")) != "graph_nexus_analysis" for entry in buy_entries)
    hint = (nexus_position_sizes or {}).get(symbol) if isinstance(nexus_position_sizes, dict) else {}
    if not isinstance(hint, dict):
        hint = {}
    executable_set = {str(sym).strip().upper() for sym in (nexus_executable_buys or []) if str(sym).strip()}
    return {
        "is_nexus_only_buy": int(decision or 0) == 1 and nexus_buy and not other_buy,
        "has_nexus_buy_vote": nexus_buy,
        "is_whitelisted": str(symbol or "").strip().upper() in executable_set,
        "has_buy_cash_hint": float(hint.get("buy_cash", 0.0) or 0.0) > 0.0,
        "asset_class": str(hint.get("asset_class") or "stock").strip().lower() or "stock",
        "buy_price_floor": float((nexus_position_sizes or {}).get("_buy_price_floor", 0.0) or 0.0),
        "hint": dict(hint) if isinstance(hint, dict) else {},
    }


def get_nexus_buy_block_details(symbol: str, price: float, guard: dict | None) -> dict | None:
    guard = guard or {}
    if not guard.get("is_nexus_only_buy"):
        return None
    if not guard.get("is_whitelisted"):
        return {
            "code": "not_whitelisted",
            "message": f"Nexus execution gate: {symbol} not in executable buy whitelist",
        }
    if not guard.get("has_buy_cash_hint"):
        return {
            "code": "missing_buy_cash_hint",
            "message": f"Nexus execution gate: {symbol} missing buy_cash hint",
        }
    buy_price_floor = float(guard.get("buy_price_floor", 0.0) or 0.0)
    is_etf = str(guard.get("asset_class") or "").strip().lower() == "etf"
    if buy_price_floor > 0.0 and not is_etf and float(price or 0.0) > 0.0 and float(price or 0.0) < buy_price_floor:
        return {
            "code": "buy_price_floor",
            "message": f"Nexus execution price floor: {symbol} at ${float(price):.2f} is below ${buy_price_floor:.2f}",
        }
    return None


def get_nexus_buy_block_reason(symbol: str, price: float, guard: dict | None) -> str | None:
    details = get_nexus_buy_block_details(symbol, price, guard)
    if not details:
        return None
    return str(details.get("message") or "") or None
