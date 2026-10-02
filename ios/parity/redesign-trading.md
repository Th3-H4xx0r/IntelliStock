# Redesign parity — trading (Wave R2)

Screens: Dashboard (hero, holdings, insights, market, strategy, Kalshi card,
services, re-run onboarding), Stock, Symbol search, Instances list and detail,
Swing (pending signals, wheel), Live Trading. Spec:
`docs/superpowers/specs/2026-10-02-ios-ui-redesign.md`.

Every action, endpoint, body, guard, confirmation, poll and string is
unchanged except where this file says so. The one new feature is the
Dashboard's Portfolios sheet, which reads `GET /widget/accounts` and
`GET /instances` (both read-only).

## Moved buttons

Every control that existed before is listed with where it lives now. Nothing
became unreachable.

### Dashboard

| Old control | New location |
|---|---|
| Account menu (`ALPACA ⌄` eyebrow, a `Menu` of accounts) | Account label above the balance (logo, name, `chevron.down`) → **Portfolios** bottom sheet; a row tap selects the account (same `selectedAccount.select`) |
| Search (toolbar magnifier) | Unchanged: the only toolbar item |
| Range picker (above the chart) | Below the chart (spec hero order) |
| Holdings Total / Daily picker | Holdings section header, trailing |
| Holding row tap → Stock | Row is a `NavigationLink` (same route and `StockRoute` arguments) |
| Today's movers chips → Stock | Mover tiles in the Insights row (same route) |
| Index cards → Stock | Index cards in the Market row (same route) |
| Market movers rows → Stock | Market movers section rows; the Gainers / Losers columns became a header switch |
| Nexus momentum rows → Stock | Section rows (`NavigationLink`) |
| Market news rows → in-app browser | Section rows (same `SFSafariViewController` sheet) |
| Strategy cards' ticker rows → Stock | Section rows (`NavigationLink`); trend tickers and watchlist tags stay inline buttons |
| Kalshi card tap and its **Open** button → Kalshi tab | One row in the Kalshi section (same `router.go("/kalshi")`) |
| Services **Refresh** button | Removed: pull to refresh calls the same `refreshNow()` (and the 10 s poll is unchanged) |
| Price Engine **Start / Terminate** | Inline action row in the Price Engine section; also its status row's context menu |
| Discover Engine **Start / Stop** | Inline action row; context menu |
| AI Backtest Agent **Start** (opens the sheet) / **Stop** | Inline action row; context menu. The sheet is unchanged (now hosted by the list) |
| AI Backtest Agent **Pause / Resume** | Status row's context menu |
| Daily Digest **Start / Stop** | Inline action row; context menu |
| Daily Digest **Send Now** | Status row's context menu |
| Nexus Graph Engine **Start / Stop** | Inline action row; context menu |
| Re-run onboarding **Open** | The row itself (`NavigationLink` to `.onboarding`) |
| Empty state **Link a Brokerage** | Unchanged |
| Error rows **Retry** | Unchanged |

### Stock and Search

| Old control | New location |
|---|---|
| Range picker | Under the chart (unchanged order) |
| Chart scrub | Unchanged |
| Search result tap → Stock | Row is a `NavigationLink` (same route) |
| **Retry Search** | The error `ContentUnavailableView`'s action |

### Instances list

| Old control (card button) | New location |
|---|---|
| **View** | Row tap (`NavigationLink` to `.instance`) |
| **Pin / Pinned** | Leading swipe; context menu |
| **Live** | Context menu → View Live |
| **Start / Stop** | Context menu (disabled while busy; a spinner replaces the status dot) |
| **Delete** (with its confirmation) | Trailing swipe; context menu (same `ConfirmRequest`, disabled while busy or while a delete runs) |
| Stocks **Add** | Context menu → Add Stock… (same sheet); also Instance detail → Stocks |
| Stock chip **×** (remove) | Instance detail → Stocks row swipe or context menu → Remove |
| Toolbar **Refresh** | Removed: pull to refresh (`refreshNow()`), and the poll is unchanged |
| Toolbar **New Instance** | `+` toolbar button (same sheet) |
| **Create Your First Instance** | Unchanged (empty state) |

### Instance detail

