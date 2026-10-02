# How to build a screen

This is the working guide for every IntelliStock screen. The binding rules are in
`docs/superpowers/specs/2026-10-02-ios-ui-redesign.md`. This page shows how to meet them with the
kit in `DesignSystem/Components/`. Model each screen on Stocks (numbers, charts), Wallet (cards,
transactions) and Settings (forms, lists).

## The rules that matter most

1. **Lists, not cards.** Any screen made of rows, key-value pairs or settings is a `List` with
   `.listStyle(.insetGrouped)`. A `Card` holds only a hero or a chart. Nothing nests more than one
   level: no card in a card, and no grey tile in a card.
2. **Titles.** Tab roots get a large title. A pushed screen gets an inline title naming the
   entity. Content never repeats the title; the hero shows the number instead.
3. **No upper case** except tickers and real acronyms (`P&L`, `SPY`, `AI`). Never use
   `.textCase(.uppercase)`, `.tracking(_:)`, `.uppercased()` on a label, or an eyebrow.
4. **Rows navigate. They never carry buttons.** Secondary actions go in `.swipeActions` and
   `.contextMenu`. The primary action goes in the toolbar, and everything else in a `ToolbarMenu`.
   Every action and every confirmation stays; only its place and style change.
5. **Colour has one meaning each.** Violet marks something you can tap. Green and red mark P&L
   and price change. Status colours appear only in `StatusBadge` and `StatusDot`. Headers, card
   titles and section icons get no colour.
6. **Type is system text styles only.** Use `.headline` for row titles, `.subheadline`
   `.secondary` for subtitles, and `.footnote` for metadata. Monospace is for logs and raw config
   keys.
7. **Refresh by pulling.** Every list and detail screen gets `.refreshable`. There are no refresh
   buttons.
8. **Still banned:** gradients, glows, coloured shadows, blur, and Liquid Glass on content. Glass
   belongs only on floating controls; the system bars and the chat accessory get it automatically.

## The kit

| Component | Use it for |
|---|---|
| `EntityRow(title, subtitle:, systemImage:, isPinned:) { trailing }` | One thing in a list (an instance, a backtest, a model). Wrap it in a `NavigationLink`. |
| `EntityRowValue(value, color:, detail:, detailColor:)` | An `EntityRow`'s trailing figure, with an optional second line (P&L, %). |
| `StatusDot(label, color:, pulsing:)` / `StatusDot(label, status:)` | Run state: "● Running", "● Stopped", "● Markets closed". |
| `StatusBadge(label:, color:, pulsing:)` | One flag worth noticing (severity, Live, Failed). At most one per row. |
| `AppBadge(label:, color:)` | The same capsule as `StatusBadge`. The label is sentence-cased. |
| `.dsBadge(color)` | The badge capsule on any text, for a one-off tag. |
| `DSSection(title, action:, footer:) { rows }` | A list section with an optional trailing header verb and a footer. |
| `DSSectionHeader(title) { accessory }` | A section header with a custom accessory: a `Picker` or a `Menu`. |
| `InlineActionRow(title, systemImage:, role:, isBusy:) { }` | A secondary action shown as a list row in accent text, or red when destructive. |
| `HeroValueHeader(value, numericValue:, change:, direction:, changeLabel:, status:, statusColor:)` | The big figure, the change line and the status line at the top of a value screen. |
| `ChangeDirection(delta)` | The direction `.up`, `.down` or `.flat`, which sets the colour and the arrow. |
| `StatGrid(columns: 2 or 3) { StatCell(label:, value:, valueColor:, footnote:) }` | Numeric summaries in the style of Stocks' key statistics. |
| `Card { }` / `Card("Title") { }` | A hero or a chart, with 22 pt corners and 16 pt padding. |
| `ScrubbableAreaChart(...)` | The value chart: 2 pt monotone line, flat fill, a dotted baseline at the start value, and at most 4 date labels. |
| `Sparkline(values:)` | A 1.5 pt price line inside a row. |
| `.chartDrawIn(trigger:, duration:, enabled:, interacting:)` | Every chart's entrance: it draws in from the leading edge, replaying when `trigger` (the series' range or account) changes. |
| `AllocationRing` / `MiniAllocationRing(fraction:, color:)` | A share of the portfolio: 44 pt with its label, or 24 pt in a row. |
| `ToolbarMenu { }` | The toolbar's More menu. |
| `ToolbarAddButton(title) { }` | The `+` create button. The title is what VoiceOver reads. |
| `SectionHeader(title:, subtitle:) { trailing }` | A heading over content outside a `List`. Rarely needed now. |
| `EmptyState`, `ErrorRow`, `LoadingState`, `.confirmAlert`, `TypedConfirmField`, `Toast` | These are unchanged; see their doc comments. |

