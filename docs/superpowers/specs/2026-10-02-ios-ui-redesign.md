# iOS UI redesign: make it feel like Apple built it

Date: 2026-10-02 · Branch: `feat/native-ios-app` · Follows: `2026-10-01-native-ios-port-design.md`

## Why

The operator ran the app on the simulator and said, on 2026-10-02: "it look sooo bad, nothing fully
apple like, this needs to be made more professional".

The orchestrator reviewed all 56 tour screenshots against the HIG. The screens work, but they read
as a web dashboard poured into iOS. These patterns repeat on nearly every screen:

| # | Pattern | Seen on | Why it reads as "not Apple" |
|---|---|---|---|
| P1 | A solid violet chat circle floats bottom-right over content | every screen | It covers rows, buttons and the Halt control. Apple keeps persistent entry points in bars or accessories, not over content (`tab-bars.md`, `liquid-glass.md`: the functional layer floats *beside* content, it does not hide it) |
| P2 | UPPERCASE tracked eyebrows ("TRADING", "EDGE RADAR", "INSTANCE INFO", "KEY STATISTICS", "PREV CLOSE") | everywhere | Web-dashboard idiom. Apple uses title-style headers and sentence-case secondary labels |
| P3 | Rows of coloured pill buttons on every card (Pin · View · Live · Start · Delete; Edit · Remove; Rerun · Playback · Delete) | Instances, Crypto, Brokerages, Backtests, Nexus, Models | Five tinted capsules per row are noise. Apple lets the row navigate, puts secondary actions in swipe actions and context menus, and puts primary actions in toolbars |
| P4 | White cards nested in white cards, with grey inset tiles inside | all detail screens | Apple apps group content in inset-grouped lists, one level deep |
| P5 | Refresh (↻) buttons in nav bars | 12 screens | iOS uses pull-to-refresh |
| P6 | Titles repeated: the nav bar says "Backtest #175059" and the hero repeats it; "Live Trading" vs "Live Trading Terminal"; Crypto "Test" twice; "Token Usage" twice | details | The title belongs to the nav bar; the hero shows the *number* |
| P7 | An eyebrow + headline + paragraph block under the large title ("BROKERAGES / Linked Accounts / Manage your…", "MODELS / LLM Models / Centralized…") | list roots | Marketing copy. The large title is the header; a footer can explain |
| P8 | Monospace everywhere (IDs, config keys, model names, masked keys) and raw UUIDs (`bf78ad0c-3073-…`) | Instances, Models, Strategy detail | Monospace is for code and logs only; UUIDs truncate in the middle, in secondary colour |
| P9 | Many badge styles (USER, AI, PRE, 24/7, FINISHED, Stopped, Live, RANK 3) in different shapes and colours | everywhere | One badge style, used sparingly |
| P10 | Sort chips row ("Sort: Name · Best P&L ↓ · Best P&L% · Backtests"), "20/page" controls, "Create Strategy" pill | Strategies, Backtests | Sorting and paging go in a toolbar `Menu`; create is a `+` toolbar button |

Keep (already Apple-like): the system tab bar, the More list, Settings, the Connect and Login forms,
the segmented range pickers, the glass nav-bar buttons, and the dashboard hero number.

## Design language (binding for every view)

Model it on **Stocks** (numbers, charts), **Wallet** (cards, transactions) and **Settings** (forms,
lists).

- **Surfaces.** `List` with `.listStyle(.insetGrouped)` is the DEFAULT container for any screen made
  of rows, key-value pairs or settings. `Card` is only for a hero chart or a visual summary that is
  not row-shaped. Never nest a card inside a card. Never put a grey tile inside a card; use
  `LabeledContent` rows or a stat grid.
- **Navigation titles.**
  - Tab roots use large titles.
  - Pushed screens use the INLINE nav title, set to the entity name.
  - Never repeat the title in content.