| Old control | New location |
|---|---|
| Header **Start / Stop** | Toolbar prominent button (same `toggleRun()`, spinner while toggling) |
| Header **Live Trading** | Toolbar More menu |
| Instance Info **Clear State** | More menu (same sheet) |
| Brokerage **Change / Link** | More menu; when unlinked also an inline "Link Brokerage" row |
| **Unlink Brokerage** (with confirmation) | More menu (destructive, same confirmation) |
| Strategy **Link** | More menu; when unlinked also an inline "Link Strategy" row |
| Strategy **Unlink** (with confirmation) | More menu (destructive, same confirmation) |
| Stocks **Add** | Stocks section header action; also the More menu |
| Stock chip **×** | Stock row swipe or context menu → Remove |
| Backtests **New Backtest** | Backtests header `+`; also the More menu |
| Backtests sort **Date / PnL** chips | Backtests header sort `Menu` (choosing the active field flips the order, as before) |
| Backtest card tap | Row `NavigationLink` to `.backtest` |
| Paging ← / → | Paging row (same buttons) |
| Toolbar **Refresh** | Removed: pull to refresh |
| Live logs panel and its controls | Live logs section (the panel itself is unchanged) |
| — | New: More menu → Copy ID (and the ID row's context menu) |

### Pending AI signals and wheel

| Old control | New location |
|---|---|
| **Approve / Approve ½ / Reject** | Large bordered buttons side by side at the foot of each signal's section; Approve green, Reject red; same confirmations and busy guards |
| **Show more / Show less** | The "Rationale" disclosure |
| Stuck card **Re-send** (with confirmation) / **Dismiss** | Text buttons in the stuck row |
| Uncertain card **Dismiss** | Text button in the row |
| Error **Retry** (signals, wheel) | Unchanged |

### Live Trading

| Old control | New location |
|---|---|
| Header **Halt** button | Removed as a duplicate: the floating glass **Halt** (already there) is the one Halt; same sheet and typed `HALT` |
| Header **Manual Order** | Toolbar button (cart symbol; same sheet and validation), shown only while a session runs, as before |
| Position card **Close** (typed `CLOSE SYM` confirmation) | Position row trailing swipe and context menu, plus a VoiceOver action; same typed confirmation, disabled while a close runs |
| Range picker / chart-style picker | Under the chart, in the hero (segmented, the style picker with SF Symbols) |
| Command toast **Dismiss** | Unchanged (floating) |
| Error **Retry** | Unchanged |

## Rulings

Format: what — why — cost if wrong.

1. **Dashboard is one `List`, and every section task runs on the list.**
   Lazy list rows would start their loads and polls only on scroll and stop
   them when scrolled away; the hero chart, holdings, sparks, insights, day
   change and Kalshi fetches now run from `.task(id:)`s on the list keyed
   exactly as before (account, range, P&L mode). — Keeps "every poll stays"
   with an Apple container. — A key that differs from the old view identity
   would refetch more or less often; the keys copy the old `.task(id:)`s.
2. **Portfolios sheet data: `/widget/accounts` keyed through `/instances`.**
   The endpoint reports one entry per *instance*, each with its brokerage's
   1D value and day P&L (`_widget_account`), and carries no brokerage id, so
   the sheet maps instance → brokerage with `GET /instances` (the server's
   `_widget_brokerage_id` order) and takes the first instance per brokerage.
   Accounts no instance links (Kalshi, an idle crypto account) show "—". —
   The spec names this endpoint; it is read-only and additive. — Figures for
   a brokerage whose instances all lack history are missing.
3. **The widget endpoint is slow (18 s measured on 2026-10-02).** The
   dashboard starts one fetch on entry when there is more than one account,
   the sheet refreshes on each appearance, a fetch runs in its own task (a
   closed sheet does not cancel it) and a second request while one runs is
   dropped. Until the first fetch settles each value is redacted. — An 18 s
   placeholder on first open would look broken. — One extra read per
   dashboard visit with several accounts.
4. **Account label and sheet rows show the account's name** ("Swing Trade
   Paper"), falling back to the old label; Live / Paper is the subtitle
   (`alpaca_paper`, or Kalshi's `kalshi_environment == "demo"`, newly read
   into `BrokerageAccount`). — Three Alpaca paper accounts all read "Alpaca ·
   Paper" in the old label; a list of them needs names. — None.
5. **No title, no large title:** `navigationTitle("Dashboard")` with an
   inline display mode and an empty principal item. The back button and
   VoiceOver still say "Dashboard".
6. **Group headings** (Holdings, Insights, Market, Strategy, Kalshi,
   Services) are `.title3` bold section headers in `Color.primary`; sections
   inside a group use the system header. Strategy and Services have several
   sections, so the group heading sits over the first one shown. — Mirrors
   Health/Fitness summaries; keeps the Dart group names. — If the
   orchestrator wants system headers only, it is one view.
7. **Insights row:** Today, Diversification and Risk are 300 pt cards with
   `StatGrid`s that snap (`.viewAligned`). Diversification's "72 /100 · Top
   35% · 4 holdings" line becomes cells Score `72/100`, Top holding `35%`,
   Holdings `4`; Today becomes Day P&L and Change cells. — Spec: StatGrids. —
   New sentence-case cell labels.
8. **Sector allocation** is `Sector3DChart` in a `Card("Sector
   allocation")`, outside the scrolling row. The chart's swipe became a
   UIKit pan that begins only for sideways drags (commit 3c02bb8c): in the
   List the old simultaneous `DragGesture` swallowed vertical scrolls that
   started on the ring. — A dashboard you cannot scroll past the ring is a
   bug. — `Sector3DChart` is shared with Crypto; the interaction maths and
   tests are untouched.
9. **Holdings rows:** quantity reads "22.4 sh" (`DashboardFormat.qtyShort`,
   spec); ticker and value are primary, only the change is green or red;
   the 24 pt ring is accent (cash keeps teal). — Spec: green and red mean
   P&L only. — The Dart coloured the whole row.
10. **Services:** one section per engine — a status row (subtitle and a
    status dot; its context menu holds every control), the stats as
    `LabeledContent`, and the card's primary action as a text-only inline
    row (red when it stops something). Secondary controls (Pause, Resume,
    Send Now) live only in the context menu, per the spec. The section
    description moved to the last section's footer. — Spec. — Send Now and
    Pause are one long-press deep.
11. **Kalshi card:** one row in a "Kalshi" section; the account name is no
    longer upper-cased; the soccer glyph in the header is dropped (no
    header icons).
12. **Market movers:** the two columns became a Gainers / Losers switch in
    the header with up to five rows; an empty side shows "No gainers" / "No
    losers" (new copy for a state the columns showed as blank).
13. **1D chart labels:** four (12AM, 8AM, 4PM, 12AM) instead of five, on the
    dashboard and Live Trading; tests updated. — Spec: at most four.
14. **Dotted baseline:** the dashboard and live charts draw Stocks' dotted
    line at the opening value; the dashboard's y range includes it.
15. **Stock:** the hero's change reads "+$0.88 (+0.11%)" (the hero format)
    instead of "▲ +$0.88  +0.11%"; side tags read "Buy" / "Sell" and the
    override tag "Overridden" in the one badge style; "Backed by" is
    secondary text (accent is for controls). Pull to refresh calls the
    existing `refreshHistory()` (the poll's fetch).
16. **Search:** the "MARKET SEARCH" eyebrow is dropped from the error state
    (now a `ContentUnavailableView` with Retry Search).
17. **Instances rows:** subtitle "AI · Strategy 197 · Alpaca Paper". AI is
    named, User is the default and omitted; the brokerage name comes from
    the nested map or the already-loaded `/brokerages` list (no new
    request), else the id shortened in the middle. The instance id moved to
    Copy ID. — Spec P8. — A user instance no longer says "User".
18. **Instance detail has no Delete.** The spec's menu lists Delete, but the
    Dart detail screen had none; adding one would be new behaviour (and a
    pop after delete). Delete stays on the list's swipe and context menu.
19. **Instance detail Start / Stop** is the accent prominent button (the
    Dart tinted Stop orange and Start green; a white label on system green
    fails contrast). The run state is the Status section's dot.
20. **Pending signals:** each signal is a section: the symbol over "Swing ·
    session …", a 3-column `StatGrid` led by Score (instead of a score
    badge), the rationale in a "Rationale" disclosure, Risks, then the
    buttons. Field labels are sentence-cased at display ("ENTRY" → "Entry";
    ITM, DTE, P&L stay). The poll and the confirmation alert moved from the
    section to the screen (`PendingSignalsActions`), because modifiers on a
    multi-row section would attach to every row.
21. **Wheel:** labels sentence-cased; "Recent scans" is its own section.
22. **Live Trading:** the "Live Trading Terminal / Real-time positions &
    executions" header is dropped (the title and subtitle already name the
    screen); status reads "Active" / "Not running" (not upper case);
    warnings are footnote labels. Range figures and account figures are two
    `StatGrid`s; Uptime moved into Account. Executions read "SELL EL" over
    the date, "131 sh @ $89.80" over the total (spec). A position with two
    badges (Option, Short) shows one ("Short option"). The command toast
    reads "Close position · Pending" instead of "CLOSE_POSITION · PENDING".
    A footer explains swipe or touch-and-hold to close.
23. **Live logs** stay inline on Instance detail and Live Trading (a section
    whose row is the panel): the panel already detaches its tailer when off
    screen and resumes when back.

## Tests

- Updated: the 1D label expectations (dashboard and live); the deleted
  `DashboardSectorDonutTests` (the donut is gone; `Sector3DGeometryTests`
  and `Sector3DInteractionTests` cover the replacement).
- New: `DashboardPortfoliosTests`, `KalshiDashboardCardModelTests`,
  `InstanceRowFormattingTests`, `LiveRedesignFormattingTests`.
