import SwiftUI

/// AI analysis — why a stock moved. For the chosen symbol (a holding from
/// the quick picks, or any ticker from search) and range: the price with a
/// marker on every day that stood out, coloured by what caused it (an
/// outline until it is explained), the daily moves under it, filter chips by
/// category, and the list of those days. A day opens its explanation:
/// the cause, the market / sector / stock-specific split and the sources.
/// "Explain moves" asks the chosen model and the screen re-reads every 3 s
/// until the answers land. Last, the selected account's last session, each
/// holding's move split the same way.
struct AnalysisView: View {
    let symbol: String?

    @Environment(AppServices.self) private var services

    var body: some View {
        AnalysisContent(symbol: symbol, services: services)
    }
}

private struct AnalysisContent: View {
    let services: AppServices

    @State private var model: AnalysisModel
    @State private var searching = false

    private static let topId = "analysis-top"

    init(symbol: String?, services: AppServices) {
        self.services = services
        _model = State(initialValue: AnalysisModel(
            symbol: symbol,
            repository: { [unowned services] in services.analysisRepository },
            holdings: { [unowned services] id in
                try await services.dashboardRepository.accountHoldings(id).positions.map(\.symbol)
            }
        ))
    }

    /// The dashboard's selected account.
    private var account: BrokerageAccount? {
        DashboardFormat.resolveSelected(services.dashboard.brokeragesValue ?? [], services.selectedAccount.selectedId)
    }

    /// Kalshi holds contracts, not stocks.
    private var isStockAccount: Bool {
        account.map { $0.brokerageType.lowercased() != "kalshi" } ?? false
    }

    /// Alpaca accounts lend their keys for news on explain requests.
    private var isAlpacaAccount: Bool {
        account?.brokerageType.lowercased() == "alpaca"
    }

    private struct AccountKey: Hashable {
        let id: String?
        let stock: Bool
        let alpaca: Bool
    }

    private var currentYear: Int {
        DartDateTime.localCalendar.component(.year, from: Date())
    }

    var body: some View {
        ScrollViewReader { proxy in
            List {
                Section {
                    Text("Why a stock moved: the market, its sector or its own news")
                        .font(DS.Font.pageSub)
                        .foregroundStyle(DS.Text.muted)
                        .dsClearRow(top: 0)
                        .id(Self.topId)
                    symbolPicker
                        .dsClearRow(top: 16, horizontal: 0)
                }
                seriesSections
                portfolioSection(proxy)
            }
            .dsList()
        }
        .navigationTitle("AI analysis")
        .navigationBarTitleDisplayMode(.large)
        .refreshable {
            await model.load()
            await model.reloadPortfolio()
        }
        .task(id: model.loadKey) { await model.load() }
        .task(id: model.pollKey) { await model.pollWhilePending() }
        // Opened from More before the dashboard loaded its accounts.
        .task {
            if services.dashboard.brokeragesValue == nil { await services.dashboard.loadBrokerages() }
        }
        .task(id: AccountKey(id: account?.id, stock: isStockAccount, alpaca: isAlpacaAccount)) {
            await model.loadAccount(account?.id, isStockAccount: isStockAccount, isAlpaca: isAlpacaAccount)
        }
        .sheet(item: $model.detail) { request in
            AnalysisEventSheet(model: model, eventId: request.id, onOpenSettings: openSettings)
        }
        .sheet(isPresented: $searching) {
            AnalysisSymbolSearchSheet(services: services) { model.select(symbol: $0) }
        }
        .toast($model.toast)
    }

    private func openSettings() {
        services.router.push(.settings)
    }

    // MARK: Symbol picker

