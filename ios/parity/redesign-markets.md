# Redesign parity: markets (Wave R2)

Restyle of the markets screens to the 2026-10-02 redesign spec
(`docs/superpowers/specs/2026-10-02-ios-ui-redesign.md`): Kalshi (all screens), Crypto (all,
including the instance sheet), Backtests (list, detail, playback), Strategies (list, detail),
Nexus (with its logs panel) and Learning.

Behaviour is unchanged: every action, endpoint, body, guard, confirmation, poll and string stays.
What changed is placement (rows, swipe actions, context menus, toolbar menus), title-case and
sentence-case capitalisation, and the removal of repeated titles and eyebrow blocks.

## Moved buttons

Every old button, and where it is now. "Row" means tapping the list row; "swipe" a swipe action;
"context" the row's long-press menu; "toolbar" a toolbar item; "menu" the toolbar's More menu
(`ToolbarMenu`); "section" a row or header action inside a list section.

### Kalshi tab (`KalshiView`)

| Old button | New location |
|---|---|
| Account picker (white field) | toolbar: account `Menu` (shown only with more than one Kalshi account, as before) |
| Instance row (chevron) | row: `NavigationLink` to the instance |
| "New Instance" text button | toolbar: `+` ("New Instance") |
| "Create Instance" (empty state) | unchanged: the empty state's prominent action |
| Portfolio "Retry" | section: `ErrorRow` Retry in the Portfolio section |
| Edge radar / positions "Retry" | section: `ErrorRow` Retry in each section |
| Live logs "View Live Logs" | section: the one row of "Live logs" (the shared `LiveLogsPanel` header) |

### Kalshi instance (`KalshiInstanceDetailView`)

| Old button | New location |
|---|---|
| Start / Stop (toolbar, tinted label) | toolbar: prominent "Start" / "Stop" |
| More › Backtest | menu: Backtest |
| More › Edit Config | menu: Edit Config |
| More › Delete (confirmation) | menu: Delete, last section, destructive, same confirmation |
| Decision card (tap to expand) | row: tap to expand, chevron rotates |
| Decision log Previous / Next page | section: borderless chevrons in the log's last row |
| Pregame / decision / portfolio "Retry" | section: `ErrorRow` Retry |
| Live logs "View Live Logs" | section: the one row of "Live logs" |

### Kalshi backtest launcher (`KalshiBacktestView`)

| Old button | New location |
|---|---|
| Run Backtest (full width) | unchanged: full-width prominent button, the form's purpose |
| Start / End date rows | unchanged: rows that open the date sheet |
| Backtest row "View results" (eye icon) | row: `NavigationLink` to the result; also context: View Results |
| Backtest row "Stop backtest" (while active) | swipe: Stop Backtest; context: Stop Backtest |
| Backtest row "Delete backtest" | swipe: Delete Backtest (no full swipe, as there is no confirmation, as before); context: Delete Backtest |

### Kalshi backtest result (`KalshiBacktestResultView`)

| Old button | New location |
|---|---|
| Day chips (All · n, each day) | section: a "Day" `Picker` menu row under the equity chart |
| Trades / Decision log / Logs segmented picker | section header of the tab section |

### Kalshi instance sheet (`KalshiInstanceSheet`)

| Old button | New location |
|---|---|
| Create Instance / Save Changes (bottom bar) | toolbar: confirmation action, same labels and in-flight guard |
| Cancel | unchanged: toolbar cancellation |
| Info buttons on labels | unchanged |

### Crypto (`CryptoView`)

| Old button | New location |
|---|---|
| Refresh (toolbar) | removed: pull-to-refresh (already present) |
| New (toolbar) | toolbar: `+` ("New Crypto Instance") |
| Card title tap / View | row: `NavigationLink`; also context: View |
| Edit | swipe (leading): Edit; context: Edit |
| Backtest | context: Backtest |
| Start / Stop | context: Start or Stop |
| Delete (confirmation) | swipe (trailing): Delete; context: Delete (destructive); same confirmation |
| (new) Copy ID | context: Copy ID (the ID left the row, P8) |

### Crypto instance (`CryptoInstanceDetailView`)

| Old button | New location |
|---|---|
| Refresh (toolbar) | removed: pull-to-refresh (already present) |
| Start / Stop (full-width bordered) | toolbar: prominent "Start" / "Stop" |
| Edit (full-width bordered) | toolbar: Edit |
| New Backtest | section header action of "Backtests (n)" |
| Backtest card | row: `NavigationLink` to the backtest |
| (new) Copy ID | context menu of the ID row |

### Crypto sheets (`CryptoInstanceSheet`, `CryptoBacktestSheet`)

