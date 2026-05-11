"""
Standalone Robinhood interactive CLI (testing-only).

WARNING:
- Uses Robinhood private/undocumented endpoints via backend/robinhood_engine.py.
- This may break at any time and may violate Robinhood's terms.
- Never run on an untrusted machine; it handles account credentials and stores tokens
  locally (unless --no-save).
"""

from __future__ import annotations

import argparse
import getpass
import json
import os
import sys
import time
from pathlib import Path
from typing import Any


def _ensure_backend_on_path() -> None:
    root = Path(__file__).resolve().parent
    backend_dir = root / "backend"
    if backend_dir.exists():
        sys.path.insert(0, str(backend_dir))


_ensure_backend_on_path()

try:
    from robinhood_engine import (
        RobinhoodAPIError,
        RobinhoodChallengeRequired,
        RobinhoodClient,
        RobinhoodMFARequired,
        RobinhoodSessionState,
        get_live_prices,
    )
except Exception as e:  # pragma: no cover
    raise SystemExit(f"Failed to import backend Robinhood engine: {e}")


DEFAULT_SESSION_FILE = Path.home() / ".intellistock" / "robinhood_session.json"


def _safe_float(v: Any) -> float | None:
    try:
        if v is None:
            return None
        return float(v)
    except Exception:
        return None


def _fmt_money(v: Any) -> str:
    f = _safe_float(v)
    return f"${f:,.2f}" if f is not None else "N/A"


def _load_session(path: Path) -> RobinhoodSessionState:
    try:
        if not path.exists():
            return RobinhoodSessionState()
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return RobinhoodSessionState()
        return RobinhoodSessionState.from_dict(data)
    except Exception:
        return RobinhoodSessionState()


def _atomic_write_json(path: Path, obj: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(obj, indent=2, sort_keys=True), encoding="utf-8")
    tmp.replace(path)


def _should_refresh(state: RobinhoodSessionState) -> bool:
    if not state.refresh_token:
        return False
    if not state.expires_in or not state.obtained_at_epoch:
        return False
    # Refresh ~60s early.
    return int(time.time()) >= int(state.obtained_at_epoch) + int(state.expires_in) - 60


def _prompt_secret(prompt: str, *, masked: bool) -> str:
    if masked:
        return getpass.getpass(prompt)
    return input(prompt)


def _interactive_login(client: RobinhoodClient, *, masked: bool) -> None:
    print("\nRobinhood login (interactive)")
    username = input("Email: ").strip()
    password = _prompt_secret("Password: ", masked=masked)
    if not username or not password:
        raise SystemExit("Email and password are required.")

    challenge_response_id: str | None = None
    mfa_code: str | None = None
    attempts = 0
    while True:
        attempts += 1
        if attempts > 10:
            raise SystemExit("Too many failed attempts.")
        try:
            client.login_password(
                username=username,
                password=password,
                challenge_type="sms",
                mfa_code=mfa_code,
                challenge_response_id=challenge_response_id,
            )
            return
        except RobinhoodMFARequired as e:
            print("MFA required.")
            if e.detail:
                print(f"Detail: {e.detail}")
            mfa_code = _prompt_secret("MFA code: ", masked=masked).strip()
            challenge_response_id = None
            continue
        except RobinhoodChallengeRequired as e:
            cid = (e.challenge_id or "").strip()
            ctype = (e.challenge_type or "sms").strip().lower()
            print("Challenge required.")
            if e.detail:
                print(f"Detail: {e.detail}")
            if not cid:
                raise SystemExit("Challenge required but no challenge id was provided by Robinhood.")

            prompt = f"{ctype.upper()} code (or press Enter if you already approved in-app): "
            code = _prompt_secret(prompt, masked=masked).strip()
            if code:
                try:
                    challenge_response_id = client.respond_to_challenge(cid, code)
                except RobinhoodAPIError as err:
                    print(f"Challenge response failed: {err.detail or str(err)}")
                    continue
            else:
                print("Waiting for in-app approval (best-effort)...")
                input("Approve in the Robinhood app, then press Enter to continue...")
                ok = client.poll_challenge_validated(cid, timeout_sec=120, interval_sec=3)
                if not ok:
                    raise SystemExit("Challenge not validated in time. Try again.")
                # Some flows accept the challenge id directly as the response id header.
                challenge_response_id = cid

            mfa_code = None
            continue
        except RobinhoodAPIError as e:
            print(f"Login failed: {e.detail or str(e)}")
            again = input("Try again? (y/n): ").strip().lower()
            if again == "y":
                username = input("Email: ").strip()
                password = _prompt_secret("Password: ", masked=masked)
                challenge_response_id = None
                mfa_code = None
                continue
            raise SystemExit(1)


