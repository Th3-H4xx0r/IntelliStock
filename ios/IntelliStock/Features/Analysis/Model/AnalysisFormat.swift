import Foundation

/// What the event list and the chart are narrowed to.
nonisolated enum AnalysisFilter: Hashable, Sendable {
    case all
    /// Explained events of one category key.
    case category(String)
    /// Unexplained, pending or failed events.
    case notExplained
}

/// One filter chip: its label and how many events it matches.
nonisolated struct AnalysisFilterChip: Hashable, Sendable, Identifiable {
    let filter: AnalysisFilter
    let label: String
    let count: Int
    /// The colour family of a category chip; nil for All and Not explained.
    let family: AnalysisCategoryFamily?

    var id: AnalysisFilter { filter }
}

/// The colour a marker takes. Eleven categories cannot each get a hue that
/// stays distinguishable on a line chart (only three validate for every pair
/// on the dark card, colour-blind included), so categories share three hue
/// families plus two neutrals; the chips and rows always name the category.
nonisolated enum AnalysisCategoryFamily: String, CaseIterable, Hashable, Sendable {
    /// The company's own news: results, outlook, deals and products,
    /// management, capital and insiders.
    case company
    /// What others said: analysts and sector peers.
    case street
    /// Outside forces: the economy, regulators and courts.
    case macro
    /// A market-wide day, labelled without the model.
    case market
    /// Explained, but no cited article supported a cause.
    case noCatalyst
    /// A category this build does not know.
    case other

    init(category: String?) {
        switch category?.lowercased() {
        case "earnings", "guidance", "deal_product", "management", "capital_insider": self = .company
        case "analyst", "sector_peer": self = .street
        case "macro", "regulatory_legal": self = .macro
        case "market": self = .market
        case "no_catalyst": self = .noCatalyst
        default: self = .other
        }
    }
}

/// How an event's marker is drawn.
nonisolated enum AnalysisMarkerStyle: Hashable, Sendable {
    /// Explained: filled in its family's colour.
    case solid(AnalysisCategoryFamily)
    /// Unexplained or pending: an outline.
    case hollow
    /// The explain attempt failed: an outline in the loss colour.
    case failed

    init(_ event: MoveEvent) {
        switch event.status {
        case .explained: self = .solid(AnalysisCategoryFamily(category: event.category))
        case .failed: self = .failed
        case .unexplained, .pending: self = .hollow
        }
    }
}

/// An event placed on the chart: the bar it sits on.
nonisolated struct AnalysisChartMarker: Hashable, Sendable {
    let index: Int
    let event: MoveEvent
}