| Old button | New location |
|---|---|
| Create Instance / Save Changes (bottom bar) | toolbar: confirmation action, same labels and guard |
| Run Backtest / Queuing… (bottom bar) | toolbar: confirmation action, same labels and guard |
| % / $ units picker | section header of "Allocation" |
| Add coin (bordered) | row: borderless "Add Coin" beside the coin menu |
| Remove coin (x) | unchanged |
| "Recommended … — tap to use" | unchanged: footer button |

### All Backtests (`BacktestsView`)

| Old button | New location |
|---|---|
| Refresh / spinner (toolbar) | removed: pull-to-refresh (already present); the spinner stays while a page loads |
| Card tap | row: `NavigationLink` to the backtest |
| Instance id link (in the card) | context: View Instance |
| Pause (while active, confirmation) | swipe: Pause; context: Pause; same confirmation |
| Resume (while paused, confirmation) | swipe: Resume; context: Resume; same confirmation |
| Stop (confirmation) | swipe: Stop (tinted, no full swipe); context: Stop (destructive); same confirmation |
| Per page picker | menu: Per Page |
| Page number buttons | menu: Go to Page (the same `buildPages` set) |
| Previous / Next page | section: borderless chevrons around "Page n of m (t total)" |
| Error "Retry" | section: `ErrorRow` Retry |

### Backtest detail (`BacktestDetailView`)

| Old button | New location |
|---|---|
| Pause / Resume / Stop chips | menu: first section (with their confirmations and the double-submit guard) |
| Rerun chip (confirmation, double-submit guard) | menu: Rerun |
| Playback chip | menu: Playback |
| Delete chip (confirmation) | menu: Delete, last section, destructive |
| AI credits Refresh (header icon) | pull-to-refresh: the screen now refreshes (it had none) and re-fetches the AI credits |
| Strategy header (tap to expand) | section: a `DisclosureGroup` |
| View Logs / Hide Logs | section: "View Logs" row pushes the log viewer (loads on first open, as before) |
| Expand All / Collapse All | section header of "Stock charts & trades" |
| Stock accordion header | row: tap to expand |
| Show more (decision trace) | unchanged: borderless button in the trace |
| Error "Retry" | section: `ErrorRow` Retry |

### Playback (`BacktestPlaybackView`)

| Old button | New location |
|---|---|
| Play / Pause (big green circle) | glass control bar: centre, `.dsGlassProminentButton()` in the accent |
| Reset | glass control bar: leading `.glass` button |
| Speed | glass control bar: trailing `.glass` button |
| Back to Backtest (error / empty) | unchanged |

### Strategies (`StrategiesView`)

| Old button | New location |
|---|---|
| Refresh / spinner (toolbar) | removed: pull-to-refresh (already present) |
| Create Strategy (bordered, full width) | toolbar: `+` ("Create Strategy"), still `go("/instances")` |
| Per page picker ("20/page") | menu: Per Page |
| Sort chips (Name, Best P&L, Best P&L%, Backtests) | toolbar: Sort `Menu`; picking the active field again flips the direction, as the chip did |
| Page number buttons | menu: Go to Page (the same five-page window) |
| Previous / Next page | section: borderless chevrons |
| Top 5 "View Strategy" | row: `NavigationLink`; also context: View Strategy |
| Top 5 "Backtest" | swipe: Backtest; context: Backtest |
| Strategy card tap | row: `NavigationLink` |
| Bar-chart glyph ("Best backtest") | swipe: Best Backtest; context: Best Backtest |

### Strategy detail (`StrategyDetailView`, `StrategyBacktestSheet`)

| Old button | New location |
|---|---|
| Backtest This Strategy (toolbar) | toolbar: play button, same label for VoiceOver |
| Best Backtest (bordered) | section: "Best Backtest" row in Overview |
| Backtest sort chips (Date, P&L, P&L%) | section header: Sort `Menu` of "Backtests (n)" (re-pick flips) |
| Backtest card | row: `NavigationLink` |
| Sheet: Run Backtest (bottom bar) | toolbar: confirmation action, same label, spinner and guard |
| Sheet: instance radio rows | rows with a checkmark |

### Nexus (`NexusView`, `NexusSheets`, `NexusLogsPanel`)

| Old button | New location |
|---|---|
| Start / Re-run (opens the start sheet) | toolbar: prominent "Start" when the service is stopped; menu: Re-run while it runs |
| Stop | toolbar: prominent "Stop" while the service runs |
| Auto-update (header) | menu: Auto-update |
| Configure (auto-update card) | section: "Configure" row in Auto-update |
| Full Rebuild (header) | menu: Full Rebuild |
| Delete Edges (header) | menu: Delete Edges, last section (same disabled rule) |
| Rebuild / Delete Edges (built card duplicates) | menu: the same two items (the duplicates are merged) |
| Retry (failed status) | unchanged: `ContentUnavailableView` action |
| Logs: View Build Logs / Hide Logs (bordered) | section: accent text action in the "Build logs" row |
| Logs: Pause / Resume, Copy, Jump to Latest | unchanged |
| Sheets: confirm (bottom bar) | form: full-width prominent button in the last section (labels too long for the toolbar) |
| Sheets: All / None, Select All / Clear | unchanged: section header buttons |
| Rebuild mode rows | rows with a checkmark |