def _normalize_access_token(raw: str) -> str:
    s = (raw or "").strip()
    if not s:
        return ""
    lower = s.lower()
    if lower.startswith("bearer "):
        s = s[7:].strip()
    return s


def _interactive_set_tokens(client: RobinhoodClient, *, allow_blank_keep: bool, masked: bool) -> None:
    """
    Prompt for access/refresh tokens (optionally device token), store on client.state.

    If allow_blank_keep=True, blank inputs keep existing values.
    """
    cur_access_set = bool(client.state.access_token)
    cur_refresh_set = bool(client.state.refresh_token)
    cur_device = client.state.device_token or ""

    print("\nPaste Robinhood tokens")
    print("Note: Do NOT paste the 'Bearer ' prefix (we'll strip it if you do).")
    if allow_blank_keep:
        print(f"Current access token set:  {'yes' if cur_access_set else 'no'} (blank keeps current)")
        print(f"Current refresh token set: {'yes' if cur_refresh_set else 'no'} (blank keeps current)")
    else:
        print("At least one of access/refresh token is required.")

    access_raw = _prompt_secret("Access token (Bearer): ", masked=masked).strip()
    refresh_raw = _prompt_secret("Refresh token: ", masked=masked).strip()

    access = _normalize_access_token(access_raw)
    refresh = (refresh_raw or "").strip()

    if allow_blank_keep:
        if access:
            client.state.access_token = access
        if refresh:
            client.state.refresh_token = refresh
    else:
        if not access and not refresh:
            raise RobinhoodAPIError("No tokens provided.")
        if access:
            client.state.access_token = access
        if refresh:
            client.state.refresh_token = refresh

    # Optional: device token override (rarely needed, but can matter for refresh).
    dev_in = input("Device token (optional; Enter to keep current): ").strip()
    if dev_in:
        client.state.device_token = dev_in
    else:
        client.state.device_token = cur_device or client.state.device_token

    exp_in = input("Expires in seconds (optional; Enter to keep current/default): ").strip()
    if exp_in:
        try:
            v = int(exp_in)
            if v > 0:
                client.state.expires_in = v
        except Exception:
            print("Invalid expires_in; keeping existing/default.")

    client.state.token_type = "Bearer"
    if client.state.obtained_at_epoch is None or (access or refresh):
        client.state.obtained_at_epoch = int(time.time())
    if client.state.expires_in is None:
        # Best guess; refresh logic also handles 401/403 on demand.
        client.state.expires_in = 86400


def _ensure_authenticated(client: RobinhoodClient) -> None:
    """
    Ensure client has a working access token.

    Strategy:
    - If access token exists, validate via /accounts (refresh on 401/403 if possible).
    - If no access token but refresh token exists, refresh then validate.
    - Otherwise raise.
    """
    if not client.state.access_token and client.state.refresh_token:
        client.refresh()

    if not client.state.access_token:
        raise RobinhoodAPIError("No access token available.")

    try:
        client.get_accounts()
        return
    except RobinhoodAPIError as e:
        if e.status_code in (401, 403) and client.state.refresh_token:
            client.refresh()
            client.get_accounts()
            return
        raise


def _interactive_auth_flow(client: RobinhoodClient, *, session_path: Path | None, no_save: bool, masked: bool) -> None:
    while True:
        print("\nAuthentication method")
        print("  1) Email + password (with MFA/challenge prompts)")
        print("  2) Paste access token + refresh token")
        choice = input("Choice (1/2): ").strip()
        if choice == "1":
            _interactive_login(client, masked=masked)
            return
        if choice == "2":
            try:
                _interactive_set_tokens(client, allow_blank_keep=False, masked=masked)
                _ensure_authenticated(client)
                if (not no_save) and session_path is not None:
                    _atomic_write_json(session_path, client.state.to_dict())
                return
            except RobinhoodAPIError as e:
                print(f"Token auth failed: {e.detail or str(e)}")
                continue
        print("Invalid choice.")


