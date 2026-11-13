# How to build a screen: the web look in SwiftUI

This design system mirrors the web app's tokens (`frontend/tailwind.config.js`) and its `ui-*`
classes (`frontend/src/style.css`). Restyle a screen by swapping system styling for the DS API below; never re-type a hex
value, radius or font at a call site. The reference screens are done: copy them.

- `Features/Dashboard/Views/*` (lists, cards in clear rows, section headers, segmented controls,
  services rows with buttons)
- `Features/Stock/Views/StockView.swift` (hero + chart, stat card, key-value rows)
- `Features/Trade/Views/TradeTicketSheet.swift` (a form: fields with labels above, header/footer)
- `Features/Trade/Views/TradeReviewView.swift`, `Features/SymbolSearch/Views/SymbolSearchView.swift`

Dark only, one graphite ramp, Inter, no hue except gain/loss (money, success/error, destructive)
and amber (a warning or paused dot, a small warning word). The root already applies
`.dsAppearance()`; every tab root and pushed route already gets the page colour.

## 1. Web class → DS API

| Web | SwiftUI |
|---|---|
| page `bg-background-dark` | automatic on routes (`dsScreenBackground()`); sheets: `.presentationBackground(DS.Surface.page)` or `DS.Surface.card` |
| `ui-page-title` / `ui-page-sub` | large navigation title (Inter 30/500, set by the bar); a subtitle line `Text(..).font(DS.Font.pageSub).foregroundStyle(DS.Text.muted)` as the first `dsClearRow()` |
| `ui-section-title` + `ui-section-sub` | `DSSection("Title", subtitle: "…")` in a list; `SectionHeader(title:subtitle:)` outside one |
| `ui-label` | `.font(DS.Font.caption).foregroundStyle(DS.Text.muted)` |
| `ui-card` | `Card { }` (20 pt padding), `Card(padding: 0) { }`, `Card("Title") { }` (header row + divider), `.dsCardSurface()` on any view |
| `ui-th` | `DSTableHeader("Symbol", trailing: "Value")` or `DSTableHeader { … }` |
| `ui-rows` / `ui-row` | in a List: `DSSection { rows }`; outside: `DSRows { DSRow { … } }` |
| `dl` key-value rows | `DSKeyValueRow("Prev close", "$188.42")` (rows 44 tall: `DSSection(…, rowMinHeight: 44)`) |
| `ui-btn-primary` | `.buttonStyle(.dsPrimary)`; bottom CTA `.dsPrimary(size: .large, fullWidth: true)` |
| `ui-btn` | `.buttonStyle(.dsSecondary)`; destructive = `Button("Stop", role: .destructive) {}.buttonStyle(.dsSecondary)` |
| `ui-btn-ghost` | `.buttonStyle(.dsGhost)` (Cancel, Back, Show all) |
| `ui-icon-btn` | `.buttonStyle(.dsIcon)` + `.accessibilityLabel` |
| `ui-seg` | `DSSegmentedControl("Label", selection: $x, options: [...]) { $0.label }` (`fullWidth:`, `segmentHeight: 32` in forms, `isDisabled:`) |
| `ui-input` | `DSTextField("Placeholder", text: $t, prefix: "$")`, or `.dsInput(focused:prefix:)` on your own `TextField` |
| `ui-field-label` + `ui-help` | `DSField("Label", help: "…") { field }`; `DSField("Shares") { accessory } content: { field }` puts a control at the label's end |
| `ui-switch` | any `Toggle` (the root sets `DSSwitchToggleStyle`) |
| `ui-dot` + word | `StatusDot("Running", tone: .live, pulsing: true)`, `StatusDot(word, status: serverStatus)`, `DSStatusDot(.warn)` alone |
| `ui-error` | `ErrorRow(message:onRetry:)`; `.dsErrorSurface()` behind custom content |
| `ui-empty` | `EmptyState(title: "No orders yet", subtitle: "…", actionLabel:, onAction:)` inside a card or row |
| `ui-skel` | `Skeleton(width:height:)` shaped like the content; no `.redacted` shimmer-alikes for new code |
| hero figure | `HeroValueHeader(value, change:, direction:, changeLabel: "Today")` (40/500, change 14 gain/loss) |
| stat block | `StatGrid(columns: 2) { StatCell(label:value:footnote:) }` (2 or 3 columns) + `.statValueStyle(.medium)` (or `.figure`, `.large`) |
| row | `EntityRow(title, subtitle:) { EntityRowValue(value, detail:, detailColor:) }`; symbols 14/500: `.entityRowTitleMedium()` |
| sparkline / chart | `Sparkline(values:)`, `ScrubbableAreaChart(…, showsYAxis: true)` |
| modal | a sheet; native `.alert` / `.confirmationDialog` stay native |