Each component has a doc comment and a `#Preview`. Open the file in Xcode's canvas to see it in
light and dark mode.

## Example 1: a list root (Instances)

```swift
struct InstancesView: View {
    @Environment(AppServices.self) private var services
    @State private var model = InstancesModel()
    @State private var creating = false
    @State private var confirm: ConfirmRequest?

    var body: some View {
        List {
            // A filter goes in the list, not in an eyebrow block above it.
            Section {
                Picker("Show", selection: $model.filter) {
                    Text("All (\(model.all.count))").tag(InstanceFilter.all)
                    Text("User (\(model.user.count))").tag(InstanceFilter.user)
                    Text("AI (\(model.ai.count))").tag(InstanceFilter.ai)
                }
                .pickerStyle(.segmented)
                .listRowBackground(Color.clear)
                .listRowInsets(EdgeInsets())
            }

            Section {
                ForEach(model.visible) { inst in
                    NavigationLink(value: Route.instance(inst.id)) {
                        EntityRow(inst.name, subtitle: inst.subtitle, systemImage: "cpu", isPinned: inst.pinned) {
                            StatusDot(inst.running ? "Running" : "Stopped",
                                      color: inst.running ? DS.Palette.success : .secondary,
                                      pulsing: inst.running)
                        }
                    }
                    .swipeActions(edge: .leading) {
                        Button(inst.pinned ? "Unpin" : "Pin", systemImage: inst.pinned ? "pin.slash" : "pin") {
                            Task { await model.togglePin(inst) }
                        }
                        .tint(.orange)
                    }
                    .swipeActions(edge: .trailing) {
                        Button("Delete", systemImage: "trash", role: .destructive) {
                            confirm = model.deleteRequest(inst)   // the same confirmation as before
                        }
                    }
                    .contextMenu {
                        Button(inst.running ? "Stop" : "Start",
                               systemImage: inst.running ? "stop.fill" : "play.fill") { … }
                        Button("View Live", systemImage: "chart.xyaxis.line") {
                            services.router.push(.liveTrading(inst.id))
                        }
                        Divider()
                        Button("Delete", systemImage: "trash", role: .destructive) {
                            confirm = model.deleteRequest(inst)
                        }
                    }
                }
            }
        }
        .listStyle(.insetGrouped)
        .navigationTitle("Instances")                       // a tab root has a large title
        .toolbar {
            ToolbarItem(placement: .primaryAction) {
                ToolbarAddButton("New Instance") { creating = true }
            }
        }
        .refreshable { await model.refresh() }               // no refresh button
        .overlay {
            if model.visible.isEmpty, !model.isLoading {
                ContentUnavailableView("No Instances", systemImage: "cpu",
                                       description: Text("Create one with +."))
            }
        }
        .confirmAlert($confirm)
        .sheet(isPresented: $creating) { NewInstanceSheet() }
    }
}
```

For row identifiers, show a human name. Where you only have an ID, truncate it in the middle
and give the row a copy action in its context menu:

```swift
Text(inst.brokerageId)
    .font(.subheadline)
    .foregroundStyle(.secondary)
    .lineLimit(1)
    .truncationMode(.middle)
// in .contextMenu:
Button("Copy ID", systemImage: "doc.on.doc") { UIPasteboard.general.string = inst.brokerageId }
```

## Example 2: a detail screen (Backtest detail)

