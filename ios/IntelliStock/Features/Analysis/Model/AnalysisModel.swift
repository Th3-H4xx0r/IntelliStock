import Foundation
import Observation

/// The event the detail sheet shows, by id, so the sheet reads the latest
/// copy while explanations land.
struct AnalysisDetailRequest: Identifiable, Hashable {
    let id: String
}

/// The AI analysis screen's state: the chosen symbol and range, its moves,
/// the filter and selection, the explain action (polling every 3 s while
/// events are pending), the account's holdings as quick picks, and the
/// account's last session.
@Observable
final class AnalysisModel {
    static let pollInterval: Duration = .seconds(3)

    /// Changes whenever the moves must be fetched again.
    struct LoadKey: Hashable {
        let symbol: String?
        let range: AnalysisRange
    }

    /// Changes whenever polling must start or stop.
    struct PollKey: Hashable {
        let symbol: String?
        let range: AnalysisRange
        let polling: Bool
    }

    private(set) var symbol: String?
    private(set) var range: AnalysisRange
    /// The current symbol and range; `.loading` until the first answer.
    /// A failed refresh keeps the data on screen.
    private(set) var moves: Loadable<MoveAnalysis> = .loading
    private(set) var filter: AnalysisFilter = .all
    /// The bar the chart's hero shows instead of the range.
    private(set) var selectedDay: Int?
    /// The event whose detail sheet is open.
    var detail: AnalysisDetailRequest?
    /// An explain request is in flight (no double submit).
    private(set) var explaining = false
    /// Events whose own Explain or Re-run is in flight.
    private(set) var explainingIds: Set<String> = []
    /// The last explain failure, shown under the chart.
    var actionError: String?
    var toast: Toast?

    /// The selected account's holdings (equities), largest first.
    private(set) var quickPicks: [String] = []
    /// The account the "Today" section is for; nil when none is linked.
    private(set) var accountId: String?
    /// nil when there is no stock account to ask about.
    private(set) var portfolio: Loadable<PortfolioMoves>?

    @ObservationIgnored private let repository: () -> AnalysisRepository
    @ObservationIgnored private let holdings: (String) async throws -> [String]
    @ObservationIgnored private let sleep: PollingSleep
    /// Bumped on every symbol or range change; answers for an older one are dropped.
    @ObservationIgnored private var generation = 0
    @ObservationIgnored private var accountGeneration = 0

    init(
        symbol: String? = nil,
        range: AnalysisRange = .threeMonths,
        repository: @escaping () -> AnalysisRepository,
        holdings: @escaping (String) async throws -> [String] = { _ in [] },
        sleep: @escaping PollingSleep = realPollingSleep
    ) {
        self.symbol = Self.normalize(symbol)
        self.range = range
        self.repository = repository
        self.holdings = holdings
        self.sleep = sleep
    }

    /// Trimmed and upper-cased; blank is nil.
    nonisolated static func normalize(_ symbol: String?) -> String? {
        guard let s = symbol?.trimmingCharacters(in: .whitespacesAndNewlines).uppercased(), !s.isEmpty else { return nil }
        return s
    }

    // MARK: Derived

    var analysis: MoveAnalysis? { moves.value }
    var loadKey: LoadKey { LoadKey(symbol: symbol, range: range) }
    var pollKey: PollKey { PollKey(symbol: symbol, range: range, polling: isPolling) }
    var isPolling: Bool { (analysis?.pending ?? 0) > 0 }
    var modelConfigured: Bool { analysis?.modelConfigured ?? false }
    var chips: [AnalysisFilterChip] { analysis.map(AnalysisFormat.chips) ?? [] }
    var visibleEvents: [MoveEvent] { AnalysisFormat.events(analysis?.events ?? [], matching: filter) }

    var markers: [AnalysisChartMarker] {
        guard let analysis else { return [] }
        return AnalysisFormat.markers(bars: analysis.bars, events: visibleEvents)
    }

    /// The server could not find the symbol, or it has no price history.
    var isNotFound: Bool { (moves.error as? ApiError)?.statusCode == 404 }

    /// "Explain moves" applies: a model is chosen and something is left.
    var canExplainAll: Bool {
        guard let analysis, analysis.modelConfigured, !explaining else { return false }
        return analysis.unexplainedCount > 0
    }

    /// The detail sheet's Explain / Re-run applies.
    func canExplain(_ event: MoveEvent) -> Bool {
        modelConfigured && !explaining && event.status != .pending
    }

    func event(id: String) -> MoveEvent? {
        analysis?.events.first { $0.id == id }
    }

    var selectedBar: MoveBar? {
        guard let selectedDay, let bars = analysis?.bars, bars.indices.contains(selectedDay) else { return nil }
        return bars[selectedDay]
    }

    // MARK: Symbol, range, filter, selection

    /// Shows `symbol`. The view's `.task(id: loadKey)` fetches it.
    func select(symbol raw: String) {
        guard let next = Self.normalize(raw), next != symbol else { return }
        symbol = next
        resetForNewSeries()
    }

    func setRange(_ next: AnalysisRange) {
        guard next != range else { return }
        range = next
        resetForNewSeries()
    }

    private func resetForNewSeries() {
        generation += 1
        moves = .loading
        selectedDay = nil
        detail = nil
        actionError = nil
    }