Type roles (`DS.Font`): `pageTitle` 24/500, `sectionTitle` 16/500, `body` 14 (default),
`bodyMedium` 14/500, `base` 16, `baseMedium`, `small` 13, `smallMedium`, `caption` 12,
`captionMedium` 12/500, `micro` 11 (axes), `large` 18/500, `figure` 20/500, `figureLarge` 24/500,
`hero` 40/500, `mono`/`monoSmall` for logs and ids. Add `.monospacedDigit()` to every figure.

Colours: `DS.Surface.page|card|raised|hover|bar`; `DS.Text.primary` (zinc-100) `soft` (200)
`label` (300) `secondary` (400) `muted` (500) `faint` (600); `DS.Palette.accent` (off-white),
`gain`, `loss`, `amber`; `DS.Line.card|divider|row|input|focus`; `DS.Chart.*`. Legacy names still
compile and resolve to these: `success`=gain, `danger`=loss, `warning`=amber, `info`/`teal`=zinc-400,
`up`/`down`, `Surface.canvas|panel|inset`. Motion: `DSMotion.standard` (150 ms ease-out),
`DSMotion.ease(_:)`; never `.spring`/`.bouncy`/`.snappy` on chrome.

## 2. Before → after

```swift
// System fonts → Inter roles
Text(name).font(.headline)                    →  Text(name).font(DS.Font.bodyMedium)   // row title: DS.Font.body
Text(sub).font(.subheadline)                  →  Text(sub).font(DS.Font.body)          // or .caption for a subline
Text(meta).font(.footnote)                    →  Text(meta).font(DS.Font.caption)
Text("$1,204").font(.title2.weight(.semibold))→  Text("$1,204").font(DS.Font.figure).monospacedDigit()
Text(x).font(.caption.monospaced())           →  Text(x).font(DS.Font.mono)
.fontWeight(.semibold) / .bold()              →  pick the medium role; never weight modifiers

// Secondary text → the zinc ramp (be explicit)
.foregroundStyle(.secondary)                  →  .foregroundStyle(DS.Text.muted)      // sublines, labels, meta
                                                  .foregroundStyle(DS.Text.secondary)  // secondary body text
.foregroundStyle(.tertiary)                   →  .foregroundStyle(DS.Text.faint)
Color.primary / Color.secondary               →  DS.Text.primary / DS.Text.muted      // these are NOT remapped

// Hues → meaning only
.foregroundStyle(Color.green)                 →  .foregroundStyle(DS.Palette.gain)    // signed money / success only
.foregroundStyle(.orange) (a warning)         →  HStack { DSStatusDot(.warn); Text(w) }   // or DS.Palette.amber on a short word
.foregroundStyle(.blue|.purple|.teal) (chrome)→  DS.Text.secondary (or primary), no hue
pnlColor(x)                                   →  dashboardChangeColor(pct) (zinc-500 for ~0%) or pnlColor (gain/loss)

// Tinted badges → dot + word, or quiet text
Text("Live").dsBadge(.green)                  →  StatusDot("Live", tone: .live)
StatusBadge(label: "Failed", color: .red)     →  unchanged call; it now draws ● Failed (12 pt)
Text("AI").padding().background(.blue.opacity(0.15), in: Capsule())
                                              →  Text("AI").font(DS.Font.caption).foregroundStyle(DS.Text.secondary)

// Icon tiles → plain muted glyph
SettingsIconTile(systemImage: "gearshape", color: .gray)   (now draws a 17 pt zinc-500 glyph; drop `color:`)
Image(systemName: s).foregroundStyle(.tint)   →  .foregroundStyle(DS.Text.muted), .font(.system(size: 16|17))

// Segmented picker
Picker("Mode", selection: $mode) { … }.pickerStyle(.segmented)
                                              →  DSSegmentedControl("Mode", selection: $mode, options: Mode.allCases) { $0.label }

// Prominent / glass buttons
Button("Start") {}.buttonStyle(.borderedProminent)  →  .buttonStyle(.dsPrimary)
Button("Stop") {}.buttonStyle(.bordered)            →  .buttonStyle(.dsSecondary)
Button("Run backtest") {}.dsGlassProminentButton()  →  .buttonStyle(.dsPrimary(size: .large, fullWidth: true))
Button(...).buttonStyle(.glass)                     →  .buttonStyle(.dsSecondary)

// Cards
.background(DS.Surface.panel, in: .rect(cornerRadius: 22))   →  .dsCardSurface()   // or Card { }

// Empty / error / loading
ContentUnavailableView("No data", systemImage: …)  →  EmptyState(title: "No data yet", subtitle: "…")
ProgressView() as a first load                     →  Skeleton blocks shaped like the content
```