- **Headers.**
  - Section headers are standard `Section("Title")`: title-style, no tracking, no icons in headers.
  - Card titles are `.headline`.
  - No uppercase text anywhere except tickers and genuine acronyms (`P&L`, `SPY`).
  - Delete every `.textCase(.uppercase)`, `.tracking(...)` and eyebrow style.
- **Key-value data.**
  - Rows: `LabeledContent("Strategy", value: "Swing trader paper")`.
  - Numeric grids: a new `StatGrid` (2 or 3 columns, Stocks style). Each cell is a label in
    `.caption`/`.secondary` with sentence case ("Prev close", "52W high"), over a value in
    `.body.monospacedDigit()`.
- **Hero.**
  - Big value: `.dsValueHero()`, monospaced digits.
  - Change line below: `.subheadline`, green or red, with an arrow SF Symbol (`arrow.up.right` /
    `arrow.down.right`).
  - One secondary status line in `.footnote`/`.secondary`.
  - Then the chart, then the range `Picker(.segmented)`.
- **Actions.**
  - **Lists:** tapping the row navigates; it never carries a button row. Secondary actions go in
    `.swipeActions` (Pin, Delete) and `.contextMenu` (Start, Stop, View Live, Edit, Delete).
    Destructive actions use `role: .destructive` and keep their confirmations.
  - **Detail screens:** the single primary action goes in the toolbar: Start/Stop as a
    `.dsProminentButton()` `ToolbarItem`, or a full-width prominent button in the first section
    when it is the main purpose of the screen. Everything else goes in a toolbar `Menu`
    (`ellipsis.circle`), grouped with `Section`s, destructive last.
  - **Inline secondary actions** inside a section are plain `Button`s styled as list rows (blue or
    accent text). Never use tinted capsules.
  - Keep every action and every confirmation. This changes their **placement and style only**.
- **Status.**
  - One `StatusBadge` style: a small capsule in `.caption2.weight(.semibold)`, colour text on a 15 %
    fill.
  - Use at most one badge per row.
  - Running state is a coloured dot plus a word ("Running", "Stopped").
  - User/AI origin is plain secondary text, not a badge.
- **Type.** System text styles only:
  - `.headline` for row titles;
  - `.subheadline`/`.secondary` for subtitles;
  - `.footnote` for metadata;
  - monospaced only for logs, tickers inside tables, and code-like config keys in an expanded
    "Raw config" disclosure.
- **IDs.** Hide raw UUIDs behind a disclosure, or show them `.truncationMode(.middle)` in
    `.secondary` with a copy context menu. Show human names (brokerage name, strategy name) instead.
- **Colour.**
  - Accent violet for interactive elements only.
  - Green and red only for P&L and price change.
  - Status colours only in `StatusBadge`.
  - No coloured section-header icons and no coloured card titles.
  - Settings-style icon tiles are allowed only in navigation lists (More, Settings).
- **Charts** (Swift Charts):
  - line width 2 (sparklines 1.5) with `.interpolationMethod(.monotone)`;
  - a flat area fill at `DS.chartAreaOpacity`;
  - axis labels in `.caption2` secondary, with at most 4 x-axis labels;
  - the gridline is a single dashed baseline at the start value (Stocks does this);
  - no chart fills the whole screen width edge to edge without margins.
- **Empty and error states.** `ContentUnavailableView` for full-screen empties. Inside a section, one
  `.secondary` row ("No open positions"). Errors use `ErrorRow` with Retry.
- **Loading.** `ProgressView()` centred for a full screen, or `.redacted(reason: .placeholder)` rows
  that keep the real layout. No bespoke skeleton bars.
- **Pull-to-refresh** on every list and detail screen. **Remove every refresh toolbar button.**
- **Spacing.** Use system list insets. Never add custom padding around `List` content. Inside
  `Card`s, use 16 pt padding and 12 pt between groups.
- **Still banned:** gradients, glows, coloured shadows, blur; Liquid Glass on content.

