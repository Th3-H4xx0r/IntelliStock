import SwiftUI

/// The full-screen stock view — `StockScreen` in `stock_screen.dart` — as
/// an inset-grouped list, Stocks style: the ticker is the inline title; the
/// hero shows the live price, today's move and the company name, over a
/// gapless scrubbable chart and its ranges; then the user's position, the
/// bot's decisions, key statistics, About and recent orders, each a section.
struct StockView: View {
    let route: StockRoute

    @Environment(AppServices.self) private var services

    var body: some View {
        StockContent(route: route, services: services)
    }
}

private struct StockContent: View {
    let route: StockRoute
    let services: AppServices

    @State private var model: StockModel

    init(route: StockRoute, services: AppServices) {
        self.route = route
        self.services = services
        _model = State(initialValue: StockModel(
            symbol: route.symbol,
            brokerageId: route.brokerageId,
            client: { [unowned services] in services.apiClient }
        ))
    }

    var body: some View {
        let info = model.info ?? JSONObject()
        // True only while the very first info fetch is in flight.
        let infoLoading = model.info == nil
        let hasSummary = !stockInfoText(info, "summary").isEmpty
        List {
            Section {
                VStack(alignment: .leading, spacing: 0) {
                    header(info)
                    chartArea
                        .padding(.top, 20)
                    Picker("Range", selection: Binding(get: { model.range }, set: { model.setRange($0) })) {
                        ForEach(stockRanges, id: \.self) { Text($0).tag($0) }
                    }
                    .pickerStyle(.segmented)
                    .padding(.top, 12)
                }
                .padding(.vertical, 4)
                .listRowBackground(Color.clear)
                .listRowSeparator(.hidden)
            }
            if let position = route.position {
                positionSection(position)
            }
            botSection
            if infoLoading {
                statsSkeleton
            } else {
                statsSection(info)
            }
            if infoLoading {
                aboutSkeleton
            } else if hasSummary {
                aboutSection(info)
            }
            ordersSection
        }
        .listStyle(.insetGrouped)
        .contentMargins(.top, 0, for: .scrollContent)
        .navigationTitle(route.symbol)
        .navigationBarTitleDisplayMode(.inline)
        .refreshable { await model.refreshHistory() }
        .task(id: model.range) { await model.pollHistory(lifecycle: services.lifecycle) }
        .task { await model.loadDetails() }
    }

    // MARK: Hero

    @ViewBuilder
    private func header(_ info: JSONObject) -> some View {
        let name = stockInfoText(info, "name")
        let vals = model.series?.vals
        let ready = (vals?.count ?? 0) >= 2
        if ready, let vals {
            let first = vals[0]
            let last = vals[vals.count - 1]
            let idx = model.scrubIndex
            let shown = (idx != nil && idx! >= 0 && idx! < vals.count) ? vals[idx!] : last
            let dAbs = shown - first
            let dPct = first != 0 ? dAbs / first * 100 : 0
            HeroValueHeader(
                fmtMoney(shown),
                numericValue: shown,
                valueAnimation: idx == nil ? .easeOut(duration: 0.45) : nil,
                change: "\(fmtPnl(dAbs)) (\(fmtPct(dPct)))",
                direction: ChangeDirection(dAbs),
                status: name.isEmpty ? nil : name
            )
        } else if model.historyLoading {
            HeroValueHeader("$000.00", change: "+$0.00 (+0.00%)", status: name.isEmpty ? "Company name" : name)
                .redacted(reason: .placeholder)
                .accessibilityLabel("Loading")
        } else {
            HeroValueHeader("—", status: "No price data")
        }
    }

    // MARK: Chart

    @ViewBuilder
    private var chartArea: some View {
        if let series = model.series, series.vals.count >= 2 {
            let up = series.vals[series.vals.count - 1] >= series.vals[0]
            // A range clears the series and remounts the chart, which draws
            // the new one in from the left; 10 s polls redraw in place
            // without replaying it.
            ScrubbableAreaChart(
                timestamps: series.ts,
                values: series.vals,
                lineColor: up ? DS.Palette.success : DS.Palette.danger,
                height: 260,
                onScrub: { model.scrubIndex = $0 },
                animate: true,
                drawInKey: AnyHashable(model.range),
                indexed: true // evenly-spaced points → no weekend/overnight gaps
            )
            .id(model.range)
        } else if model.historyLoading {
            Skeleton(height: 260, radius: 12)
        } else {
            // Loaded but no usable series (illiquid/obscure tickers).
            let error = model.history.error != nil && model.series == nil
            Text(error ? "Couldn't load prices" : "No chart data available for \(route.symbol)")
                .font(.footnote)
                .foregroundStyle(error ? DS.Palette.danger : .secondary)
                .multilineTextAlignment(.center)
                .frame(maxWidth: .infinity)
                .frame(height: 260)
        }
    }

    // MARK: Your position

    private func positionSection(_ p: AccountPosition) -> some View {
        let color = ChangeDirection(p.unrealizedPnl).color
        let total = route.portfolioTotal
        let frac: Double? = (total ?? 0) > 0 ? p.marketValue / total! : nil
        return Section("Your position") {
            HStack(spacing: 16) {
                if let frac {
                    AllocationRing(fraction: frac, color: color, size: 46, lineWidth: 5, labelColor: .primary)
                }
                StatGrid {
                    StatCell(label: "Total P&L", value: fmtPnl(p.unrealizedPnl), valueColor: color, footnote: fmtPct(p.unrealizedPnlPct))
                    StatCell(label: "Value", value: fmtMoney(p.marketValue))
                }
            }
            .padding(.vertical, 4)
            Text("\(DashboardFormat.qtyNumber(p.qty)) shares · avg \(fmtMoney(p.avgEntryPrice))")
                .font(.subheadline)
                .foregroundStyle(.secondary)
        }
    }