    /// Search first, then the current symbol when it is not a holding, then
    /// the holdings.
    private var symbolPicker: some View {
        ScrollView(.horizontal, showsIndicators: false) {
            HStack(spacing: 8) {
                AnalysisChip(label: "Search", selected: false, action: { searching = true }) {
                    Image(systemName: "magnifyingglass")
                        .font(.system(size: 12))
                        .foregroundStyle(DS.Text.secondary)
                }
                .accessibilityLabel("Search symbols")
                if let symbol = model.symbol, model.showsSymbolChip {
                    AnalysisChip(label: symbol, selected: true) {}
                }
                ForEach(model.quickPicks, id: \.self) { s in
                    AnalysisChip(label: s, selected: model.isCurrent(s)) { model.select(symbol: s) }
                }
            }
            .padding(.horizontal, DS.pageGutter)
        }
        .scrollClipDisabled()
    }

    // MARK: Symbol, chart, moves

    @ViewBuilder
    private var seriesSections: some View {
        if let symbol = model.symbol {
            switch model.moves {
            case .loading:
                Section {
                    heroSkeleton.dsClearRow(top: 4)
                    VStack(spacing: 0) {
                        Skeleton(height: AnalysisMoveChart.priceHeight + AnalysisMoveChart.gap + AnalysisMoveChart.stripHeight, radius: DS.Radius.control)
                        rangeControl.padding(.top, 36)
                    }
                    .dsClearRow(top: 16)
                }
            case .failed(let error):
                Section {
                    failure(symbol: symbol, error: error).dsClearRow(top: 4)
                }
            case .loaded(let analysis):
                loadedSections(analysis)
            }
        } else {
            Section {
                Card(padding: 0) {
                    EmptyState(
                        title: "Choose a symbol",
                        subtitle: "Pick a holding above, or search for any ticker",
                        actionLabel: "Search",
                        onAction: { searching = true }
                    )
                }
                .dsClearRow(top: 4)
            }
        }
    }

    @ViewBuilder
    private func loadedSections(_ a: MoveAnalysis) -> some View {
        Section {
            hero(a).dsClearRow(top: 4)
            VStack(spacing: 0) {
                if a.bars.isEmpty {
                    Text("No price data for \(a.symbol) in this range")
                        .font(DS.Font.body)
                        .foregroundStyle(DS.Text.muted)
                        .frame(maxWidth: .infinity)
                        .frame(height: 200)
                } else {
                    AnalysisMoveChart(
                        bars: a.bars,
                        markers: model.markers,
                        selectedIndex: model.selectedDay,
                        range: model.range,
                        onTap: { model.tapDay($0) }
                    )
                }
                rangeControl.padding(.top, 16)
            }
            .dsClearRow(top: 16)
            if model.chips.count > 1 {
                categoryChips.dsClearRow(top: 16, horizontal: 0)
            }
        }
        if !a.modelConfigured {
            modelNotice
        }
        if let error = model.actionError {
            Section {
                ErrorRow(message: error).dsClearRow()
            }
        }
        DSSection("Moves", subtitle: AnalysisFormat.movesSubtitle(a), rowMinHeight: 64) {
            let events = model.visibleEvents
            if events.isEmpty {
                emptyRow(a.events.isEmpty ? "No day stood out in this range" : "No moves in this category")
            } else {
                ForEach(events) { event in
                    Button { model.open(event) } label: {
                        AnalysisEventRow(event: event, currentYear: currentYear)
                    }
                    .buttonStyle(.plain)
                }
            }
        } accessory: {
            explainControl(a)
        }
    }

    private var rangeControl: some View {
        DSSegmentedControl(
            "Chart range",
            selection: Binding(get: { model.range }, set: { model.setRange($0) }),
            options: AnalysisRange.allCases,
            fullWidth: true
        ) { $0.label }
    }