## Global changes (DesignSystem / App: do these first)

**G1. Chat entry.** Delete the floating violet button.
- **Where it goes:** `MainTabView` uses iOS 26 `.tabViewBottomAccessory { ChatAccessoryView() }`, a
  compact glass capsule above the tab bar: `sparkles` glyph, "Ask IntelliStock", and the current
  model name in `.secondary`. Tapping it presents the existing chat sheet.
- **What stays the same:** keep all chat behaviour. Only the entry changes.
- **Lock:** while locked, there is no accessory (the content is replaced anyway).

**G2. Component kit.** Add or replace these in `DesignSystem/Components/`:
- **`StatGrid`:** a `Grid` of `StatCell(label:value:valueColor:)`.
- **`HeroValueHeader`:** value, change and status line.
- **`EntityRow`:** leading `IconTile` (optional), title, subtitle, trailing value or badge, and a
  chevron via `NavigationLink`.
- **`StatusBadge`:** restyle to the single style above.
- **`DSSection`:** a convenience wrapper over `Section` with a title, an optional trailing header
  action, and a footer.
- **`StatusDot`:** a coloured dot plus a label.
- **`FlowLayout`:** if the fix agent hasn't already added it.
- **`Card`:** keep, but restrict it to hero and chart use, with radius 22 and 16 pt padding.
- **Remove, or make unused:** eyebrow/`SectionHeader` styles that uppercase, and every pill-button
  helper.

**G3. Toolbar convention.**
- `+` for create.
- `ellipsis.circle` `Menu` for the rest.
- No refresh buttons.
- The search button stays on Dashboard (a `magnifyingglass` toolbar item).

## Per-screen directives

### Dashboard (tab root)

- **Top of the screen:**
  - Large title "Dashboard". The account switcher becomes a toolbar `Menu` titled with the account
    name, NOT an "ALPACA ⌄" eyebrow under the title.
  - Hero: `HeroValueHeader`, then the chart, then the range picker. "Markets Closed" goes in the
    status line.
- **Holdings:** a `Section("Holdings")` of rows, with the Total/Daily picker in the section header,
  trailing:
  - leading: a small allocation ring (24 pt);
  - title: ticker, plus shares in the subtitle;
  - middle: a sparkline (60×24);
  - trailing: value, with the change below in green or red.
  - Show full share counts; drop "sh…" truncation by shortening to "22.4 sh".
- **Insights:** a horizontally scrolling row of compact cards, kept, but title-case.
  - "Today", "Diversification" and "Risk" become `StatGrid`s.
  - "Sector allocation" is the donut `Card`.
  - "Sector performance" is a list of rows with a bar.
- **Market:** "S&P 500 / Nasdaq / Dow" mini cards stay as cards, Stocks-widget style.
- **Services and strategy cards:** `Section`s with `EntityRow`s and status dots. Their engine
  controls go in each row's context menu, plus one inline button where the Dart had a primary
  action.

### Kalshi (tab root)

- **Account:** large title. The account picker is a toolbar `Menu`, not a white field.
- **Instances:** a `Section("Instances")` of `EntityRow`s (status dot, Live/Paper as plain text),
  with "New Instance" as a `+` toolbar item.
- **Portfolio:** `HeroValueHeader`, then the chart card.
- **Edge radar, open positions:** `Section`s with rows or an empty row.
- **Live logs:** a `Section("Live logs")` with a single row, "View Live Logs", that pushes or opens
  the `LiveLogsPanel`. No big empty card.

### Instances (tab root) · Crypto

- **Header:** large title. The All/User/AI filter is a `Picker(.segmented)` in the list header, with
  no eyebrow block.
- **Rows:** each instance is ONE `EntityRow`:
  - title: name;
  - subtitle: "Strategy 194 · Swing Trade Paper", using the strategy and brokerage NAMES where the
    data has them, otherwise the ID truncated in the middle;
  - trailing: a status dot plus "Running" or "Stopped";
  - a pin glyph when pinned;
  - tap: Instance Detail.