Copy: sentence case ("New instance", "Cancel order"), no trailing period on titles, labels,
buttons or one-line subtitles, no exclamation marks. Borrow the web's words where it has the same
screen (`frontend/src/views/*.vue`). Keep every fact, field and action.

## 3. A List, the web way

```swift
List {
    Section { pageHeader.dsClearRow(top: 8) }                // anything with its own surface
    DSSection("Holdings", subtitle: "5 positions") {          // header outside, rows on one card
        ForEach(rows) { r in
            NavigationLink(value: Route.stock(…)) { EntityRow(r.symbol, subtitle: r.qty) { EntityRowValue(…) } }
                .swipeActions { Button("Cancel order", systemImage: "xmark") { … }.tint(DS.Palette.loss) }
                .contextMenu { … }
        }
    } accessory: {
        DSSegmentedControl("P&L period", selection: $mode, options: [.daily, .total]) { $0.label }
    }
    DSSection("Market", subtitle: "Today's session", style: .plain) {   // header, no card
        Card("Sector performance", padding: 0) { … }.dsClearRow()
    }
}
.dsList()
```

- `.dsList()` replaces `.listStyle(.insetGrouped)`: grouped (headers scroll), page background,
  32 pt between sections. **Every `Section` must become a `DSSection`** (or hold only
  `dsClearRow()` rows): a raw `Section` in a `.dsList()` draws full-width grey rows.
- `DSSection` draws the card per row (rounded first/last, 7% outline, 5% hairlines between rows,
  56 pt min height, 20 pt inside, page gutter 16). It splits its content with `Group(subviews:)`,
  so `ForEach`, `if`, swipe actions, context menus and `NavigationLink` chevrons all work. Change
  the row height with `rowMinHeight:` (44 for key-value rows, 68 for two-line rows with buttons).
- `action: DSSectionAction("Add", systemImage: "plus") { }` is a ghost verb in the header;
  `accessory:` takes any view; `footer: "…"` is a 12 pt zinc-500 line under the card.
- Buttons inside a row: give each a DS style (`.dsSecondary`, `.plain`) so each is its own target.
- Swipe actions draw a white label on their tint: use `.tint(DS.Swipe.neutral)` (zinc-700) for
  every non-destructive action and `.tint(DS.Palette.loss)` for a destructive one. Never
  `.tint(DS.Palette.accent)` or `.tint(DS.Palette.info)` there: white on off-white/zinc-400 is
  unreadable (Backtests, Crypto, Strategies, Live trading still do this).
- A `Form`: convert it to a `List` + `.dsList()` with `DSSection`s, or to a `ScrollView` of
  `DSField`s like the trade ticket. Toggles in rows already draw the web switch.
- Keep `.refreshable`, `.searchable` and every `.task`; only styling moves.
- Header with an icon refresh: `DSSection(…) { … } accessory: { Button {…} label: { Image(systemName: "arrow.clockwise") }.buttonStyle(.dsIcon) }`.

## 4. Chrome, sheets, toolbars

- Navigation bars are configured globally (Inter titles, off-white buttons, page colour). iOS 26
  draws bar buttons and the tab bar as Liquid Glass and ignores a custom tab bar appearance; leave
  both alone. Large titles show only because the scroll-edge appearance is transparent: do not
  set a bar background per screen.
- Toolbar items: plain `Button("Start")`/`ToolbarAddButton`/`ToolbarMenu`. Do **not** put
  `.dsPrimary` in a toolbar (it draws a white slab inside the glass capsule).
- Sheets: keep the grabber; set `.presentationBackground(DS.Surface.card)` (or `.page`). A form
  sheet can follow `TradeTicketSheet`: header (title 16/500 + subtitle, `xmark` icon button),
  fields, footer (`.dsGhost` Cancel + `.dsPrimary` action over a `DSDivider()`).
- Status: green only for something actually running/live (`.live`, may `pulsing`); paused amber;
  stopped/queued zinc-600; done zinc-400 (`.neutral`); failed loss. `StatusBadge.color(forStatus:)`
  and `StatusDot(_, status:)` already use this map.