### Learning (`LearningView`, `LearningTargetsSheet`)

| Old button | New location |
|---|---|
| Start / Stop (engine card) | section: trailing button in the Engine section's first row |
| Mode picker | section: segmented `Picker` in the Engine section |
| Documents & instances (targets label) | section: row in the Engine section, opens the sheet |
| Approve (prominent) / Reject (bordered) | row: two `.bordered` large buttons, Approve green, Reject red, same guard |
| Finding card (tap to expand) | row: `DisclosureGroup` |
| Retry (failed load) | unchanged |
| Targets sheet: Save (bottom bar) | toolbar: confirmation action, same labels and guard |

## Rulings

Format: `Ruling: what — why — cost if wrong`.

1. **Live logs stay inline.** Ruling: Kalshi's and the instance's "Live logs" section holds the shared
   `LiveLogsPanel` as its one row (its header is the "View Live Logs" row; the log opens beneath)
   instead of pushing a screen — a pushed screen would need a second tap on the panel's own button,
   and `LiveLogsPanel` is a DesignSystem component this agent does not own — cost if wrong: the
   panel's header still draws its own capsule button and a monospaced file name; a DS change (an
   `initiallyOpen` flag, a plain-text toggle) would fix both for every caller.
2. **Toolbar Start / Stop is text.** Ruling: the prominent toolbar action reads "Start" / "Stop"
   rather than a play glyph — iOS 26 drops the title of a `Label` in a toolbar, and a bare glyph is
   ambiguous for a trading control — cost if wrong: one line per screen.
3. **Start and Stop share the accent.** Ruling: the prominent toolbar button stays accent-tinted
   for both states (the Dart tinted Stop orange) — `onAccent` text on orange fails 4.5:1 in light
   mode — cost if wrong: Stop loses its orange.
4. **Confirm actions in sheets.** Ruling: Kalshi, Crypto, strategy-backtest and learning-targets
   sheets put confirm in the toolbar with their full Dart labels; the Nexus sheets keep a full-width
   prominent button as the form's last section — Nexus labels ("Confirm Destructive Rebuild") are
   too long for a toolbar item and each Nexus sheet exists for that one action — cost if wrong: two
   conventions for sheets.
5. **Swipe actions that confirm are not destructive roles.** Ruling: Delete (Crypto), Stop
   (Backtests) and Delete (Kalshi backtest) swipes are red-tinted buttons, not `role: .destructive`,
   with full swipe off — a destructive role animates the row away before the confirmation answers;
   the Kalshi backtest delete never had a confirmation, so no full swipe keeps it a deliberate tap —
   cost if wrong: none to behaviour.
6. **Orders split into sections.** Ruling: the instance's "Orders" card becomes four sections —
   "Pending · n", "Filled · n", "Mock positions · n", "Mock filled · n" — with the same
   conditions (Pending and Mock positions always, the other two only when non-empty) — cost if
   wrong: the "Orders" heading itself is gone.
7. **Upper-case words became sentence case.** Ruling: MOCK, LIVE, RANK, AGENT BEST, LINKED, the
   decision/action words (PLACED, BUY, OPEN 3), the status words (FINISHED), "LIVE NOW · n" and every
   eyebrow are sentence case ("Mock", "Live now · 3"); the clock fallback "LIVE" reads "Live". The
   safety copy in the Kalshi sheet ("REAL orders", "Places REAL orders with REAL money") stays as
   written — its capitals are the warning — cost if wrong: one string each.
8. **RANK badges dropped.** Ruling: strategy rows show the rank as a leading medal (SF Symbol,
   gold/silver/bronze) for 1–3 and "#n" for 4–5, with no "RANK n" badge — the spec's "#1 leading
   text, medals as SF Symbols" — cost if wrong: the emoji medals (`StrategyRank.medal`, still
   tested) are unused in the view.
9. **Status colours.** Ruling: decision counts (Placed, Queued, Blocked), the crypto Mode, the
   backtest win rate and portfolio high lost their colours; P&L, price change, edge and fee figures
   keep green and red — the spec's one-meaning-per-colour rule — cost if wrong: one `valueColor`
   each.