    // MARK: Bot activity

    private var botSection: some View {
        Section("Bot activity") {
            if route.brokerageId == nil {
                emptyRow("No linked brokerage")
            } else if let events = model.botEvents {
                if events.isEmpty {
                    emptyRow("No bot trades logged yet for \(route.symbol)")
                } else {
                    ForEach(Array(events.enumerated()), id: \.offset) { _, e in
                        StockBotEventRow(event: e)
                    }
                }
            } else {
                DashboardPlaceholderRows(count: 2)
            }
        }
    }

    private func emptyRow(_ msg: String) -> some View {
        Text(msg).foregroundStyle(.secondary)
    }

    // MARK: Key statistics

    @ViewBuilder
    private func statsSection(_ info: JSONObject) -> some View {
        let cells = stockStatCells(info: info, series: model.series, range: model.range)
        if !cells.isEmpty {
            Section("Key statistics") {
                StatGrid(columns: 3) {
                    ForEach(Array(cells.enumerated()), id: \.offset) { _, cell in
                        StatCell(label: cell.label, value: cell.value)
                    }
                }
                .padding(.vertical, 6)
            }
        }
    }

    private var statsSkeleton: some View {
        Section("Key statistics") {
            StatGrid(columns: 3) {
                ForEach(0..<6, id: \.self) { _ in
                    StatCell(label: "Prev close", value: "$000.00")
                }
            }
            .padding(.vertical, 6)
            .redacted(reason: .placeholder)
            .accessibilityHidden(true)
        }
    }

    // MARK: About

    private func aboutSection(_ info: JSONObject) -> some View {
        let tags = [stockInfoText(info, "sector"), stockInfoText(info, "industry")].filter { !$0.isEmpty }
        return Section("About") {
            VStack(alignment: .leading, spacing: 12) {
                if !tags.isEmpty {
                    DashboardFlowLayout(spacing: 8) {
                        ForEach(tags, id: \.self) { t in
                            Text(t)
                                .font(.footnote.weight(.semibold))
                                .foregroundStyle(.secondary)
                                .padding(.horizontal, 10)
                                .padding(.vertical, 5)
                                .background(Color(uiColor: .tertiarySystemFill), in: .rect(cornerRadius: 8, style: .continuous))
                        }
                    }
                }
                Text(stockInfoText(info, "summary"))
                    .font(.body)
                    .lineSpacing(3)
                    .lineLimit(10)
            }
            .padding(.vertical, 6)
        }
    }

    private var aboutSkeleton: some View {
        Section("About") {
            Text("A placeholder paragraph that holds the shape of the company summary while it loads from the server.")
                .redacted(reason: .placeholder)
                .accessibilityHidden(true)
        }
    }

    // MARK: Order history

    private var ordersSection: some View {
        Section("Order history") {
            if route.brokerageId == nil {
                emptyRow("No linked brokerage")
            } else if let orders = model.orders {
                if orders.isEmpty {
                    emptyRow("No recent orders for \(route.symbol)")
                } else {
                    ForEach(Array(orders.enumerated()), id: \.offset) { _, t in
                        StockOrderRow(trade: t)
                    }
                }
            } else {
                DashboardPlaceholderRows(count: 3)
            }
        }
    }
}

/// One bot buy/sell for the symbol (`_BotEventRow`): the side and strategy,
/// when, the reason, who backed it, and the price.
private struct StockBotEventRow: View {
    let event: BotTradeEvent

    var body: some View {
        let color = event.isBuy ? DS.Palette.success : DS.Palette.danger
        let reason = event.reason.trimmingCharacters(in: .whitespacesAndNewlines)
        let backers = event.backers
        VStack(alignment: .leading, spacing: 5) {
            HStack(spacing: 8) {
                Text(event.side.capitalized)
                    .dsBadge(color)
                Text(event.title)
                    .font(.headline)
                    .lineLimit(1)
                    .frame(maxWidth: .infinity, alignment: .leading)
                if let ts = event.ts {
                    Text(fmtRelative(ts))
                        .font(.footnote)
                        .foregroundStyle(.secondary)
                }
            }
            if !reason.isEmpty {
                Text(reason)
                    .font(.subheadline)
                    .foregroundStyle(.secondary)
                    .lineLimit(4)
            }
            if !backers.isEmpty {
                Text("Backed by \(backers.joined(separator: ", "))")
                    .font(.footnote)
                    .foregroundStyle(.secondary)
                    .lineLimit(1)
            }
            if event.price != nil || event.overrideApplied {
                HStack(spacing: 10) {
                    if let price = event.price {
                        Text("@ \(fmtMoney(price))")
                            .font(.footnote.monospacedDigit())
                            .foregroundStyle(.secondary)
                    }
                    if event.overrideApplied {
                        Text("Overridden")
                            .dsBadge(DS.Palette.accent)
                    }
                }
            }
        }
        .padding(.vertical, 4)
        .accessibilityElement(children: .combine)
    }
}

/// One recent fill (`_OrderRow`): the side, quantity at price, when, and the
/// fill's total.
private struct StockOrderRow: View {
    let trade: Trade

    var body: some View {
        let isBuy = trade.side.lowercased() == "buy"
        EntityRow(
            "\(DashboardFormat.qtyNumber(trade.qty)) @ \(fmtMoney(trade.price))",
            subtitle: fmtDateTime(trade.ts)
        ) {
            Text(trade.side.capitalized)
                .dsBadge(isBuy ? DS.Palette.success : DS.Palette.danger)
                .frame(minWidth: 40, alignment: .leading)
        } trailing: {
            EntityRowValue(fmtMoney(trade.price * trade.qty))
        }
    }
}