/// Pure presentation helpers for the AI analysis screen.
nonisolated enum AnalysisFormat {
    // MARK: Filtering

    /// All, then each category with at least one explained event (in the
    /// server's order, unknown keys after), then Not explained when any are.
    static func chips(_ analysis: MoveAnalysis) -> [AnalysisFilterChip] {
        let events = analysis.events
        var chips = [AnalysisFilterChip(filter: .all, label: "All", count: events.count, family: nil)]
        var counts: [String: Int] = [:]
        var order: [String] = []
        for e in events where e.isExplained {
            guard let key = e.category else { continue }
            if counts[key] == nil { order.append(key) }
            counts[key, default: 0] += 1
        }
        let known = analysis.categories.map(\.key)
        let keys = known.filter { counts[$0] != nil } + order.filter { !known.contains($0) }
        for key in keys {
            chips.append(AnalysisFilterChip(
                filter: .category(key),
                label: analysis.label(for: key),
                count: counts[key] ?? 0,
                family: AnalysisCategoryFamily(category: key)
            ))
        }
        let open = events.filter { !$0.isExplained }.count
        if open > 0 {
            chips.append(AnalysisFilterChip(filter: .notExplained, label: "Not explained", count: open, family: nil))
        }
        return chips
    }

    /// The events a filter keeps, in their order (newest first).
    static func events(_ events: [MoveEvent], matching filter: AnalysisFilter) -> [MoveEvent] {
        switch filter {
        case .all: events
        case .category(let key): events.filter { $0.isExplained && $0.category == key }
        case .notExplained: events.filter { !$0.isExplained }
        }
    }

    /// Each event matched to its bar by date; events outside the bars are
    /// left off the chart (they still list).
    static func markers(bars: [MoveBar], events: [MoveEvent]) -> [AnalysisChartMarker] {
        let index = barIndexByDate(bars)
        return events.compactMap { e in index[e.date].map { AnalysisChartMarker(index: $0, event: e) } }
            .sorted { $0.index < $1.index }
    }

    static func barIndexByDate(_ bars: [MoveBar]) -> [String: Int] {
        var out: [String: Int] = [:]
        for (i, b) in bars.enumerated() where !b.date.isEmpty { out[b.date] = i }
        return out
    }

    // MARK: Text

    private static let months = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]

    /// `2026-10-02` → `Oct 2` in `currentYear`, else `Oct 2, 2025`.
    static func dayLabel(_ ymd: String, currentYear: Int) -> String {
        let parts = ymd.split(separator: "-").compactMap { Int($0) }
        guard parts.count == 3, (1...12).contains(parts[1]) else { return ymd }
        let short = "\(months[parts[1] - 1]) \(parts[2])"
        return parts[0] == currentYear ? short : "\(short), \(parts[0])"
    }

    /// `dayLabel` against today's year on this device.
    static func dayLabel(_ ymd: String, now: Date = Date()) -> String {
        dayLabel(ymd, currentYear: DartDateTime.localCalendar.component(.year, from: now))
    }

    /// `Market -0.80% · Sector -1.10% · Stock -6.00%`; parts without a value
    /// are left out, and nil when none has one. A no-break space holds each
    /// label to its value, so a wrapped line breaks between parts.
    static func splitText(market: Double?, sector: Double?, specific: Double?) -> String? {
        let parts = [("Market", market), ("Sector", sector), ("Stock", specific)]
            .compactMap { label, v in v.map { "\(label)\u{00A0}\(fmtPct($0))" } }
        return parts.isEmpty ? nil : parts.joined(separator: " · ")
    }

    /// The event row's first line: the headline, else what is happening.
    static func title(_ e: MoveEvent) -> String {
        switch e.status {
        case .explained: e.headline ?? e.categoryLabel ?? "Explained"
        case .pending: "Explaining…"
        case .failed: "Couldn\u{2019}t explain this move"
        case .unexplained: "Not explained yet"
        }
    }

    /// `Medium confidence`; nil when absent.
    static func confidenceLabel(_ c: String?) -> String? {
        guard let c, !c.isEmpty else { return nil }
        return "\(c.prefix(1).uppercased())\(c.dropFirst().lowercased()) confidence"
    }

    /// The event row's second line: `Oct 2 · Analysts`. The category is left
    /// out when the headline already says it ("No catalyst found"); the
    /// confidence is in the detail.
    static func metaLine(_ e: MoveEvent, currentYear: Int) -> String {
        var parts = [dayLabel(e.date, currentYear: currentYear)]
        if e.isExplained, let label = e.categoryLabel ?? e.category,
           label.caseInsensitiveCompare(title(e)) != .orderedSame {
            parts.append(label)
        }
        return parts.joined(separator: " · ")
    }

    /// `Technology · XLK · beta 1.62`, from what the server knows. No beta
    /// when the server had too little history to estimate one.
    static func profileLine(_ a: MoveAnalysis) -> String {
        var parts: [String] = []
        if let sector = a.sector {
            let label = sectorLabel(sector)
            if !label.isEmpty { parts.append(label) }
        }
        if let etf = a.sectorEtf { parts.append(etf) }
        if let beta = a.beta { parts.append("beta \(dartToStringAsFixed(beta, 2))") }
        return parts.joined(separator: " · ")
    }

    /// A sector key as words: `financial_services` → `Financial services`,
    /// `nasdaq_100` → `Nasdaq 100`.
    static func sectorLabel(_ key: String) -> String {
        let words = key.replacingOccurrences(of: "_", with: " ").trimmingCharacters(in: .whitespaces)
        guard let first = words.first else { return "" }
        return first.uppercased() + words.dropFirst()
    }

    // MARK: Symbols

    /// A symbol the server would treat as the same: upper-cased, with `.`
    /// and `/` spelled `-` (the server answers `BRK.B` as `BRK-B`).
    static func canonical(_ symbol: String) -> String {
        symbol.trimmingCharacters(in: .whitespacesAndNewlines).uppercased()
            .replacingOccurrences(of: ".", with: "-")
            .replacingOccurrences(of: "/", with: "-")
    }

    private static let cryptoQuotes = ["USDT", "USDC", "USD"]

    /// A crypto pair, which has no sector to split against: `BTC/USD`,
    /// Yahoo's `BTC-USD`, or Alpaca's `BTCUSD` (letters only, ending in a
    /// dollar quote, longer than any US stock ticker's five letters).
    static func isCryptoSymbol(_ raw: String) -> Bool {
        let s = raw.trimmingCharacters(in: .whitespacesAndNewlines).uppercased()
        if s.contains("/") { return true }
        for quote in cryptoQuotes {
            if s.hasSuffix("-\(quote)") { return true }
            if s.count >= 6, s.hasSuffix(quote), s.allSatisfy({ $0.isASCII && $0.isLetter }) { return true }
        }
        return false
    }

    /// Search results the analysis can use: no crypto.
    static func analyzableResults(_ results: [SearchInstrument]) -> [SearchInstrument] {
        results.filter { $0.type.lowercased() != "crypto" && !isCryptoSymbol($0.symbol) }
    }

    // MARK: Errors

    /// A server that predates these routes: FastAPI's bare 404, detail
    /// exactly `Not Found`. The real unknown-symbol 404 names the symbol.
    static func isMissingRoute(_ error: (any Error)?) -> Bool {
        guard let api = error as? ApiError else { return false }
        return api.statusCode == 404 && api.message == "Not Found"
    }

    /// The copy for `isMissingRoute`.
    static let missingRouteMessage = "This server has no AI analysis yet"

    /// Worth a Retry: not a 400 (the request itself is wrong) and not a
    /// server without the feature.
    static func isRetryable(_ error: (any Error)?) -> Bool {
        if isMissingRoute(error) { return false }
        return (error as? ApiError)?.statusCode != 400
    }

    /// The detail sheet's action: Re-run an explained day, Try again after
    /// a failure, else Explain.
    static func explainButtonTitle(_ e: MoveEvent) -> String {
        switch e.status {
        case .explained: "Re-run"
        case .failed: "Try again"
        case .unexplained, .pending: "Explain"
        }
    }

    /// The range's move: last close against the first.
    static func rangeChange(_ bars: [MoveBar]) -> (abs: Double, pct: Double)? {
        let closes = bars.compactMap(\.close)
        guard closes.count >= 2, let first = closes.first, let last = closes.last, first != 0 else { return nil }
        return (last - first, (last - first) / first * 100)
    }

    /// `1M` → `Past month`.
    static func rangeCaption(_ range: AnalysisRange) -> String {
        switch range {
        case .oneMonth: "Past month"
        case .threeMonths: "Past 3 months"
        case .sixMonths: "Past 6 months"
        case .oneYear: "Past year"
        }
    }

    /// The Moves section's subtitle.
    static func movesSubtitle(_ a: MoveAnalysis) -> String {
        let n = a.events.count
        if n == 0 { return "No day stood out in this range" }
        let days = n == 1 ? "1 day stood out" : "\(n) days stood out"
        if a.pending > 0 { return "\(days) · explaining \(a.pending)" }
        let open = a.unexplainedCount
        return open == 0 ? days : "\(days) · \(open) not explained"
    }
}