    /// The symbol and its profile, then the price: the range's move, or the
    /// tapped day's move with its split.
    private func hero(_ a: MoveAnalysis) -> some View {
        let profile = AnalysisFormat.profileLine(a)
        return VStack(alignment: .leading, spacing: 8) {
            HStack(alignment: .firstTextBaseline, spacing: 8) {
                Text(a.symbol.isEmpty ? (model.symbol ?? "") : a.symbol)
                    .font(DS.Font.large)
                    .foregroundStyle(DS.Text.primary)
                if !profile.isEmpty {
                    Text(profile)
                        .font(DS.Font.caption)
                        .foregroundStyle(DS.Text.muted)
                        .lineLimit(1)
                }
            }
            if let bar = model.selectedBar {
                HeroValueHeader(
                    fmtMoney(bar.close),
                    numericValue: bar.close,
                    valueAnimation: nil,
                    change: fmtPct(bar.retPct),
                    direction: ChangeDirection(bar.retPct),
                    changeLabel: AnalysisFormat.dayLabel(bar.date, currentYear: currentYear)
                )
                HStack(spacing: 12) {
                    Text(AnalysisFormat.splitText(market: bar.marketPct, sector: bar.sectorPct, specific: bar.specificPct) ?? "No split for this day")
                        .font(DS.Font.caption)
                        .foregroundStyle(DS.Text.secondary)
                        .monospacedDigit()
                        .frame(maxWidth: .infinity, alignment: .leading)
                    Button("Show range") { model.clearSelection() }
                        .buttonStyle(.dsGhost)
                }
            } else {
                let last = a.bars.last(where: { $0.close != nil })?.close
                let change = AnalysisFormat.rangeChange(a.bars)
                HeroValueHeader(
                    fmtMoney(last),
                    numericValue: last,
                    change: change.map { "\(fmtPnl($0.abs)) (\(fmtPct($0.pct)))" },
                    direction: ChangeDirection(change?.abs),
                    changeLabel: AnalysisFormat.rangeCaption(model.range)
                )
                Text(a.events.isEmpty ? "Tap the chart for a day's split" : "Tap a marker to see why that day moved")
                    .font(DS.Font.caption)
                    .foregroundStyle(DS.Text.muted)
                    .frame(minHeight: 32, alignment: .leading)
            }
        }
    }

    private var heroSkeleton: some View {
        VStack(alignment: .leading, spacing: 12) {
            Skeleton(width: 120, height: 18)
            Skeleton(width: 192, height: 44, radius: DS.Radius.small)
            Skeleton(width: 160, height: 16)
        }
        .accessibilityElement(children: .ignore)
        .accessibilityLabel("Loading")
    }

    /// An older server (no AI analysis), an unknown symbol, or any other
    /// failure; a 400 is the request itself, so it gets no Retry.
    @ViewBuilder
    private func failure(symbol: String, error: any Error) -> some View {
        if model.isServerUnsupported {
            Card(padding: 0) {
                EmptyState(
                    title: AnalysisFormat.missingRouteMessage,
                    subtitle: "Update IntelliStock on the server to use it"
                )
            }
        } else if model.isNotFound {
            Card(padding: 0) {
                EmptyState(
                    title: "No price history for \(symbol)",
                    subtitle: "Check the ticker, or pick another symbol",
                    actionLabel: "Search",
                    onAction: { searching = true }
                )
            }
        } else {
            ErrorRow(
                message: (error as? ApiError)?.message ?? error.localizedDescription,
                onRetry: AnalysisFormat.isRetryable(error) ? { Task { await model.retry() } } : nil
            )
        }
    }

    private var categoryChips: some View {
        ScrollView(.horizontal, showsIndicators: false) {
            HStack(spacing: 8) {
                ForEach(model.chips) { chip in
                    AnalysisChip(label: chip.label, count: chip.count, selected: model.filter == chip.filter) {
                        model.setFilter(chip.filter)
                    } leading: {
                        if let family = chip.family {
                            AnalysisMarkerDot(style: .solid(family), size: 8)
                        } else if chip.filter == .notExplained {
                            AnalysisMarkerDot(style: .hollow, size: 8)
                        }
                    }
                }
            }
            .padding(.horizontal, DS.pageGutter)
        }
        .scrollClipDisabled()
        .accessibilityLabel("Filter by cause")
    }