def _resolve_symbol_cache(client: RobinhoodClient, instrument_url: str, cache: dict[str, str | None]) -> str:
    if not instrument_url:
        return "?"
    if instrument_url in cache:
        return cache[instrument_url] or "?"
    inst = client.get_instrument(instrument_url)
    sym = (inst.get("symbol") or "").strip().upper() or None
    cache[instrument_url] = sym
    return sym or "?"


def _print_account_summary(client: RobinhoodClient) -> None:
    accounts = client.get_accounts()
    if not accounts:
        print("No accounts found.")
        return
    acct = None
    for a in accounts:
        if client.state.account_number and a.get("account_number") == client.state.account_number:
            acct = a
            break
    acct = acct or accounts[0]
    acct_num = acct.get("account_number") or "?"
    buying_power = acct.get("buying_power")
    cash = acct.get("cash")

    portfolio = client.get_portfolio(acct_num)
    equity = portfolio.get("equity") or portfolio.get("extended_hours_equity")

    print("\n" + "=" * 60)
    print("ROBINHOOD ACCOUNT SUMMARY")
    print("=" * 60)
    print(f"Account number: {acct_num}")
    print(f"Buying power:   {_fmt_money(buying_power)}")
    if cash is not None:
        print(f"Cash:           {_fmt_money(cash)}")
    if equity is not None:
        print(f"Equity:         {_fmt_money(equity)}")
    print("=" * 60)


def _print_positions(client: RobinhoodClient) -> None:
    acct_num = client.state.account_number
    positions = client.get_positions(account_number=acct_num, nonzero=True)
    if not positions:
        print("No positions.")
        return

    instrument_cache: dict[str, str | None] = {}
    symbols: list[str] = []
    parsed = []
    for p in positions:
        qty = _safe_float(p.get("quantity")) or 0.0
        if qty <= 0:
            continue
        inst_url = (p.get("instrument") or "").strip()
        sym = _resolve_symbol_cache(client, inst_url, instrument_cache)
        avg = _safe_float(p.get("average_buy_price")) or 0.0
        symbols.append(sym)
        parsed.append((sym, qty, avg))

    prices = get_live_prices(list({s for s in symbols if s and s != "?"})) if symbols else {}

    print("\nPOSITIONS (nonzero)")
    print("-" * 60)
    for sym, qty, avg in sorted(parsed, key=lambda x: x[0]):
        last = prices.get(sym)
        mv = (float(last) * float(qty)) if last is not None else None
        mv_str = _fmt_money(mv)
        last_str = _fmt_money(last)
        print(f"{sym:6}  qty={qty:10.4f}  avg={avg:10.2f}  last={last_str:>10}  mv={mv_str:>12}")


def _print_orders(client: RobinhoodClient, *, limit: int = 10) -> list[dict]:
    orders = client.list_orders(limit=limit)
    if not orders:
        print("No orders.")
        return []

    instrument_cache: dict[str, str | None] = {}
    print("\nRECENT ORDERS")
    print("-" * 60)
    for idx, o in enumerate(orders, start=1):
        created = (o.get("created_at") or "")[:19].replace("T", " ")
        side = (o.get("side") or "?").upper()
        qty = o.get("quantity") or "?"
        otype = (o.get("type") or "?").lower()
        state = (o.get("state") or "?").lower()
        oid = o.get("id") or "?"
        inst_url = (o.get("instrument") or "").strip()
        sym = _resolve_symbol_cache(client, inst_url, instrument_cache) if inst_url else "?"
        print(f"{idx:2}. {created:19}  {side:4}  {qty:>6}  {sym:6}  {otype:6}  state={state:18}  id={oid}")
    return orders


def _prompt_side() -> str:
    while True:
        raw = input("Buy or sell? (b/s): ").strip().lower()
        if raw in ("b", "buy"):
            return "buy"
        if raw in ("s", "sell"):
            return "sell"
        print("Enter b or s.")


