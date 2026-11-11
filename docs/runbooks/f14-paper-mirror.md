# F14_F paper mirror

The `f14-mirror` compose service (`backend/forward_mirror.py`) trades the frozen
forward-test rule F14_F_live (`research/forward-test-2026-11/`) on one Alpaca
**paper** account: "TMasters Swing", `PA3TRLBV6ZNZ`, stored as the "Alpaca Crypto
Paper" brokerage (`b0d7dc67…`). PA37JPXGU196 ("Swing Trade Paper") is OFF LIMITS by
operator ruling (2026-11-09). The mirror refuses with `unauthorized` until the
`b0d7dc67…` row's API keys are regenerated in Alpaca and saved in the web UI.

The scored forward test is `score_forward.py`, which needs no broker. The mirror
measures what the scorecard cannot: real fills, DBMF liquidity, margin, and the
trading days the data feed misses.

## Two halves since 2026-11-11

The mirror is now an IntelliStock strategy with its own instance.

| half | where | job |
|---|---|---|
| **signal** | `f14-mirror` compose service, `F14_MIRROR_ROLE=signal` (the default) | Verifies the frozen dir, runs the frozen rule once per session after the open, publishes `f14-paper\|YYYY-MM-DD\|signal`, writes the `close` rows. Never orders. |
| **executor** | instance `f14-paper`, strategy lane `strategy_f14` (`backend/strategies/strategy_f14.py`) | On the scheduler tick 20 minutes before the close it reads the signal row and rebalances. Start/Stop on the Instances tab is the trading switch. |

Why two: the frozen rule lives in `research/`, outside the backend image, so an
instance container cannot run it; and the engine's own order gates refuse every
opening order on negative cash, so a 1.4-2.0x book cannot go through them. The
lane therefore always returns no decisions to the broker and sends its orders
through `forward_mirror.Mirror` (role `executor`): the same order code, safety
checks, `f14m-` client ids and ledger as before. The instance's own order
counters stay at zero; the ledger and the brokerage positions are the record.

Both halves share the `f14-paper|state` row, so "at most once per session"
holds across a role change.

- Create or repair the doc and instance: `python3 scripts/f14_instance_setup.py [--start]`.
- The lane has no backtest. A backtest tick passes no scheduler mode and the
  lane returns nothing. The scored test is `score_forward.py`.
- The executor acts on ONE tick a day (12:40 PT; 09:40 PT on a half day). If
  the instance is down then, or the signal row is missing, that session is
  skipped and the next session's 5-pt drift check repairs it.
- The signal service tolerates exactly one running instance on the brokerage:
  `f14-paper` (`F14_MIRROR_INSTANCE_ID`). Any other still refuses.
- **Rollback to the single service:** stop the `f14-paper` instance, set
  `F14_MIRROR_ROLE=full`, redeploy. `full` refuses while any instance runs on
  the brokerage, so the two can never trade together.

The sections below describe the trading logic, which is unchanged. Read
"the mirror" as "the executor" for orders and "the signal service" for the
frozen checks and close rows. The executor's window opens 25 minutes before the
close (`act_minutes`), not 20, so the 12:40 tick is inside it.

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

### What the engine can still do on the instance

The lane returns no decisions, so the engine never buys, sells or trims. It can
still **cancel** open orders it did not place, `f14m-` ones included:

- the risk ladder's kill rung (default 12% below the equity high-water mark,
  about an 8% market fall at this gross) cancels open BUY orders once, on the
  transition. A cancelled leg is repaired by the next session's drift check;
- a `halt` command from the UI or chatbot cancels every open order;
- the alpha watchdog sidecar, if `alpha_watchdog_enabled` is ever set on the
  instance. Leave it off.

Keep the strategy document to this one lane, with no sleeve or LLM keys.

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
| `f14-paper\|YYYY-MM-DD\|signal` | the frozen rule's answer for that session: `frame_last_close`, `action`, `next` (what the executor trades from) |
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
- `F14_MIRROR_ROLE`: `signal` (default) or `full`; see "Two halves".
- Lane config (strategy doc, not environment): `strategy_f14_enabled`,
  `dry_run`, `act_minutes`, and the pinned `brokerage_id` / `account_number` /
  `instance_id`.
- `F14_MIRROR_BROKERAGE_ID`: a different paper brokerage row. It must still
  pass every safety check.

Never edit `research/forward-test-2026-11/`: a changed file fails the boot hash
check, and the mirror stops trading.