- **Row actions:**
  - swipe leading: Pin/Unpin;
  - swipe trailing: Delete (with its confirmation);
  - context menu: Start/Stop, View Live, Pin, Delete.
- **Stocks:** move the "Stocks (0) + Add / No stocks added" block INTO Instance Detail as a section.
  Keep it reachable and functional there.

### Instance detail · Live Trading

- **Instance detail:**
  - inline nav title: instance name;
  - toolbar: Start/Stop as the primary prominent button; a `Menu` holding Live Trading, Clear State,
    Change Brokerage, Unlink Brokerage, Unlink Strategy and Delete (destructive actions keep their
    typed confirmations);
  - sections: Status (a `StatGrid` with uptime, granularity, created by), Brokerage
    (`LabeledContent` rows), Strategy, Stocks, Pending AI signals, Logs.
- **Pending AI signals:**
  - each signal is a section; the stat fields go in a `StatGrid`, and the rationale is a
    `DisclosureGroup`;
  - Approve and Reject are two `.bordered` buttons, `.controlSize(.large)`, side by side, with
    Approve `.tint(.green)` and Reject `.tint(.red)`; keep the confirmations.
- **Live Trading:**
  - inline title "Live Trading", subtitle the instance id;
  - hero: portfolio equity (`HeroValueHeader`), then the chart, then the range picker;
  - the chart-style picker is a `Picker(.segmented)` with SF Symbols;
  - Range high/low and day/total P&L go in a `StatGrid`;
  - Cash, buying power and total are a `StatGrid`;
  - positions are rows; executions are a `Section("Recent executions")` of rows: "SELL EL" title,
    the date as subtitle, "131 sh @ $89.80" trailing, and the total below;
  - Halt stays a floating glass button, raised above the tab-bar accessory; Manual Order is a
    toolbar button.

### Strategies (tab root) · Strategy detail

- **Strategies list:**
  - large title; toolbar: a sort `Menu` (Name, Best P&L, Best P&L %, Backtests) and a per-page
    `Menu`; Create Strategy is a `+` toolbar item;
  - "Top 5 best strategies" is the first `Section` (rank shown as "#1" leading text in `.headline`,
    medals are fine as SF Symbols);
  - rows: `EntityRow` with the name as title, "ID 179 · 2 sub-strategies" as subtitle, and P&L as a
    trailing 2-line value;
  - the bar-chart glyph button becomes a context-menu item or swipe action, "Backtest".
- **Strategy detail:**
  - inline title: the name; toolbar: a play button for "Backtest";
  - sections: Overview (`StatGrid`), Sub-strategies (rows; each sub-strategy pushes or expands to its
    config);
  - config is `LabeledContent` rows with human labels; raw keys sit in a "Raw config" disclosure in
    monospace.

### Backtests (More) · Backtest detail · Playback

- **List:**
  - each backtest is an `EntityRow`: title the instance name, subtitle "#554589 · Jul 7 – Sep 18,
    2026"; trailing P&L in green or red with the % below; a status dot only when not finished;
  - tickers are dropped from the row (they belong to the detail);
  - paging and per-page go in a toolbar `Menu`, or load-more at the bottom.
- **Detail:**
  - inline title "Backtest #175059"; toolbar `Menu`: Rerun, Playback, Delete (each with its
    confirmation and the double-submit guard);
  - hero: P&L, then the portfolio chart;
  - `StatGrid`: P&L %, portfolio, trades (buy/sell), win rate, high/low, elapsed;
  - "AI credits" is a section with a `StatGrid`; "By model / call site / provider" are
    `LabeledContent` rows;
  - tickers are a `FlowLayout` of small neutral tags in a "Symbols" section;
  - logs are a row that opens the log viewer.