def _prompt_order_type() -> str:
    while True:
        raw = input("Order type: market or limit? (m/l): ").strip().lower()
        if raw in ("m", "market"):
            return "market"
        if raw in ("l", "limit"):
            return "limit"
        print("Enter m or l.")


def _prompt_int(prompt: str) -> int:
    while True:
        raw = input(prompt).strip()
        try:
            v = int(raw)
        except ValueError:
            print("Enter a whole number.")
            continue
        if v <= 0:
            print("Value must be positive.")
            continue
        return v


def _prompt_float(prompt: str, *, default: float | None = None) -> float:
    while True:
        raw = input(prompt).strip()
        if not raw and default is not None:
            return float(default)
        try:
            v = float(raw)
        except ValueError:
            print("Enter a number.")
            continue
        if v <= 0:
            print("Value must be positive.")
            continue
        return v


def _place_order_flow(client: RobinhoodClient, *, dry_run: bool) -> None:
    acct_url = client.state.account_url
    acct_num = client.state.account_number
    if not acct_url:
        client.select_default_account(preferred_account_number=acct_num)
        acct_url = client.state.account_url
    if not acct_url:
        print("No account URL available; cannot place orders.")
        return

    symbol = input("Ticker symbol (e.g. AAPL): ").strip().upper()
    if not symbol:
        print("Ticker required.")
        return
    instrument_url = client.find_instrument_url_by_symbol(symbol)
    if not instrument_url:
        print(f"No instrument found for {symbol}.")
        return

    side = _prompt_side()
    order_type = _prompt_order_type()
    qty: float | None = None
    dollars: float | None = None
    limit_price: float | None = None
    time_in_force: str = "gtc"

    if order_type == "limit":
        qty = float(_prompt_int("Number of shares (whole): "))
        limit_price = _prompt_float("Limit price per share: ")
    else:
        if side == "buy":
            while True:
                mode = input("Market buy mode: dollars or shares? (d/s): ").strip().lower()
                if mode in ("d", "dollars"):
                    dollars = _prompt_float("Buy $ amount (min $1.00): ")
                    if dollars < 1.0:
                        print("Dollar amount must be at least $1.00 for fractional buys.")
                        continue
                    break
                if mode in ("s", "shares"):
                    qty = _prompt_float("Number of shares (can be fractional, e.g. 0.5): ")
                    break
                print("Enter d or s.")
        else:
            qty = float(_prompt_int("Number of shares to sell (whole): "))

    # Convert dollars -> fractional shares using current price (best-effort).
    if dollars is not None:
        ask = None
        try:
            quote = client.get_latest_quote(symbol)
            ask_raw = quote.get("ask_price") if isinstance(quote, dict) else None
            ask = float(ask_raw) if ask_raw is not None else None
        except Exception:
            ask = None
        if ask is None or ask <= 0:
            # Fallback to last price.
            last = None
            try:
                prices_map = get_live_prices([symbol])
                last = prices_map.get(symbol)
            except Exception:
                last = None
            ask = float(last) if last is not None else None
        if ask is None or ask <= 0:
            print("Could not fetch live ask/price for dollars-based buy; try shares mode.")
            return
        qty = round(float(dollars) / float(ask), 6)
        print(f"Estimated fractional shares: {qty} @ ~${float(ask):.2f} (actual fill may differ)")

    if qty is None:
        print("Quantity required.")
        return

    # Fractional market orders should use gfd.
    if order_type == "market":
        try:
            is_fractional = abs(float(qty) - round(float(qty))) > 1e-9
        except Exception:
            is_fractional = False
        if is_fractional:
            time_in_force = "gfd"

    print("\nORDER REVIEW")
    print("-" * 60)
    print(f"Symbol:     {symbol}")
    print(f"Side:       {side}")
    print(f"Type:       {order_type}")
    print(f"Quantity:   {qty}")
    if dollars is not None:
        print(f"Dollars:    ${dollars:.2f}")
    if limit_price is not None:
        print(f"Limit px:   {limit_price:.2f}")
    print(f"TIF:        {time_in_force}")
    print("-" * 60)
    confirm = input('Type "YES" to submit: ').strip()
    if confirm != "YES":
        print("Cancelled.")
        return

    if dry_run:
        payload = client.build_order_payload_equity(
            account_url=acct_url,
            instrument_url=instrument_url,
            symbol=symbol,
            side=side,
            order_type=order_type,
            quantity=float(qty),
            limit_price=limit_price,
            time_in_force=time_in_force,
        )
        print("DRY RUN: would POST /orders/ with payload:")
        print(json.dumps(payload, indent=2, sort_keys=True))
        return

    try:
        res = client.place_order_equity(
            account_url=acct_url,
            instrument_url=instrument_url,
            symbol=symbol,
            side=side,
            order_type=order_type,
            quantity=float(qty),
            limit_price=limit_price,
            time_in_force=time_in_force,
        )
        oid = res.get("id") or "?"
        state = res.get("state") or "?"
        print(f"Order submitted. id={oid} state={state}")
    except RobinhoodAPIError as e:
        print(f"Order failed: {e.detail or str(e)}")


