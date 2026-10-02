import SwiftUI

/// The full-screen stock view — `StockScreen` in `stock_screen.dart`: name
/// and live price, a gapless scrubbable chart with ranges, the user's
/// position, the bot's decisions, key statistics, About and recent orders.
/// The violet crown and round back button become the system navigation bar
/// on the plain grouped background.
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
        ScrollView {
            VStack(alignment: .leading, spacing: 0) {
                header(info)
                chartArea
                    .padding(.top, 18)
                Picker("Range", selection: Binding(get: { model.range }, set: { model.setRange($0) })) {
                    ForEach(stockRanges, id: \.self) { Text($0).tag($0) }
                }
                .pickerStyle(.segmented)
                .padding(.top, 10)
                if let position = route.position {
                    positionSection(position)
                        .padding(.top, 26)
                }
                botSection
                    .padding(.top, 28)
                Group {
                    if infoLoading {
                        statsSkeleton
                    } else {
                        statsSection(info)
                    }
                }
                .padding(.top, 28)
                if infoLoading {
                    aboutSkeleton.padding(.top, 30)
                } else if hasSummary {
                    aboutSection(info).padding(.top, 30)
                }
                ordersSection
                    .padding(.top, 30)
            }
            .padding(.horizontal, 20)
            .padding(.top, 4)
            .padding(.bottom, 40)
        }
        .background(DS.Surface.canvas)
        .navigationTitle(route.symbol)
        .navigationBarTitleDisplayMode(.inline)
        .task(id: model.range) { await model.pollHistory(lifecycle: services.lifecycle) }
        .task { await model.loadDetails() }
    }

    // MARK: Header

    private func header(_ info: JSONObject) -> some View {
        let name = stockInfoText(info, "name")
        let vals = model.series?.vals
        let ready = (vals?.count ?? 0) >= 2
        let first = ready ? vals![0] : 0
        let last = ready ? vals![vals!.count - 1] : 0
        let idx = model.scrubIndex
        let shown = (ready && idx != nil && idx! >= 0 && idx! < vals!.count) ? vals![idx!] : last
        let dAbs = shown - first
        let dPct = first != 0 ? dAbs / first * 100 : 0
        let color = dAbs >= 0 ? DS.Palette.success : DS.Palette.danger
        return HStack(alignment: .top, spacing: 12) {
            VStack(alignment: .leading, spacing: 4) {
                Text(name.isEmpty ? route.symbol : name)
                    .font(.title3.bold())
                    .lineLimit(2)
                Text(route.symbol)
                    .font(.caption.weight(.semibold))
                    .tracking(0.5)
                    .foregroundStyle(.secondary)
            }
            .frame(maxWidth: .infinity, alignment: .leading)
            VStack(alignment: .trailing, spacing: 4) {
                if ready {
                    Text(fmtMoney(shown))
                        .font(.title.weight(.heavy).monospacedDigit())
                        .contentTransition(.numericText(value: shown))
                        .animation(.easeOut(duration: 0.45), value: shown)
                        .lineLimit(1)
                        .minimumScaleFactor(0.6)
                    Text("\(dAbs >= 0 ? "▲" : "▼") \(fmtPnl(dAbs))  \(fmtPct(dPct))")
                        .font(.subheadline.weight(.bold).monospacedDigit())
                        .foregroundStyle(color)
                } else if model.historyLoading {
                    Skeleton(width: 120, height: 28, radius: 8)
                    Skeleton(width: 100, height: 16, radius: 5)
                } else {
                    Text("—")
                        .font(.title.weight(.heavy))
                    Text("No price data")
                        .font(.caption)
                        .foregroundStyle(.secondary)
                }
            }
        }
        .accessibilityElement(children: .combine)
    }

    // MARK: Chart

    @ViewBuilder
    private var chartArea: some View {
        if let series = model.series, series.vals.count >= 2 {
            let up = series.vals[series.vals.count - 1] >= series.vals[0]
            // A range remounts the chart, which animates once; 10 s polls
            // redraw in place without replaying the grow-in.
            ScrubbableAreaChart(
                timestamps: series.ts,
                values: series.vals,
                lineColor: up ? DS.Palette.success : DS.Palette.danger,
                height: 280,
                onScrub: { model.scrubIndex = $0 },
                animate: true,
                indexed: true // evenly-spaced points → no weekend/overnight gaps
            )
            .id(model.range)
        } else if model.historyLoading {
            Skeleton(height: 280, radius: 16)
                .padding(.vertical, 6)
        } else {
            // Loaded but no usable series (illiquid/obscure tickers).
            let error = model.history.error != nil && model.series == nil
            Text(error ? "Couldn't load prices" : "No chart data available for \(route.symbol)")
                .font(.caption)
                .foregroundStyle(error ? DS.Palette.danger : .secondary)
                .multilineTextAlignment(.center)
                .frame(maxWidth: .infinity)
                .frame(height: 280)
        }
    }

    // MARK: Sections

    private func sectionTitle(_ s: String) -> some View {
        Text(s.uppercased())
            .font(.footnote.weight(.bold))
            .tracking(1.0)
            .foregroundStyle(.secondary)
            .accessibilityAddTraits(.isHeader)
    }

    private func positionSection(_ p: AccountPosition) -> some View {
        let up = p.unrealizedPnl >= 0
        let color = up ? DS.Palette.success : DS.Palette.danger
        let total = route.portfolioTotal
        let frac: Double? = (total ?? 0) > 0 ? p.marketValue / total! : nil
        return VStack(alignment: .leading, spacing: 12) {
            sectionTitle("Your position")
            HStack(spacing: 14) {
                if let frac {
                    DashboardAllocationRing(fraction: frac, color: color, size: 46, lineWidth: 5, labelColor: .primary)
                }
                VStack(alignment: .leading, spacing: 3) {
                    Text("TOTAL P&L")
                        .font(.caption2)
                        .foregroundStyle(.secondary)
                    HStack(alignment: .firstTextBaseline, spacing: 6) {
                        Text(fmtPnl(p.unrealizedPnl))
                            .font(.title3.weight(.semibold).monospacedDigit())
                        Text(fmtPct(p.unrealizedPnlPct))
                            .font(.caption.weight(.bold).monospacedDigit())
                    }
                    .foregroundStyle(color)
                }
                .frame(maxWidth: .infinity, alignment: .leading)
                VStack(alignment: .trailing, spacing: 3) {
                    Text("VALUE")
                        .font(.caption2)
                        .foregroundStyle(.secondary)
                    Text(fmtMoney(p.marketValue))
                        .font(.subheadline.weight(.bold).monospacedDigit())
                }
            }
            Text("\(DashboardFormat.qtyNumber(p.qty)) shares · avg \(fmtMoney(p.avgEntryPrice))")
                .font(.caption)
                .foregroundStyle(.secondary)
        }
    }

    private func emptyLine(_ msg: String) -> some View {
        Text(msg)
            .font(.caption)
            .foregroundStyle(.secondary)
            .padding(.vertical, 12)
    }

    private var botSection: some View {
        VStack(alignment: .leading, spacing: 8) {
            sectionTitle("Bot activity")
            if route.brokerageId == nil {
                emptyLine("No linked brokerage")
            } else if let events = model.botEvents {
                if events.isEmpty {
                    emptyLine("No bot trades logged yet for \(route.symbol)")
                } else {
                    VStack(spacing: 0) {
                        ForEach(Array(events.enumerated()), id: \.offset) { i, e in
                            if i > 0 { Divider() }
                            StockBotEventRow(event: e)
                        }
                    }
                }
            } else {
                ForEach(0..<2, id: \.self) { _ in
                    VStack(alignment: .leading, spacing: 6) {
                        Skeleton(width: 150, height: 13, radius: 5)
                        Skeleton(width: 90, height: 10, radius: 4)
                        Skeleton(height: 10, radius: 4)
                    }
                    .padding(.vertical, 10)
                }
            }
        }
    }

    private func statsSection(_ info: JSONObject) -> some View {
        let cells = stockStatCells(info: info, series: model.series, range: model.range)
        return Group {
            if !cells.isEmpty {
                VStack(alignment: .leading, spacing: 16) {
                    sectionTitle("Key statistics")
                    Grid(alignment: .topLeading, horizontalSpacing: 14, verticalSpacing: 18) {
                        ForEach(Array(stride(from: 0, to: cells.count, by: 3)), id: \.self) { i in
                            GridRow {
                                ForEach(i..<(i + 3), id: \.self) { j in
                                    if j < cells.count {
                                        statCell(cells[j])
                                    } else {
                                        Color.clear.gridCellUnsizedAxes([.horizontal, .vertical])
                                    }
                                }
                            }
                        }
                    }
                }
                .padding(.top, 4)
            }
        }
    }

    private func statCell(_ cell: (label: String, value: String)) -> some View {
        VStack(alignment: .leading, spacing: 4) {
            Text(cell.label.uppercased())
                .font(.caption2)
                .tracking(0.2)
                .foregroundStyle(.secondary)
            Text(cell.value)
                .font(.callout.weight(.bold).monospacedDigit())
        }
        .lineLimit(1)
        .frame(maxWidth: .infinity, alignment: .leading)
        .accessibilityElement(children: .combine)
    }

    private var statsSkeleton: some View {
        VStack(alignment: .leading, spacing: 18) {
            sectionTitle("Key statistics")
            ForEach(0..<3, id: \.self) { _ in
                HStack(spacing: 14) {
                    ForEach(0..<3, id: \.self) { _ in
                        VStack(alignment: .leading, spacing: 7) {
                            Skeleton(width: 46, height: 9, radius: 4)
                            Skeleton(width: 62, height: 15, radius: 5)
                        }
                        .frame(maxWidth: .infinity, alignment: .leading)
                    }
                }
            }
        }
        .padding(.top, 4)
    }

    private func aboutSection(_ info: JSONObject) -> some View {
        let tags = [stockInfoText(info, "sector"), stockInfoText(info, "industry")].filter { !$0.isEmpty }
        return VStack(alignment: .leading, spacing: 0) {
            sectionTitle("About")
            if !tags.isEmpty {
                DashboardFlowLayout(spacing: 8) {
                    ForEach(tags, id: \.self) { t in
                        Text(t)
                            .font(.caption.weight(.semibold))
                            .foregroundStyle(.tint)
                            .padding(.horizontal, 10)
                            .padding(.vertical, 5)
                            .background(DS.Palette.accent.opacity(DS.tintFill), in: .rect(cornerRadius: 7, style: .continuous))
                    }
                }
                .padding(.top, 12)
            }
            Text(stockInfoText(info, "summary"))
                .font(.body)
                .lineSpacing(4)
                .lineLimit(10)
                .padding(.top, 14)
        }
        .padding(.top, 4)
    }

    private var aboutSkeleton: some View {
        VStack(alignment: .leading, spacing: 9) {
            sectionTitle("About")
                .padding(.bottom, 5)
            Skeleton(height: 12, radius: 5)
            Skeleton(height: 12, radius: 5)
            Skeleton(width: 220, height: 12, radius: 5)
        }
        .padding(.top, 4)
    }

    private var ordersSection: some View {
        VStack(alignment: .leading, spacing: 6) {
            sectionTitle("Order history")
            if route.brokerageId == nil {
                emptyLine("No linked brokerage")
            } else if let orders = model.orders {
                if orders.isEmpty {
                    emptyLine("No recent orders for \(route.symbol)")
                } else {
                    VStack(spacing: 0) {
                        ForEach(Array(orders.enumerated()), id: \.offset) { i, t in
                            if i > 0 { Divider() }
                            StockOrderRow(trade: t)
                        }
                    }
                }
            } else {
                ForEach(0..<3, id: \.self) { _ in
                    HStack(spacing: 10) {
                        Skeleton(width: 40, height: 18, radius: 5)
                        VStack(alignment: .leading, spacing: 5) {
                            Skeleton(width: 110, height: 13, radius: 5)
                            Skeleton(width: 78, height: 10, radius: 4)
                        }
                        Spacer()
                        Skeleton(width: 54, height: 13, radius: 5)
                    }
                    .padding(.vertical, 8)
                }
            }
        }
        .padding(.top, 4)
    }
}