- **Playback:**
  - the transport controls are a glass control bar (play/pause, restart, speed) using
    `.dsGlassProminentButton()` for play and `.glass` for the others, centred at the bottom above the
    accessory;
  - no big green circle.

### Kalshi instance · Kalshi backtest · Kalshi result · Crypto instance

- **Same rules:**
  - inline title;
  - toolbar primary action plus a menu;
  - stats in `StatGrid`, lists as sections;
  - the decision log is rows: flags, match, outcome subtitle, trailing edge % and a status word;
  - pregame analysis is rows;
  - the Kalshi backtest form is already a native `Form`, so keep it, and remove any card wrappers.
- **Crypto instance:** Start and Edit move to the toolbar; the allocation donut is a `Card`; instance
  info and brokerage are `LabeledContent` rows.

### Stock · Search

- **Stock:**
  - inline title: the ticker; hero: price and change (with the name as the status line);
  - chart, then the range picker;
  - "Key statistics" is a 3-column `StatGrid` with sentence-case labels;
  - About, Bot activity and Order history are sections.
- **Search:** keep the native `.searchable`.

### Brokerages · Models · Token Usage · Agent Runs · Nexus · Learning · Notifications

- **Brokerages:**
  - rows: brand logo, name, subtitle "Alpaca · Paper · PA3IBY5S84PG", trailing status dot;
  - tap: an edit sheet; swipe: Remove (confirmation kept); `+` toolbar: Link Brokerage;
  - no eyebrow/headline block.
- **Models:**
  - rows: provider glyph, name ("Azure / gpt-oss-120b — High"), subtitle "Azure OpenAI · Created
    Apr 12, 2026";
  - tap: the editor sheet; swipe: Delete (confirmation kept); `+` toolbar: Add Model;
  - the masked key is shown only in the editor.
- **Token Usage:**
  - large or inline title, with no second title and no description card;
  - the window picker sits at the top; stats go in a `StatGrid`; the trend chart is a card;
  - "Top spenders…" sections hold rows.
- **Agent Runs:**
  - controls are a `Section` with buttons as rows; runs are rows;
  - the countdown ring stays.
- **Nexus:**
  - status and controls go in a toolbar `Menu`, plus a primary Start/Stop;
  - auto-update is a section;
  - graph counts are rows (name as title, raw key as subtitle in `.caption` `.secondary`, count
    trailing);
  - bootstrap is a section.
- **Learning:**
  - stats in a `StatGrid`;
  - the engine is a section with a mode `Picker(.segmented)` and a Start/Stop button;
  - findings are rows with a `StatusBadge` for severity and a `DisclosureGroup` for the body;
  - observed runs are rows.
- **Notifications:**
  - an insetGrouped `Form`;
  - "Test delivery" is a section with two button rows;
  - devices are rows with swipe-to-delete;
  - each alert type is a section titled with the type, with two `Toggle` rows: "Discord" and "iOS
    push".

### Settings · More · Connect · Login · Onboarding

These are already close. Remove any uppercase or tracking, keep them as `Form`/`List`, and make sure
the icon tiles match the More list. Login: drop the "Unlock with biometrics" toggle card into a
plain row under the form, as Settings rows look.

## Acceptance

1. **Behaviour unchanged.**
   - The full unit suite stays green.
   - No endpoint, body, string, guard or confirmation changes, apart from the documented ones:
     placement, capitalisation, and the title de-duplication.
   - Every action that existed before is still reachable, now via the row, a swipe, a context menu
     or the toolbar. Each Wave R agent lists, per screen, where each old button went.
2. **The tour reruns,** and the orchestrator reviews every screenshot against this spec.
   - Any remaining P1–P10 pattern is a defect.
   - The tour's back-navigation and row lookups may need small updates, because rows are now
     `NavigationLink`s.
3. **Mechanical rules:** no gradients, no uppercase eyebrows, no refresh buttons, no pill-button
   rows, no nested cards.