def _cancel_order_flow(client: RobinhoodClient, *, dry_run: bool) -> None:
    orders = client.list_orders(limit=20)
    if not orders:
        print("No orders found.")
        return

    # Prefer showing "open-ish" orders first.
    open_states = {"queued", "confirmed", "unconfirmed", "partially_filled", "held"}
    ranked = sorted(
        orders,
        key=lambda o: (0 if (o.get("state") or "").lower() in open_states else 1, (o.get("created_at") or "")),
    )
    shown = ranked[:10]

    print("\nSELECT ORDER TO CANCEL")
    print("-" * 60)
    instrument_cache: dict[str, str | None] = {}
    for idx, o in enumerate(shown, start=1):
        created = (o.get("created_at") or "")[:19].replace("T", " ")
        side = (o.get("side") or "?").upper()
        qty = o.get("quantity") or "?"
        state = (o.get("state") or "?").lower()
        oid = o.get("id") or "?"
        inst_url = (o.get("instrument") or "").strip()
        sym = _resolve_symbol_cache(client, inst_url, instrument_cache) if inst_url else "?"
        print(f"{idx:2}. {created:19}  {side:4}  {qty:>6}  {sym:6}  state={state:18}  id={oid}")

    raw = input("Pick number (1-10) or paste order id: ").strip()
    order = None
    if raw.isdigit():
        i = int(raw)
        if 1 <= i <= len(shown):
            order = shown[i - 1]
    else:
        for o in orders:
            if (o.get("id") or "") == raw:
                order = o
                break

    if not order:
        print("Invalid selection.")
        return

    oid = order.get("id") or "?"
    cancel_url = (order.get("cancel") or "").strip() or None
    state = (order.get("state") or "?").lower()
    print(f"Selected order id={oid} state={state}")
    confirm = input('Type "CANCEL" to confirm: ').strip()
    if confirm != "CANCEL":
        print("Cancelled.")
        return

    if dry_run:
        url = cancel_url or f"/orders/{oid}/cancel/"
        print(f"DRY RUN: would POST {url}")
        return

    try:
        client.cancel_order(cancel_url=cancel_url, order_id=str(oid))
        print("Cancel submitted.")
    except RobinhoodAPIError as e:
        print(f"Cancel failed: {e.detail or str(e)}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Interactive Robinhood CLI (testing-only).")
    parser.add_argument("--session-file", type=str, default=str(DEFAULT_SESSION_FILE), help="Path to session JSON file.")
    parser.add_argument("--no-save", action="store_true", help="Do not write tokens/session to disk.")
    parser.add_argument("--account", type=str, default=None, help="Preferred account number.")
    parser.add_argument("--dry-run", action="store_true", help="Do not place/cancel orders; print actions only.")
    parser.add_argument("--debug-http", action="store_true", help="Print HTTP status codes and endpoint paths.")
    parser.add_argument("--paste-tokens", action="store_true", help="Prompt to paste access/refresh tokens at startup.")
    parser.add_argument("--no-mask", action="store_true", help="Do not hide password/token input (insecure).")
    args = parser.parse_args(argv)

    session_path = Path(os.path.expanduser(args.session_file)).resolve()
    state = _load_session(session_path)

    use_saved = False
    if session_path.exists() and (state.access_token or state.refresh_token) and not args.paste_tokens:
        ans = input(f"Found session at {session_path}. Use it? (Y/n): ").strip().lower()
        use_saved = ans in ("", "y", "yes")

    client = RobinhoodClient(state=state, debug_http=bool(args.debug_http))
    masked = not bool(args.no_mask)

    if args.paste_tokens:
        _interactive_set_tokens(client, allow_blank_keep=True, masked=masked)
        try:
            _ensure_authenticated(client)
        except RobinhoodAPIError as e:
            print(f"Token auth failed: {e.detail or str(e)}")
            _interactive_auth_flow(client, session_path=session_path, no_save=bool(args.no_save), masked=masked)

    if use_saved and _should_refresh(client.state):
        try:
            client.refresh()
        except Exception:
            pass

    if use_saved and client.state.access_token:
        try:
            client.get_accounts()
        except RobinhoodAPIError as e:
            if e.status_code in (401, 403) and client.state.refresh_token:
                try:
                    client.refresh()
                except Exception:
                    client.state.access_token = None
            else:
                client.state.access_token = None

    if not client.state.access_token and client.state.refresh_token:
        try:
            client.refresh()
        except Exception:
            pass

    if not client.state.access_token:
        _interactive_auth_flow(client, session_path=session_path, no_save=bool(args.no_save), masked=masked)
    else:
        # Validate token; refresh on auth failure if possible.
        try:
            _ensure_authenticated(client)
        except RobinhoodAPIError as e:
            print(f"Saved token invalid: {e.detail or str(e)}")
            _interactive_auth_flow(client, session_path=session_path, no_save=bool(args.no_save), masked=masked)

    try:
        client.select_default_account(preferred_account_number=args.account)
    except Exception:
        pass

    if not args.no_save:
        _atomic_write_json(session_path, client.state.to_dict())

    while True:
        print("\nMENU")
        print("  1) Account summary")
        print("  2) List positions")
        print("  3) List recent orders")
        print("  4) Place order (equities)")
        print("  5) Cancel order")
        print("  6) Refresh token")
        print("  7) Logout (and optionally delete session)")
        print("  8) Set/Update tokens")
        print("  0) Exit")
        choice = input("Choice: ").strip()

        if choice == "1":
            try:
                _print_account_summary(client)
            except RobinhoodAPIError as e:
                print(f"Error: {e.detail or str(e)}")
        elif choice == "2":
            try:
                _print_positions(client)
            except RobinhoodAPIError as e:
                print(f"Error: {e.detail or str(e)}")
        elif choice == "3":
            try:
                _print_orders(client, limit=10)
            except RobinhoodAPIError as e:
                print(f"Error: {e.detail or str(e)}")
        elif choice == "4":
            _place_order_flow(client, dry_run=bool(args.dry_run))
        elif choice == "5":
            _cancel_order_flow(client, dry_run=bool(args.dry_run))
        elif choice == "6":
            try:
                client.refresh()
                print("Token refreshed.")
                if not args.no_save:
                    _atomic_write_json(session_path, client.state.to_dict())
            except Exception as e:
                print(f"Refresh failed: {e}")
        elif choice == "7":
            try:
                client.logout()
            except Exception:
                pass
            if not args.no_save and session_path.exists():
                del_ans = input(f"Delete session file {session_path}? (y/N): ").strip().lower()
                if del_ans in ("y", "yes"):
                    try:
                        session_path.unlink()
                    except Exception:
                        pass
            print("Logged out.")
        elif choice == "8":
            try:
                _interactive_set_tokens(client, allow_blank_keep=True, masked=masked)
                _ensure_authenticated(client)
                print("Tokens updated.")
                if not args.no_save:
                    _atomic_write_json(session_path, client.state.to_dict())
            except RobinhoodAPIError as e:
                print(f"Failed to set tokens: {e.detail or str(e)}")
        elif choice == "0":
            break
        else:
            print("Invalid choice.")

    if not args.no_save:
        _atomic_write_json(session_path, client.state.to_dict())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