    /// Shown when no model is chosen: the split still works, the
    /// explanations need one.
    private var modelNotice: some View {
        Section {
            Card {
                VStack(alignment: .leading, spacing: 12) {
                    HStack(spacing: 8) {
                        DSStatusDot(.warn)
                        Text("No model chosen")
                            .font(DS.Font.bodyMedium)
                            .foregroundStyle(DS.Text.primary)
                    }
                    Text("The split works without one. To name what caused each move, choose the model that explains them in Settings.")
                        .font(DS.Font.body)
                        .foregroundStyle(DS.Text.secondary)
                        .fixedSize(horizontal: false, vertical: true)
                    Button("Open settings", action: openSettings)
                        .buttonStyle(.dsSecondary)
                }
                .frame(maxWidth: .infinity, alignment: .leading)
            }
            .dsClearRow()
        }
    }

    /// The Moves header's action: Explain moves, or progress while the
    /// model works.
    @ViewBuilder
    private func explainControl(_ a: MoveAnalysis) -> some View {
        if model.explaining || model.isPolling {
            HStack(spacing: 6) {
                ProgressView()
                    .controlSize(.small)
                    .tint(DS.Text.muted)
                Text(a.pending > 0 ? "Explaining \(a.pending)" : "Explaining")
                    .font(DS.Font.caption)
                    .foregroundStyle(DS.Text.muted)
                    .monospacedDigit()
            }
            .accessibilityElement(children: .combine)
        } else if a.modelConfigured, a.unexplainedCount > 0 {
            Button("Explain moves") {
                Task { await model.explainAll() }
            }
            .buttonStyle(.dsSecondary)
            .disabled(!model.canExplainAll)
        }
    }

    // MARK: Today in your account

    @ViewBuilder
    private func portfolioSection(_ proxy: ScrollViewProxy) -> some View {
        DSSection("Today in your account", subtitle: portfolioSubtitle, rowMinHeight: 64) {
            if services.dashboard.brokeragesValue == nil {
                DashboardPlaceholderRows(count: 3)
            } else if let account {
                if !isStockAccount {
                    emptyRow("\(DashboardFormat.accountName(account)) holds no stock positions")
                } else {
                    switch model.portfolio {
                    case nil, .loading?:
                        DashboardPlaceholderRows(count: 3)
                    case .failed(let error)?:
                        if model.isPortfolioUnsupported {
                            emptyRow(AnalysisFormat.missingRouteMessage)
                        } else {
                            portfolioError(error)
                        }
                    case .loaded(let p)?:
                        if p.holdings.isEmpty {
                            emptyRow("No stock positions in \(DashboardFormat.accountName(account))")
                        } else {
                            AnalysisTotalsRow(totals: p.totals, spyPct: p.spyPct)
                            ForEach(p.holdings) { h in
                                Button {
                                    model.select(symbol: h.symbol)
                                    withAnimation(DSMotion.standard) { proxy.scrollTo(Self.topId, anchor: .top) }
                                } label: {
                                    AnalysisHoldingRow(holding: h, selected: model.isCurrent(h.symbol))
                                }
                                .buttonStyle(.plain)
                                .accessibilityHint("Shows \(h.symbol) above")
                            }
                        }
                    }
                }
            } else {
                emptyRow("Link a brokerage account to see its holdings here")
            }
        }
    }

    /// `Alpaca Live · Nov 13 · S&P 500 -0.70%`.
    private var portfolioSubtitle: String? {
        guard let account else { return nil }
        var parts = [DashboardFormat.accountName(account)]
        if let p = model.portfolio?.value {
            if let asOf = p.asOf { parts.append(AnalysisFormat.dayLabel(asOf, currentYear: currentYear)) }
            if let spy = p.spyPct { parts.append("S&P 500 \(fmtPct(spy))") }
        }
        return parts.joined(separator: " · ")
    }

    private func portfolioError(_ error: any Error) -> some View {
        HStack(spacing: 12) {
            Text((error as? ApiError)?.message ?? error.localizedDescription)
                .font(DS.Font.body)
                .foregroundStyle(DS.Palette.loss)
                .frame(maxWidth: .infinity, alignment: .leading)
                .fixedSize(horizontal: false, vertical: true)
            if AnalysisFormat.isRetryable(error) {
                Button("Retry") { Task { await model.reloadPortfolio() } }
                    .buttonStyle(.dsSecondary)
            }
        }
    }