- Instances (running cards): copy the web's `.pin-*` CSS (end of `frontend/src/views/InstancesView.vue`): ping the dot
  with `DSStatusDot(.live, pinging: true)`, a green-tinted card border (`DS.Palette.gain.opacity(0.18)`
  via `.dsCardSurface(border:)`), the orbiting light and glow (build it in the Instances folder;
  respect Reduce Motion), live ticking uptime (`TimelineView`), and the sparkline's pulsing end dot.

## 5. Snapshot harness

Files: `ios/IntelliStockTests/Snapshots/SnapshotHarness.swift` (engine, docs at the top),
`SnapshotScreens.swift` (one `@Test` per screen), `SnapshotFixtures.swift` (stub JSON by path;
account ids `SnapshotFixtures.alpacaLiveId`, `paperTestId`, `swingPaperId`, `kalshiId`).

Run (skipped unless `IS_SNAPSHOT_DIR` is set; `TEST_RUNNER_` passes it to the test process):

```sh
cd ios && xcodegen generate
TEST_RUNNER_IS_SNAPSHOT_DIR=/path/out TEST_RUNNER_IS_SNAPSHOT_ONLY=instances \
xcodebuild test -project IntelliStock.xcodeproj -scheme IntelliStock \
  -destination 'platform=iOS Simulator,id=<your sim id>' -derivedDataPath /private/tmp/claude-501/dd-<you> \
  -only-testing:IntelliStockTests/SnapshotScreens
```

Add a screen:

```swift
@Test func backtests() async throws {
    try await Snapshot.capture("backtests", pages: 3) { services in
        SnapshotHost.shell(services, tab: .more, stack: [.backtests])   // real tab + nav bars
    }
}
// A sheet: Snapshot.capture("x") { _ in SnapshotHost.sheet { MySheet(…) } }
// Data first: let env = Snapshot.makeServices(); …await env.services.someRepository…;
//             Snapshot.capture("x", environment: env) { … }
// Typing: Snapshot.capture("x", prepare: { w in Snapshot.type("AA", in: w); Snapshot.endEditing(w) }) { … }
```

Missing data? Add the path to `SnapshotFixtures.respond` (read the model's `init(json:)` for keys).
`IS_SNAPSHOT_ONLY` filters by name prefix. PNGs are 1206×2622 (3x); `pages:` scrolls the main
scroll view. Use a simulator id (a new simulator's name is not found by `xcodebuild` at first).

## 6. Leftover grep (run on your folders; justify or fix each hit)

```sh
grep -rnE '\.purple|\.indigo|\.blue|\.teal|\.orange|\.pink|\.mint|\.cyan|Color\.accentColor|\.accentColor|\.bold\(|fontWeight\(\.(bold|heavy|black|semibold)|\.textCase\(\.uppercase|\.uppercased\(\)|\.font\(\.(largeTitle|title|title2|title3|headline|subheadline|body|callout|footnote|caption|caption2)\b|Color\.(green|red)\b|LinearGradient|\.ultraThinMaterial|\.regularMaterial|\.shadow\(' <folders>
# also: insetGrouped, pickerStyle(.segmented), glassEffect, buttonStyle(.glass, borderedProminent,
#       foregroundStyle(.secondary), Color.secondary, Color.primary, ContentUnavailableView, Section {
```

Accepted: `.uppercased()` on tickers/data, `LinearGradient` only for the faint fade under a P&L
line (`DS.Chart.areaTopOpacity` → 0), `.shadow` only on a floating menu/toast, `.font(.system(size:))`
for SF Symbols.

## 7. Things that bite

- `DS.Font` is static Inter; `.fontWeight`/`.bold()` on it fakes a weight. Use the role.
- `.foregroundStyle(.secondary)` resolves to zinc-400 and `.tertiary` to zinc-500 (root mapping),
  but `Color.secondary`/`Color.primary` are system colours and are not remapped.
- `Card("Title")` now draws a header row with a divider (the web's card header), not a title in
  the padding; for a bare title use `Card { VStack { Text(t).font(DS.Font.bodyMedium) … } }`.
- `StatusBadge`, `AppBadge`, `.dsBadge` no longer draw capsules; `IconTile` and
  `SettingsIconTile` are bare zinc-500 glyphs (`EntityRowMetrics.iconSize` is 24).
- `HeroValueHeader` draws no arrow; pass `changeLabel` ("Today", or the range caption via
  `dashboardRangeCaption(range)`).
- Do not feed an `onGeometryChange` measurement back into the same view's padding; no TaskGroups
  capturing MainActor state.
- Accessibility identifiers, routes, data flow and tests' behaviour must not change; update only
  tests that assert old colours or strings.