/// One bot buy/sell for the symbol (`_BotEventRow`).
private struct StockBotEventRow: View {
    let event: BotTradeEvent

    var body: some View {
        let color = event.isBuy ? DS.Palette.success : DS.Palette.danger
        let reason = event.reason.trimmingCharacters(in: .whitespacesAndNewlines)
        let backers = event.backers
        VStack(alignment: .leading, spacing: 5) {
            HStack(spacing: 8) {
                DashboardTintTag(text: event.side.uppercased(), color: color)
                Text(event.title)
                    .font(.subheadline.weight(.semibold))
                    .lineLimit(1)
                    .frame(maxWidth: .infinity, alignment: .leading)
                if let ts = event.ts {
                    Text(fmtRelative(ts))
                        .font(.caption2)
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
                    .font(.caption.weight(.semibold))
                    .foregroundStyle(.tint)
                    .lineLimit(1)
            }
            if event.price != nil || event.overrideApplied {
                HStack(spacing: 10) {
                    if let price = event.price {
                        Text("@ \(fmtMoney(price))")
                            .font(.caption)
                            .foregroundStyle(.secondary)
                    }
                    if event.overrideApplied {
                        DashboardTintTag(text: "OVERRIDDEN", color: DS.Palette.accent, weight: .bold)
                    }
                }
                .padding(.top, 1)
            }
        }
        .padding(.vertical, 10)
        .accessibilityElement(children: .combine)
    }
}

/// One recent fill (`_OrderRow`).
private struct StockOrderRow: View {
    let trade: Trade

    var body: some View {
        let isBuy = trade.side.lowercased() == "buy"
        HStack(spacing: 10) {
            DashboardTintTag(text: trade.side.uppercased(), color: isBuy ? DS.Palette.success : DS.Palette.danger)
            VStack(alignment: .leading, spacing: 2) {
                Text("\(DashboardFormat.qtyNumber(trade.qty)) @ \(fmtMoney(trade.price))")
                    .font(.subheadline.weight(.semibold).monospacedDigit())
                Text(fmtDateTime(trade.ts))
                    .font(.caption2)
                    .foregroundStyle(.secondary)
            }
            .frame(maxWidth: .infinity, alignment: .leading)
            Text(fmtMoney(trade.price * trade.qty))
                .font(.subheadline.monospacedDigit())
        }
        .padding(.vertical, 8)
        .accessibilityElement(children: .combine)
    }
}