    /// Narrows the list and the chart's markers. The selected day stays: it
    /// is a day, not a marker.
    func setFilter(_ next: AnalysisFilter) {
        filter = next
    }

    /// A tap on the chart: shows that day in the hero, and opens its event
    /// when the filter shows one there. Tapping the selected day again
    /// returns the hero to the range.
    func tapDay(_ index: Int) {
        guard let bars = analysis?.bars, bars.indices.contains(index) else { return }
        if selectedDay == index {
            selectedDay = nil
            return
        }
        selectedDay = index
        let date = bars[index].date
        if let event = visibleEvents.first(where: { $0.date == date }) {
            detail = AnalysisDetailRequest(id: event.id)
        }
    }

    func clearSelection() {
        selectedDay = nil
    }

    func open(_ event: MoveEvent) {
        detail = AnalysisDetailRequest(id: event.id)
    }

    // MARK: Loading and polling

    /// Fetches the current symbol and range. A failure shows only when
    /// nothing is on screen; an answer for an older symbol or range is dropped.
    func load() async {
        guard let symbol else { return }
        let started = generation
        let r = range
        do {
            let value = try await repository().moves(symbol: symbol, range: r)
            guard started == generation else { return }
            apply(value)
        } catch {
            guard started == generation, !error.isCancellationOrTaskCancelled else { return }
            if moves.value == nil { moves = .failed(error) }
        }
    }

    /// The error state's Retry.
    func retry() async {
        moves = .loading
        await load()
    }

    private func apply(_ value: MoveAnalysis) {
        moves = .loaded(value)
        if !AnalysisFormat.chips(value).contains(where: { $0.filter == filter }) {
            filter = .all
        }
        if let selectedDay, !value.bars.indices.contains(selectedDay) {
            self.selectedDay = nil
        }
    }

    /// Re-reads the moves every `pollInterval` while any event is pending;
    /// returns when none is, when the symbol or range changes, or when the
    /// calling task is cancelled (the view went away).
    func pollWhilePending() async {
        let started = generation
        while started == generation, isPolling {
            do { try await sleep(Self.pollInterval) } catch { return }
            guard !Task.isCancelled, started == generation else { return }
            await load()
        }
    }

    // MARK: Explain

    /// "Explain moves": every unexplained or failed event in the range.
    func explainAll() async {
        await explain(dates: nil, force: false, ids: [])
    }

    /// The detail sheet's Explain (or Re-run, which forces an explained one).
    func explain(_ event: MoveEvent) async {
        await explain(dates: [event.date], force: event.isExplained, ids: [event.id])
    }

    private func explain(dates: [String]?, force: Bool, ids: Set<String>) async {
        guard let symbol, !explaining else { return }
        let started = generation
        explaining = true
        explainingIds.formUnion(ids)
        actionError = nil
        defer {
            explaining = false
            explainingIds.subtract(ids)
        }
        do {
            let queued = try await repository().explain(symbol: symbol, range: range, dates: dates, force: force)
            guard started == generation else { return }
            if queued == 0 { toast = Toast("Nothing to explain") }
            // Picks up the pending rows; the view's poll task takes over.
            await load()
        } catch {
            guard started == generation, !error.isCancellation else { return }
            actionError = (error as? ApiError)?.message ?? error.localizedDescription
        }
    }

    // MARK: Account

    /// The dashboard's selected account: its holdings become the quick
    /// picks (the first one is shown when no symbol is chosen yet), and its
    /// last session fills "Today in your account". `isStockAccount` false
    /// (Kalshi) skips the portfolio call.
    func loadAccount(_ id: String?, isStockAccount: Bool) async {
        accountGeneration += 1
        let started = accountGeneration
        let changed = id != accountId
        accountId = id
        guard let id else {
            quickPicks = []
            portfolio = nil
            return
        }
        if changed { quickPicks = [] }
        if isStockAccount, let symbols = try? await holdings(id), started == accountGeneration {
            setQuickPicks(symbols)
        }
        guard isStockAccount else {
            portfolio = nil
            return
        }
        if changed || portfolio == nil { portfolio = .loading }
        await fetchPortfolio(id, started: started)
    }

    /// The "Today" section's Retry and pull-to-refresh.
    func reloadPortfolio() async {
        guard let id = accountId, portfolio != nil else { return }
        accountGeneration += 1
        let started = accountGeneration
        if portfolio?.value == nil { portfolio = .loading }
        await fetchPortfolio(id, started: started)
    }

    private func fetchPortfolio(_ id: String, started: Int) async {
        do {
            let value = try await repository().portfolio(brokerageId: id)
            guard started == accountGeneration else { return }
            portfolio = .loaded(value)
        } catch {
            guard started == accountGeneration, !error.isCancellationOrTaskCancelled else { return }
            if portfolio?.value == nil { portfolio = .failed(error) }
        }
    }

    /// Equity tickers only (no options), first-seen order, no repeats.
    func setQuickPicks(_ symbols: [String]) {
        var seen = Set<String>()
        quickPicks = symbols.compactMap { raw -> String? in
            guard let s = Self.normalize(raw), !isOccOptionSymbol(s), TradeTicket.isTradableSymbol(s), seen.insert(s).inserted else { return nil }
            return s
        }
        if symbol == nil, let first = quickPicks.first {
            select(symbol: first)
        }
    }
}
