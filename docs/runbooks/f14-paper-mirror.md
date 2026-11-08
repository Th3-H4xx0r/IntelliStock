# F14_F paper mirror

The `f14-mirror` compose service (`backend/forward_mirror.py`) trades the frozen
forward-test rule F14_F_live (`research/forward-test-2026-11/`) on one Alpaca
**paper** account: "Alpaca Crypto Paper" (brokerage `b0d7dc67…`, account
`PA3TRLBV6ZNZ`, $10,000 at start).

The scored forward test is `score_forward.py`, which needs no broker. The mirror
measures what the scorecard cannot: real fills, DBMF liquidity, margin, and the
trading days the data feed misses.

## What it does

- **Boot.** It refuses to trade until three checks pass:
  - every file in the read-only `/frozen` mount matches the SHA-256 block in
    `FROZEN.md`;
  - `repro_check.py` prints `OVERALL: PASS` inside the image;
  - the account passes the account checks under Safety.

  Each attempt writes a `boot` row. A refused boot writes `ok: false` and
  retries hourly.
- **Every NYSE session, 20 to 3 minutes before the close** (Alpaca's clock, so
  half-days are handled). The 3-minute mark is a hard deadline: it bounds the
  data fetch, and the clock is re-read before the sells and again before the
  buys, so no order goes out after it.
  1. It fetches yfinance/FRED through the frozen `fetch_data.py`, which drops
     today's unsettled row.
  2. It runs `strategies.run_f14_live`.
  3. It acts on the result:
     - **TRADE:** rebalance to the decision's target weights.
     - **HOLD:** leave the drifted book alone, with two exceptions. On the
       first session the empty account is synced to the model's book. If any
       weight is more than 5 pts off the model's, it repairs back to it. A
       drifted book above 2.0x gross is scaled down to 2.0x for a sync or
       repair. A TRADE target above the cap is refused.
     - A leg that is rejected or misses its fill is left to that 5-pt drift
       band. Once any rebalance has gone through, the account counts as synced
       for good, so one leg that keeps failing can't trigger a full-book
       rebalance every day. A first sync that sent nothing is retried the next
       session.

  A stale frame (yesterday's close missing) is retried every 5 minutes until
  the window shuts; then that session is skipped. Until the session acts, its
  `act` row stays `status: pending` and holds the last error and the attempt
  count, so a mirror that keeps failing shows up in the ledger, not only in
  `docker logs`.
- **After 18:30 ET** it records each order's final status, `filled_qty` and
  `filled_avg_price` on the session's `act` row. Then it writes one `close`
  row: the account against the model, the deviation, `all_filled`, and
  tomorrow's action.

Orders are market, TIF day: notional for fractionable ETFs, otherwise whole
shares. Sells go first and buys follow after they fill. A session is marked
done before its first order goes out, so a crash never re-sends an order; the
next session's drift check repairs it instead. Gross is 1.4–1.9x, financed on
the paper account's margin.

## Safety

The mirror refuses (`MirrorRefused`, a `refused` or boot row) if any of these
holds:

- the brokerage row is not `alpaca_paper: true`;
- the account number isn't the pinned `PA3TRLBV6ZNZ`
  (`F14_MIRROR_ACCOUNT_NUMBER`);
- the account has an open order whose client id doesn't start with `f14m-`;
- the account holds anything outside SPY/IEF/MTUM/IWD/DBMF;
- an IntelliStock instance with `runCommand: true` is bound to this brokerage.
  The "Test" crypto instance is; keep it stopped.

It never reads or trades any other account. The frozen subprocess gets only
PATH/HOME/TZ/locale/proxy variables: no DB, broker or LLM secrets.

## Comparing NAVs

Alpaca paper accounts do not pay dividends. The model's NAV is Adj-Close total
return, so the account will trail it by the SPY/IWD/MTUM/IEF yields plus
DBMF's annual December distribution (its size varies a lot year to year).
Compare weights and fills, or add dividends back, before calling the gap
slippage. The drift check is weight-based and barely affected.

## Reading it

Read-only Postgres (or `store`): table `ForwardMirrorLedger`, filtered on
`mirror = 'f14-paper'`.

| id | what |
|---|---|
| `f14-paper\|state` | `synced`, `last_act_session`, `last_close_session` |
| `f14-paper\|YYYY-MM-DD\|act` | `action`, `reason` (`trade`/`initial_sync`/`drift_repair`/`hold`), `orders` with statuses, `model_target`, `account_weights` |
| `f14-paper\|YYYY-MM-DD\|close` | `equity`, `account_weights`, `model_current`, `deviation`, `next_action` |
| `f14-paper\|YYYY-MM-DD\|error` | a failure after the session acted |
| `f14-paper\|YYYY-MM-DD\|dry` | a dry run's plan (never overwrites `act`) |
| `f14-paper\|boot\|…`, `f14-paper\|refused\|…` | boot results (incl. `multiplier` and per-ETF `tradable`/`fractionable`/`marginable`) and refusals |

Positions are also visible via `GET /brokerages/b0d7dc67-f12b-4959-83db-0d324c6bc517/positions`.

## Switches

Set these in the deployment's environment, then redeploy:

- `F14_MIRROR_ENABLED=0`: idle, no orders.
- `F14_MIRROR_DRY_RUN=1`: log the plan in `act` rows, send nothing. A dry run
  spends its own `last_dry_session` key, so switching it off trades the same
  day.
- `F14_MIRROR_BROKERAGE_ID`: a different paper brokerage row. It must still
  pass every safety check.

Never edit `research/forward-test-2026-11/`: a changed file fails the boot hash
check, and the mirror stops trading.