10. **Backtest row dates.** Ruling: the All Backtests subtitle formats `yyyy-MM-dd` pairs as
    "Jul 7 – Sep 18, 2026" (the spec's example), falling back to the raw "start → end"; the
    elapsed time and completed-at line stay as a footnote; tickers left the row for the detail's
    Symbols section — cost if wrong: `BacktestRowFormat` is one helper with tests.
11. **The backtest hero.** Ruling: the detail's hero is the total P&L with the P&L % as its change
    and the status dot and dates as its status line; the portfolio chart keeps its own scrub
    readout under it ("Portfolio value over time", value, P&L vs start, date) — cost if wrong: the
    scrub readout duplicates the Results grid's Portfolio cell.
12. **Pull-to-refresh on the backtest detail.** Ruling: the screen gained `.refreshable` (the spec
    requires it), which calls `load()` and `refreshLlmCost()`, replacing the AI-credits refresh
    icon — cost if wrong: one extra GET of the LLM cost per pull.
13. **Backtest logs push a viewer.** Ruling: "View Logs" is a row that pushes `BacktestLogsScreen`
    (first open loads, as the panel's first toggle did); "Hide Logs" is the back button — cost if
    wrong: the inline toggle is gone.
14. **Crypto "24/7" badge dropped.** Ruling: the list and detail no longer show the "24/7" badge
    — every crypto instance is 24/7, and the spec allows one badge per row — cost if wrong: one
    `AppBadge`.
15. **Crypto list title.** Ruling: Crypto takes a large title, as the spec groups it with the
    Instances tab root; Backtests, Nexus and Learning stay inline — cost if wrong: one modifier.
16. **The 3D chart's legend colours.** Ruling: the crypto sheet's legend keeps per-coin colours
    while the `Sector3DChart` wedges are its violet ramp (the Dart did the same; sector3d report
    R17); the colours that matched the old flat donut are gone — cost if wrong: an optional palette
    on `Sector3DChart`.
17. **Nexus primary control.** Ruling: the toolbar shows Stop while the service runs, else Start
    (hidden while building); Re-run moves to the menu while the service runs; a spinner replaces
    the control while a request is in flight — cost if wrong: Re-run is one tap deeper.
18. **Nexus status.** Ruling: the Building / Ready / Idle pill is a `StatusDot` row in the first
    section, with the description as its footer — the spec's "status in a toolbar Menu" would hide
    the build state — cost if wrong: one row.
19. **Learning approvals.** Ruling: Approve and Reject are `.bordered` large buttons, green and red,
    side by side (the spec's Pending AI signals style), instead of prominent + bordered; the red
    outline on a held-forever approval is gone (its red target line stays) — cost if wrong: two
    modifiers.
20. **Finding bodies collapse.** Ruling: a finding's detail text moved into its disclosure with the
    ladder (the spec: "a DisclosureGroup for the body"); the row shows severity, target and title —
    cost if wrong: the detail is one tap away.
21. **Day chips.** Ruling: the Kalshi result's day chips are a "Day" picker menu (P10, no chip
    rows); a scrub still selects the day — cost if wrong: days are one tap deeper.
22. **The playback bar keeps three glass shapes.** Ruling: restart, play and speed sit in one
    `GlassEffectContainer`, centred above the accessory, with gaps wider than its merge spacing so
    each keeps its own glass — `glassEffectUnion` was tried and dropped, because a united shape takes
    one glass variant and the prominent play lost its accent (its glyph vanished in dark mode); a
    tighter merge drew gooey bridges between them — cost if wrong: the "bar" reads as a grouped row of
    three glass buttons rather than one capsule.
23. **Confirm is the prominent checkmark.** Ruling: sheet confirm actions in the toolbar draw as the
    iOS 26 prominent checkmark (`Label(title, systemImage: "checkmark")` with
    `.dsGlassProminentButton()`), the Dart label kept for VoiceOver and a spinner while in flight —
    a text label in the toolbar drew as plain glass and truncated titles such as "Create Kalshi
    Instance" — cost if wrong: the visible "Saving…" / "Queuing…" text is a spinner now.
24. **Strategy rows wrap the name.** Ruling: the strategy rows use a private `StrategyRowLabel`
    (the `EntityRow` layout with a two-line title) because `EntityRow` truncates its title to one
    line and strategy names run long beside a two-line P&L — cost if wrong: a `titleLineLimit` on
    `EntityRow` (DesignSystem, not this agent's) would retire it.
25. **The All Backtests row is two columns.** Ruling: the backtest row lays out name / P&L and
    "#id · dates" / % line by line (Stocks style) instead of `EntityRow`, so the dates get the width
    the narrower % leaves — cost if wrong: one private layout.
26. **Skeletons.** Ruling: the bespoke skeletons (backtest detail, playback, Nexus, strategy
    detail) are a centred `ProgressView`; the list skeletons (Backtests, Strategies) are redacted
    `EntityRow`s — the spec's loading rule — cost if wrong: none to behaviour.