```swift
struct BacktestDetailView: View {
    let id: String
    @State private var model: BacktestDetailModel
    @State private var confirm: ConfirmRequest?
    @State private var rerunning = false

    var body: some View {
        List {
            // The hero and its chart are the only card-like content. Put them in the
            // first section, with the list's insets, and never full bleed.
            Section {
                VStack(alignment: .leading, spacing: DS.cardGroupSpacing) {
                    HeroValueHeader(
                        fmtPnl(model.pnl),
                        numericValue: model.pnl,
                        change: fmtPct(model.pnlPct),
                        direction: ChangeDirection(model.pnl),
                        status: model.dateRangeText            // "Jul 7 – Sep 18, 2026"
                    )
                    ScrubbableAreaChart(timestamps: model.times, values: model.equity,
                                        lineColor: ChangeDirection(model.pnl).color,
                                        height: 220, baseline: model.startValue)
                }
                .padding(.vertical, 4)
            }

            DSSection("Results") {
                StatGrid {
                    StatCell(label: "Portfolio", value: fmtMoney(model.endValue))
                    StatCell(label: "Trades", value: "\(model.trades)", footnote: "\(model.buys) buy · \(model.sells) sell")
                    StatCell(label: "Win rate", value: fmtPct(model.winRate))
                    StatCell(label: "Elapsed", value: model.elapsedText)
                }
            }

            DSSection("Run") {
                LabeledContent("Instance", value: model.instanceName)
                LabeledContent("Strategy", value: model.strategyName)
                LabeledContent("Granularity", value: "15 minutes")
            }

            DSSection("Logs") {
                NavigationLink("View Logs") { BacktestLogsView(id: id) }
            }
        }
        .listStyle(.insetGrouped)
        .navigationTitle("Backtest #\(id)")
        .navigationBarTitleDisplayMode(.inline)              // pushed: inline title, no echo below
        .toolbar {
            ToolbarItem(placement: .topBarTrailing) {
                ToolbarMenu {
                    Section {
                        Button("Rerun", systemImage: "arrow.clockwise") { confirm = model.rerunRequest() }
                            .disabled(rerunning)              // keep the double-submit guard
                        Button("Playback", systemImage: "play.rectangle") { … }
                    }
                    Section {
                        Button("Delete", systemImage: "trash", role: .destructive) { confirm = model.deleteRequest() }
                    }
                }
            }
        }
        .refreshable { await model.refresh() }
        .confirmAlert($confirm, isRunning: $rerunning)
    }
}
```

For a screen whose single primary action is Start or Stop, put that action in the toolbar beside
the menu:

```swift
ToolbarItem(placement: .primaryAction) {
    Button(running ? "Stop" : "Start") { … }
        .dsProminentButton()          // never a bare .borderedProminent
}
```

If the action is the whole point of the screen (approve a signal, link a brokerage), use a
full-width prominent button in the first section instead.

## Example 3: a form (Link brokerage)

```swift
NavigationStack {                       // sheets bring their own stack
    Form {
        Section {
            Picker("Brokerage", selection: $draft.type) { … }
            TextField("Account name", text: $draft.name)
        }
        DSSection("Keys", footer: "Keys are stored on your server, never on this device.") {
            SecureField("API key", text: $draft.key)
            SecureField("Secret", text: $draft.secret)
            Toggle("Paper trading", isOn: $draft.paper)
        }
        if let error = model.error {
            Section { ErrorRow(message: error) { model.clearError() } }
        }
        Section {
            InlineActionRow("Test Connection", systemImage: "bolt.horizontal", isBusy: model.testing) {
                Task { await model.test() }
            }
        }
    }
    .navigationTitle("Link Brokerage")
    .navigationBarTitleDisplayMode(.inline)
    .toolbar {
        ToolbarItem(placement: .cancellationAction) { Button("Cancel") { dismiss() } }
        ToolbarItem(placement: .confirmationAction) {
            Button("Link") { Task { await model.link() } }
                .disabled(!draft.isValid || model.saving)
        }
    }
}
.presentationDetents([.large])
```

Form rules:

- Use system controls (`TextField`, `SecureField`, `Picker`, `Toggle`, `DatePicker`,
  `LabeledContent`), and add no card wrappers.
- Put explanations in a section footer, not a paragraph above the form.
- Confirm and Cancel go in the toolbar.

## Toolbar convention

- **Create:** `ToolbarAddButton`, trailing.
- **Primary action:** at most one, `.dsProminentButton()`, trailing.
- **Everything else:** `ToolbarMenu`, grouped in `Section`s with destructive items last. Sort
  and page-size choices go in the menu as `Picker`s:

  ```swift
  ToolbarMenu("Sort and Page Size") {
      Picker("Sort", selection: $sort) { ForEach(StrategySort.allCases) { Text($0.label).tag($0) } }
      Picker("Per Page", selection: $perPage) { ForEach([10, 20, 50], id: \.self) { Text("\($0) per page").tag($0) } }
  }
  ```