    /// The web's empty card line: one centred sentence in zinc-400.
    private func emptyRow(_ message: String) -> some View {
        Text(message)
            .font(DS.Font.body)
            .foregroundStyle(DS.Text.secondary)
            .multilineTextAlignment(.center)
            .frame(maxWidth: .infinity)
            .padding(.vertical, 16)
    }
}

/// The account's whole move: the contribution total, split three ways,
/// against the S&P 500.
private struct AnalysisTotalsRow: View {
    let totals: PortfolioMoveTotals?
    let spyPct: Double?

    var body: some View {
        HStack(alignment: .firstTextBaseline, spacing: 12) {
            VStack(alignment: .leading, spacing: 4) {
                Text("Account")
                    .font(DS.Font.bodyMedium)
                    .foregroundStyle(DS.Text.primary)
                Text(AnalysisFormat.splitText(market: totals?.marketPct, sector: totals?.sectorPct, specific: totals?.specificPct) ?? "No split")
                    .font(DS.Font.caption)
                    .foregroundStyle(DS.Text.muted)
                    .lineLimit(2)
            }
            .frame(maxWidth: .infinity, alignment: .leading)
            VStack(alignment: .trailing, spacing: 4) {
                Text(fmtPct(totals?.contributionPct))
                    .font(DS.Font.bodyMedium)
                    .foregroundStyle(dashboardChangeColor(totals?.contributionPct))
                if let spyPct {
                    Text("S&P 500 \(fmtPct(spyPct))")
                        .font(DS.Font.caption)
                        .foregroundStyle(DS.Text.muted)
                }
            }
            .monospacedDigit()
        }
        .padding(.vertical, 4)
        .accessibilityElement(children: .combine)
    }
}

/// One holding's last session: its weight, its contribution to the account
/// split three ways, and the explanation when the day was flagged.
private struct AnalysisHoldingRow: View {
    let holding: PortfolioHoldingMove
    let selected: Bool

    var body: some View {
        let h = holding
        HStack(alignment: .firstTextBaseline, spacing: 12) {
            VStack(alignment: .leading, spacing: 4) {
                HStack(spacing: 6) {
                    Text(h.symbol)
                        .font(DS.Font.bodyMedium)
                        .foregroundStyle(DS.Text.primary)
                    if let w = h.weightPct {
                        Text("\(dartToStringAsFixed(w, 1))% of the account")
                            .font(DS.Font.caption)
                            .foregroundStyle(DS.Text.muted)
                            .monospacedDigit()
                    }
                    if let event = h.event {
                        AnalysisMarkerDot(style: AnalysisMarkerStyle(event), size: 8)
                    }
                }
                if let split = AnalysisFormat.splitText(market: h.marketPct, sector: h.sectorPct, specific: h.specificPct) {
                    Text(split)
                        .font(DS.Font.caption)
                        .foregroundStyle(DS.Text.muted)
                        .monospacedDigit()
                        .lineLimit(2)
                }
                if let event = h.event {
                    Text(event.isExplained ? AnalysisFormat.title(event) : "Stood out today · \(AnalysisFormat.title(event).lowercased())")
                        .font(DS.Font.caption)
                        .foregroundStyle(event.isExplained ? DS.Text.secondary : DS.Text.muted)
                        .lineLimit(2)
                }
            }
            .frame(maxWidth: .infinity, alignment: .leading)
            VStack(alignment: .trailing, spacing: 4) {
                Text(fmtPct(h.contributionPct))
                    .font(DS.Font.bodyMedium)
                    .foregroundStyle(dashboardChangeColor(h.contributionPct))
                Text("\(fmtPct(h.retPct)) day")
                    .font(DS.Font.caption)
                    .foregroundStyle(DS.Text.muted)
            }
            .monospacedDigit()
        }
        .padding(.vertical, 4)
        .contentShape(Rectangle())
        .accessibilityElement(children: .combine)
        .accessibilityAddTraits(selected ? .isSelected : [])
    }
}
