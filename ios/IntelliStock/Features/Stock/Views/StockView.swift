import SwiftUI

/// The full-screen stock view — the web's `StockView.vue` — as a
/// `.dsList()`: the ticker is the (large) title, the company and exchange
/// under it; the price, its move and the range's caption over a gapless
/// scrubbable chart with price gridlines and the range control; then the
/// user's position, the symbol's open orders (each cancelable), the bot's
/// decisions, key statistics, About and the order history, each a section.
/// For an equity, Sell and Buy sit at the bottom and open the trade ticket
/// (`TradeTicketSheet`).
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
    /// Open orders for the symbol, cancels, and the position after a trade.
    @State private var trade: StockTradeModel
    @State private var ticket: TradeTicketRequest?
    @State private var aboutOpen = false

    init(route: StockRoute, services: AppServices) {
        self.route = route
        self.services = services
        _model = State(initialValue: StockModel(
            symbol: route.symbol,
            brokerageId: route.brokerageId,
            client: { [unowned services] in services.apiClient }
        ))
        _trade = State(initialValue: StockTradeModel(
            symbol: route.symbol,
            backend: { [unowned services] in TradeRepository(client: services.apiClient) }
        ))
    }

    /// The accounts the app can trade (Alpaca).
    private var tradableAccounts: [BrokerageAccount] {
        TradeTicket.tradableAccounts(services.dashboard.brokeragesValue ?? [])
    }

    /// Buy and Sell show for an equity, once a tradable account is known.
    private var canTrade: Bool {
        TradeTicket.isTradableSymbol(route.symbol) && !tradableAccounts.isEmpty
    }

    /// The position to show: re-read after a trade on this screen's account
    /// (or on any account when the screen came without one), else the
    /// dashboard's snapshot.
    private var shownPosition: (position: AccountPosition, total: Double?)? {
        if let live = trade.livePosition, route.brokerageId == nil || live.accountId == route.brokerageId {
            return live.position.map { ($0, live.total) }
        }
        return route.position.map { ($0, route.portfolioTotal) }
    }

    /// Opens a ticket on `side`, starting on the dashboard's account when it
    /// is tradable.
    private func openTicket(_ side: TradeSide) {
        let accounts = tradableAccounts
        let selected = DashboardFormat.resolveSelected(services.dashboard.brokeragesValue ?? [], services.selectedAccount.selectedId)
        guard let accountId = TradeTicket.defaultAccountId(accounts, dashboardSelected: selected?.id, route: route.brokerageId) else { return }
        let model = model
        let fallback = route.position?.lastPrice
        let services = services
        ticket = TradeTicketRequest(model: TradeTicketModel(
            symbol: route.symbol,
            side: side,
            accounts: accounts,
            accountId: accountId,
            lastPrice: { model.series?.vals.last ?? fallback },
            backend: { [unowned services] in TradeRepository(client: services.apiClient) }
        ))
    }

    var body: some View {
        let info = model.info ?? JSONObject()
        // True only while the very first info fetch is in flight.
        let infoLoading = model.info == nil
        let hasSummary = !stockInfoText(info, "summary").isEmpty
        List {
            Section {
                VStack(alignment: .leading, spacing: 24) {
                    // The web header's subtitle: the company and exchange.
                    Text(subtitle(info))
                        .font(DS.Font.pageSub)
                        .foregroundStyle(DS.Text.muted)
                        .lineLimit(1)
                        .redacted(reason: infoLoading ? .placeholder : [])
                    header(info)
                }
                .dsClearRow(top: 0)
                VStack(alignment: .leading, spacing: 0) {
                    chartArea
                    DSSegmentedControl("Chart range", selection: Binding(get: { model.range }, set: { model.setRange($0) }), options: stockRanges, fullWidth: true) { $0 }
                        .padding(.top, 16)
                    DSDivider()
                        .padding(.top, 16)
                }
                .dsClearRow(top: 24)
            }
            if let shown = shownPosition {
                positionSection(shown.position, total: shown.total)
            }
            StockOpenOrdersSection(model: trade, showsAccount: tradableAccounts.count > 1)
            if let option = parseOccSymbol(route.symbol) {
                underlyingSection(option)
            } else if !AnalysisFormat.isCryptoSymbol(route.symbol) {
                // Crypto has no sector or market split to explain against.
                analysisLink
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
        .dsList()
        .navigationTitle(stockDisplayTitle(route.symbol))
        .navigationBarTitleDisplayMode(.large)
        .refreshable {
            await model.refreshHistory()
            await trade.reloadOpenOrders()
        }
        .task(id: model.range) { await model.pollHistory(lifecycle: services.lifecycle) }
        .task { await model.loadDetails() }
        // Reached from search before the dashboard loaded its accounts.
        .task {
            if services.dashboard.brokeragesValue == nil { await services.dashboard.loadBrokerages() }
        }
        .task(id: tradableAccounts.map(\.id)) {
            await trade.loadOpenOrders(tradableAccounts)
        }
        .safeAreaInset(edge: .bottom) {
            if canTrade {
                TradeActionBar(onSell: { openTicket(.sell) }, onBuy: { openTicket(.buy) })
            }
        }
        .sheet(item: $ticket) { request in
            TradeTicketSheet(model: request.model) { accountId in
                let trade = trade
                let routeAccount = route.brokerageId
                Task { await trade.refreshAfterTrade(accountId: accountId, routeAccountId: routeAccount) }
            }
            .presentationDetents([.large])
        }
        .tradeCancelAlert($trade.cancelPrompt) { prompt in
            let trade = trade
            Task { await trade.cancel(brokerageId: prompt.brokerageId, orderId: prompt.order.id) }
        }
        .toast($trade.toast)
    }

    // MARK: Hero

    /// "Apple Inc. · NasdaqGS"; an option's expiry; "Stock" without a name.
    private func subtitle(_ info: JSONObject) -> String {
        if parseOccSymbol(route.symbol) != nil {
            return stockStatusLine(symbol: route.symbol, name: "")
        }
        let parts = [stockInfoText(info, "name"), stockInfoText(info, "exchange")].filter { !$0.isEmpty }
        return parts.isEmpty ? (model.info == nil ? "Company name · Exchange" : "Stock") : parts.joined(separator: " · ")
    }

    @ViewBuilder
    private func header(_ info: JSONObject) -> some View {
        let vals = model.series?.vals
        let ready = (vals?.count ?? 0) >= 2
        if ready, let vals {
            let first = vals[0]
            let last = vals[vals.count - 1]
            let idx = model.scrubIndex
            let shown = (idx != nil && idx! >= 0 && idx! < vals.count) ? vals[idx!] : last
            let dAbs = shown - first
            let dPct = first != 0 ? dAbs / first * 100 : 0
            let ts = model.series?.ts
            let caption = idx.flatMap { i in ts.flatMap { i < $0.count ? dashboardScrubCaption($0[i], range: model.range) : nil } }
                ?? dashboardRangeCaption(model.range)
            HeroValueHeader(
                fmtMoney(shown),
                numericValue: shown,
                valueAnimation: idx == nil ? DSMotion.ease(0.45) : nil,
                change: "\(fmtPnl(dAbs)) (\(fmtPct(dPct)))",
                direction: ChangeDirection(dAbs),
                changeLabel: caption
            )
        } else if model.historyLoading {
            VStack(alignment: .leading, spacing: 12) {
                Skeleton(width: 192, height: 44, radius: DS.Radius.small)
                Skeleton(width: 160, height: 16)
            }
            .accessibilityElement(children: .ignore)
            .accessibilityLabel("Loading")
        } else {
            VStack(alignment: .leading, spacing: 8) {
                Text("—")
                    .font(DS.Font.hero)
                    .foregroundStyle(DS.Text.faint)
                Text("No price data")
                    .font(DS.Font.body)
                    .foregroundStyle(DS.Text.muted)
            }
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
                lineColor: up ? DS.Palette.gain : DS.Palette.loss,
                height: 280,
                onScrub: { model.scrubIndex = $0 },
                animate: true,
                drawInKey: AnyHashable(model.range),
                indexed: true, // evenly-spaced points → no weekend/overnight gaps
                showsYAxis: true
            )
            .id(model.range)
        } else if model.historyLoading {
            Skeleton(height: 280, radius: DS.Radius.control)
        } else {
            // Loaded but no usable series (illiquid/obscure tickers).
            let error = model.history.error != nil && model.series == nil
            Text(error ? "Couldn't load prices" : "No chart data for \(route.symbol)")
                .font(DS.Font.body)
                .foregroundStyle(DS.Text.muted)
                .multilineTextAlignment(.center)
                .frame(maxWidth: .infinity)
                .frame(height: 280)
        }
    }

    // MARK: Your position

    /// The account the screen's position is in, for the section's subtitle.
    private var positionAccount: BrokerageAccount? {
        let id = trade.livePosition?.accountId ?? route.brokerageId
        return (services.dashboard.brokeragesValue ?? []).first { $0.id == id }
    }

    /// The web's "Your position" card: value with its share of the account,
    /// total P&L since bought, shares and average cost.
    private func positionSection(_ p: AccountPosition, total: Double?) -> some View {
        let frac: Double? = (total ?? 0) > 0 ? p.marketValue / total! : nil
        let option = isOccOptionSymbol(route.symbol)
        let qty = DashboardFormat.qtyNumber(abs(p.qty))
        return DSSection(
            "Your position",
            subtitle: positionAccount.map { "\(DashboardFormat.accountName($0)) · \(TradeTicket.modeText($0))" },
            style: .plain
        ) {
            Card {
                StatGrid(columns: 2) {
                    StatCell(label: "Value", footnote: frac.map { "\(dartToStringAsFixed($0 * 100, 1))% of the account" }) {
                        Text(fmtMoney(p.marketValue)).font(DS.Font.figureLarge)
                    }
                    StatCell(label: "Total P&L", footnote: "\(fmtPct(p.unrealizedPnlPct)) since bought") {
                        Text(fmtPnl(p.unrealizedPnl))
                            .font(DS.Font.figureLarge)
                            .foregroundStyle(dashboardChangeColor(p.unrealizedPnl))
                    }
                    StatCell(label: option ? "Contracts" : "Shares", value: p.qty < 0 ? "\(qty) short" : qty)
                    StatCell(label: "Average cost", value: fmtMoney(p.avgEntryPrice))
                }
            }
            .dsClearRow()
        }
    }

    // MARK: Underlying (options)

    /// For an option: the underlying's chart with the strike dashed across it
    /// and, for a sold option, the assignment zone in red; above it, what the
    /// position means.
    private func underlyingSection(_ option: OccContract) -> some View {
        let qty = route.position?.qty
        let put = option.optionType.lowercased() == "put"
        let riskBelow: Bool? = (qty ?? 0) < 0 ? put : nil
        return DSSection(option.underlying, style: .plain) {
            Card {
                VStack(alignment: .leading, spacing: 12) {
                    if let text = stockOptionPlanText(route.symbol, qty: qty, premium: route.position?.avgEntryPrice) {
                        Text(text)
                            .font(DS.Font.body)
                            .foregroundStyle(DS.Text.secondary)
                            .fixedSize(horizontal: false, vertical: true)
                    }
                    SwingOrderChart(symbol: option.underlying, strike: option.strike, riskBelow: riskBelow)
                }
            }
            .dsClearRow()
        }
    }

    // MARK: AI analysis

    /// Opens AI analysis on this symbol: which days stood out and why.
    private var analysisLink: some View {
        DSSection {
            NavigationLink(value: Route.analysis(route.symbol)) {
                Label {
                    VStack(alignment: .leading, spacing: 2) {
                        Text("Why it moved")
                            .font(DS.Font.body)
                            .foregroundStyle(DS.Text.primary)
                        Text("AI analysis of the days \(route.symbol) stood out")
                            .font(DS.Font.caption)
                            .foregroundStyle(DS.Text.muted)
                    }
                } icon: {
                    SettingsIconTile(systemImage: "text.magnifyingglass")
                }
            }
        }
    }

    // MARK: Bot activity

    private var botSection: some View {
        DSSection("Bot activity", subtitle: "Why the bot bought or sold \(stockDisplayTitle(route.symbol))") {
            if route.brokerageId == nil {
                emptyRow("No linked brokerage")
            } else if let events = model.botEvents {
                if events.isEmpty {
                    emptyRow("No bot trades logged yet for \(stockDisplayTitle(route.symbol))")
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

    /// The web's empty card line: one centred sentence in zinc-400.
    private func emptyRow(_ msg: String) -> some View {
        Text(msg)
            .font(DS.Font.body)
            .foregroundStyle(DS.Text.secondary)
            .multilineTextAlignment(.center)
            .frame(maxWidth: .infinity)
            .padding(.vertical, 16)
    }

    // MARK: Key statistics

    @ViewBuilder
    private func statsSection(_ info: JSONObject) -> some View {
        let cells = stockStatCells(info: info, series: model.series, range: model.range, symbol: route.symbol)
        if !cells.isEmpty {
            // The web's key-value rows, 44 tall.
            DSSection("Key statistics", rowMinHeight: 44) {
                ForEach(Array(cells.enumerated()), id: \.offset) { _, cell in
                    DSKeyValueRow(cell.label, cell.value)
                }
            }
        }
    }

    private var statsSkeleton: some View {
        DSSection("Key statistics", rowMinHeight: 44) {
            ForEach(0..<6, id: \.self) { _ in
                HStack {
                    Skeleton(width: 80, height: 12)
                    Spacer()
                    Skeleton(width: 64, height: 12)
                }
                .accessibilityHidden(true)
            }
        }
    }

    // MARK: About

    private func aboutSection(_ info: JSONObject) -> some View {
        let tags = [stockInfoText(info, "sector"), stockInfoText(info, "industry")].filter { !$0.isEmpty }
        let summary = stockInfoText(info, "summary")
        return DSSection("About", style: .plain) {
            Card {
                VStack(alignment: .leading, spacing: 0) {
                    if !tags.isEmpty {
                        Text(tags.joined(separator: " · "))
                            .font(DS.Font.caption)
                            .foregroundStyle(DS.Text.muted)
                            .padding(.bottom, 12)
                    }
                    Text(summary)
                        .font(DS.Font.body)
                        .foregroundStyle(DS.Text.label)
                        .lineSpacing(4)
                        .lineLimit(aboutOpen ? nil : 6)
                    if summary.count > 360 {
                        Button(aboutOpen ? "Show less" : "Show more") {
                            withAnimation(DSMotion.standard) { aboutOpen.toggle() }
                        }
                        .font(DS.Font.body)
                        .foregroundStyle(DS.Text.secondary)
                        .buttonStyle(.plain)
                        .padding(.top, 8)
                    }
                }
            }
            .dsClearRow()
        }
    }

    private var aboutSkeleton: some View {
        DSSection("About", style: .plain) {
            Card {
                VStack(alignment: .leading, spacing: 8) {
                    Skeleton(height: 12)
                    Skeleton(height: 12)
                    Skeleton(height: 12).padding(.trailing, 96)
                }
            }
            .accessibilityHidden(true)
            .dsClearRow()
        }
    }

    // MARK: Order history

    private var ordersSection: some View {
        DSSection("Order history", subtitle: "Fills in \(stockDisplayTitle(route.symbol))") {
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

/// One bot buy/sell for the symbol (the web's row): "Buy · Momentum
/// breakout" with the price and when on the right, the reason, then who
/// backed it and whether it was overridden.
private struct StockBotEventRow: View {
    let event: BotTradeEvent

    var body: some View {
        let reason = event.reason.trimmingCharacters(in: .whitespacesAndNewlines)
        let backers = event.backers
        let meta = [event.price.map { fmtMoney($0) }, event.ts.map { fmtRelative($0) }].compactMap { $0 }.joined(separator: " · ")
        let footer = [backers.isEmpty ? nil : "Backed by \(backers.joined(separator: ", "))", event.overrideApplied ? "Overridden" : nil]
            .compactMap { $0 }
            .joined(separator: " · ")
        VStack(alignment: .leading, spacing: 4) {
            HStack(alignment: .firstTextBaseline, spacing: 16) {
                (Text(event.side.capitalized).foregroundStyle(DS.Text.secondary)
                    + Text("  ·  ").foregroundStyle(DS.Text.faint)
                    + Text(event.title).foregroundStyle(DS.Text.primary))
                    .font(DS.Font.body)
                    .lineLimit(1)
                    .frame(maxWidth: .infinity, alignment: .leading)
                if !meta.isEmpty {
                    Text(meta)
                        .font(DS.Font.caption)
                        .foregroundStyle(DS.Text.muted)
                        .monospacedDigit()
                }
            }
            if !reason.isEmpty {
                Text(reason)
                    .font(DS.Font.caption)
                    .foregroundStyle(DS.Text.secondary)
                    .lineSpacing(3)
                    .lineLimit(3)
            }
            if !footer.isEmpty {
                Text(footer)
                    .font(DS.Font.caption)
                    .foregroundStyle(DS.Text.muted)
                    .lineLimit(1)
            }
        }
        .padding(.vertical, 6)
        .accessibilityElement(children: .combine)
    }
}

/// One fill (the web's history row): the side over "12 sh · Nov 9, 3:42 PM";
/// the fill price over its total.
private struct StockOrderRow: View {
    let trade: Trade

    var body: some View {
        EntityRow(
            trade.side.capitalized,
            subtitle: "\(DashboardFormat.qtyNumber(trade.qty)) sh · \(fmtDateTime(trade.ts))"
        ) {
            EntityRowValue(fmtMoney(trade.price), detail: "Total \(fmtMoney(trade.price * trade.qty))")
        }
    }
}