- **No refresh buttons.** The only search button left is the Dashboard's `magnifyingglass`.
- **More symbol:** `ToolbarSymbol.more` is the bare `ellipsis`. iOS 26 already draws each toolbar
  item on a glass circle, so the result reads as the familiar circled ellipsis without a circle
  drawn inside another circle (`toolbars.md` › Actions).

## Status: dot or badge?

- **Running or stopped** (any state that changes on its own) uses a `StatusDot` with a word. The
  dot carries the colour and the word stays secondary.
- **A flag** (severity, Live versus Paper, Failed) uses one `StatusBadge`.
- **Who made it** (User or AI) is plain secondary text in the subtitle: "AI · Strategy 197".

## Charts

- Use `ScrubbableAreaChart` for any value-over-time chart. It already has the house style: a
  2 pt monotone line, a flat `DS.chartAreaOpacity` fill, one dotted baseline at the start value
  (or `baseline:`), and at most four `.caption2` date labels.
- Place it with margins: inside a `Card`, or in a list section with the list's insets.
- Follow it with the range `Picker(.segmented)`.
- Use `Sparkline` inside rows, framed about 60 × 24.
- Every chart draws itself in from the leading edge with `.chartDrawIn`, as `Sparkline` does:
  - It eases out over 0.9 s (0.65 s for a row-sized chart). Under Reduce Motion the chart
    shows at once.
  - Key `trigger` on what names the series: the range and account, or the range the data was
    loaded for. Never key it on the points, so polls leave the chart still.
  - Pass `interacting: selection != nil` on a scrubbable chart. A scrub during the draw-in
    uncovers the whole chart.
  - On a chart with axes, apply it inside `.chartPlotStyle`, so the axes and legend stay put.

## Empty, loading, error

- **A whole empty screen:** `ContentUnavailableView`, or `EmptyState` with an action.
- **Empty inside a section:** one `.secondary` row, such as "No open positions".
- **Loading:** a centred `ProgressView()` for a whole screen, or `.redacted(reason: .placeholder)`
  on rows that keep their real layout. Prefer that to `Skeleton`.
- **Errors:** `ErrorRow(message:onRetry:)`.

## The chat accessory

The chat's entry is the tab bar's bottom accessory: `ChatAccessoryView`
(`Features/Chatbot/Views/ChatAccessoryView.swift`), attached in `App/MainTabView.swift` together
with `chatbotPresenter()`, which hosts the chat sheet. Screens don't need to do anything for it.

- Content and the bottom safe area already sit above it.
- A screen's own floating control (Live Trading's Halt, the playback transport) must be laid out
  against the bottom safe area, with `.safeAreaInset(edge: .bottom)` or an overlay that respects
  the safe area. Never use a fixed offset from the screen edge.

## What R1 changed for every screen automatically

- `Card`'s default padding is now 16. Its corner radius stays 22.
- `SectionHeader` draws a `.headline` title and ignores `eyebrow:`. Drop that argument when you
  edit the call.
- `StatTile` draws as a `StatCell`, with no grey tile.
- `AppBadge` uses the `StatusBadge` capsule and sentence-cases its label.
- `StatusBadge` is text only; it shows a dot only when `pulsing` is true.
- `ScrubbableAreaChart` draws a dotted start-value baseline.
- `IconTile` corners are 30 % of the tile's size.
- `DashboardAllocationRing` and `DashboardMiniSpark` are typealiases of `AllocationRing` and
  `Sparkline`.

## Before you call a screen done

- [ ] No `.uppercased()`, `.textCase(.uppercase)` or `.tracking` on user-facing text, and no
      upper-case string literals except tickers and acronyms.
- [ ] No refresh toolbar buttons; `.refreshable` is present.
- [ ] No row of tinted capsule buttons; every old action is reachable by tap, swipe, context menu
      or toolbar.
- [ ] No card inside a card, and no grey tile inside a card.
- [ ] The inline title on a pushed screen is not repeated in the content.
- [ ] No gradients, glows, coloured shadows or blur.
- [ ] Every icon-only button has an accessibility label, and every target is 44 pt or more.
- [ ] Checked in light, dark and an accessibility text size.
