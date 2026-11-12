import Foundation
import Observation

/// Which list the Orders section shows.
nonisolated enum OrdersMode: String, Hashable, Sendable, CaseIterable {
    case open, recent

    /// The first load's choice: Open when anything is working, else Recent.
    static func initial(for book: OrderBook) -> OrdersMode {
        book.open.isEmpty ? .recent : .open
    }

    /// The segment label: "Open (2)" with a count, "Open" without; "Recent".
    func label(openCount: Int) -> String {
        switch self {
        case .open: openCount > 0 ? "Open (\(openCount))" : "Open"
        case .recent: "Recent"
        }
    }

    /// The section's one row when the list is empty.
    var emptyText: String { self == .open ? "No open orders" : "No recent orders" }
}

/// The selected stock account's open orders and recent order history for
/// the dashboard's Orders section. Read-only.
///
/// One instance per selected account (it lives in `DashboardAccountScope`).
/// Polls every 30 s while the dashboard is on screen, pausing in the
/// background, the way `AccountHoldingsModel` does:
///
/// - a failed first load shows as `.failed`;
/// - a failed poll keeps the last good data;
/// - the mode is settled once, by the first good load or by the user, and a
///   later poll never flips it.
@Observable
final class AccountOrdersModel {
    static let interval: Duration = .seconds(30)
    /// How many closed orders to ask for.
    static let recentLimit = 25
    /// Rows shown before "Show all".
    static let visibleLimit = 10

    let brokerageId: String
    private(set) var book: Loadable<OrderBook> = .loading
    private(set) var mode: OrdersMode = .open
    /// The modes whose list the user expanded with "Show all".
    private(set) var expanded: Set<OrdersMode> = []

    @ObservationIgnored private let repository: () -> DashboardRepository
    @ObservationIgnored private var modeSettled = false

    init(brokerageId: String, repository: @escaping () -> DashboardRepository) {
        self.brokerageId = brokerageId
        self.repository = repository
    }

    // MARK: What the section shows

    /// The section is hidden: still loading, or the account has no order book.
    var isHidden: Bool {
        switch book {
        case .loading: true
        case .loaded(let book): !book.supported
        case .failed: false
        }
    }

    var openCount: Int { book.value?.open.count ?? 0 }

    /// Every order in the current mode.
    var orders: [BrokerOrder] {
        guard let book = book.value else { return [] }
        return mode == .open ? book.open : book.recent
    }

    /// The rows to draw: the first `visibleLimit`, or all once expanded.
    var visibleOrders: [BrokerOrder] {
        expanded.contains(mode) ? orders : Array(orders.prefix(Self.visibleLimit))
    }

    /// Orders behind "Show all"; 0 when none are hidden.
    var hiddenCount: Int { orders.count - visibleOrders.count }

    // MARK: Actions

    /// The user's pick, kept for the life of this account's scope.
    func select(_ mode: OrdersMode) {
        self.mode = mode
        modeSettled = true
    }

    /// "Show all": expands the current list in place.
    func showAll() {
        expanded.insert(mode)
    }

    // MARK: Loading

    /// One fetch. Success replaces the book; a failure shows only when there
    /// is nothing to keep, and a cancellation changes nothing.
    private func fetch() async {
        do {
            let value = try await repository().orderBook(brokerageId, recentLimit: Self.recentLimit)
            if !modeSettled {
                mode = OrdersMode.initial(for: value)
                modeSettled = true
            }
            book = .loaded(value)
        } catch {
            if error.isCancellationOrTaskCancelled { return }
            if book.value == nil { book = .failed(error) }
        }
    }

    /// One poll tick. An account without an order book is not asked again
    /// until a pull to refresh.
    func refresh() async {
        if book.value?.supported == false { return }
        await fetch()
    }

    /// Pull to refresh and Retry: always asks.
    func reload() async {
        await fetch()
    }

    /// First fetch (unless already loaded), then every 30 s until cancelled,
    /// pausing in the background.
    func poll(lifecycle: AppLifecycle?, sleep: @escaping PollingSleep = realPollingSleep) async {
        if book.value == nil { await fetch() }
        await PollingLoop(interval: { Self.interval }, sleep: sleep) { [weak self] in
            await self?.refresh()
        }
        .run(lifecycle: lifecycle)
    }
}

/// One order row's text: the symbol, "Buy · Limit · 3 sh", the figure, and
/// the status line ("Filled · 2h ago").
nonisolated struct OrderRowText: Equatable, Sendable {
    let title: String
    /// Side · type · size, sentence case: "Buy · Limit · 3 sh",
    /// "Sell · Trailing stop · 2 ct", "Buy · Market · $250.00".
    let subtitle: String?
    /// The fill's dollar value, else the limit or stop price, else "Market".
    /// A dollar order's amount is its size, on the left.
    let value: String
    /// A closed order that never filled: its figure is drawn secondary.
    let isQuiet: Bool
    /// "Filled · 2h ago", "Partially filled · 5m ago", "Open · Nov 3".
    let detail: String

    init(_ order: BrokerOrder, now: Date = Date(), calendar: Calendar = .autoupdatingCurrent) {
        title = order.symbol.isEmpty ? "—" : stockDisplayTitle(order.symbol)
        subtitle = Self.subtitleText(order)
        value = Self.figure(order)
        isQuiet = Self.isClosed(order.status) && !order.hasFill
        let status = Self.statusText(order)
        if let when = Self.whenText(Self.timestamp(order), now: now, calendar: calendar) {
            detail = "\(status) · \(when)"
        } else {
            detail = status
        }
    }

    // MARK: Pieces

    /// "stop_limit" → "Stop limit".
    static func sentenceCase(_ raw: String) -> String {
        let words = raw.replacingOccurrences(of: "_", with: " ")
            .trimmingCharacters(in: .whitespacesAndNewlines)
            .lowercased()
        guard let first = words.first else { return "" }
        return first.uppercased() + words.dropFirst()
    }

    static func sideAndType(_ o: BrokerOrder) -> String? {
        let parts = [o.side, o.type ?? ""].filter { !$0.isEmpty }.map(sentenceCase)
        return parts.isEmpty ? nil : parts.joined(separator: " · ")
    }

    /// The left subline: side · type · size.
    static func subtitleText(_ o: BrokerOrder) -> String? {
        let parts = [sideAndType(o), sizeText(o)].compactMap { $0 }
        return parts.isEmpty ? nil : parts.joined(separator: " · ")
    }

    /// The web's `qtyNumber`: 0 decimals at or above 100, 2 under 100, 4
    /// under 10, trailing zeros trimmed: `150`, `22.4`, `0.4925`, `3`.
    static func qtyNumber(_ q: Double) -> String {
        let magnitude = abs(q)
        let digits = magnitude >= 100 ? 0 : (magnitude >= 10 ? 2 : 4)
        var s = DartNumberFormat.toStringAsFixed(q, digits)
        if s.contains(".") {
            while s.hasSuffix("0") { s.removeLast() }
            if s.hasSuffix(".") { s.removeLast() }
        }
        return s
    }

    /// The order's size, as the web's `orderQtyLabel`: `3 sh`, `2 ct` for an
    /// option, `1 of 3 sh` while a partial fill is still working. A dollar
    /// order shows its amount, `$250.00`, until shares fill, then the filled
    /// shares. nil when there is nothing to say.
    static func sizeText(_ o: BrokerOrder) -> String? {
        let unit = o.isOption ? "ct" : "sh"
        let filled = o.filledQty ?? 0
        guard let qty = o.qty else {
            if filled > 0 { return "\(qtyNumber(filled)) \(unit)" }
            return o.notional.map { fmtMoney($0) }
        }
        if filled > 0, filled < qty, !isClosed(o.status) {
            return "\(qtyNumber(filled)) of \(qtyNumber(qty)) \(unit)"
        }
        return "\(qtyNumber(qty)) \(unit)"
    }

    static func figure(_ o: BrokerOrder) -> String {
        if o.hasFill, let avg = o.filledAvgPrice, let filled = o.filledQty {
            return fmtMoney(avg * filled * o.multiplier)
        }
        if let price = o.limitPrice ?? o.stopPrice { return fmtMoney(price) }
        return o.type == "trailing_stop" ? "Trailing" : "Market"
    }

    /// Closed for good: nothing more will fill.
    static func isClosed(_ status: String) -> Bool {
        ["filled", "canceled", "cancelled", "expired", "rejected", "replaced"].contains(status)
    }

    /// "4 of 10", or "4" when the order has no share quantity; numbers as
    /// the size prints them.
    static func filledOf(_ o: BrokerOrder) -> String {
        let filled = qtyNumber(o.filledQty ?? 0)
        guard let qty = o.qty else { return filled }
        return "\(filled) of \(qtyNumber(qty))"
    }

    /// A plain status word, sentence case.
    static func statusText(_ o: BrokerOrder) -> String {
        switch o.status {
        case "filled":
            return "Filled"
        case "partially_filled":
            // How much has filled is the size, on the left.
            return "Partially filled"
        case "canceled", "cancelled":
            return o.hasFill ? "Canceled, \(filledOf(o)) filled" : "Canceled"
        case "expired":
            return o.hasFill ? "Expired, \(filledOf(o)) filled" : "Expired"
        case "rejected":
            return "Rejected"
        case "pending_cancel":
            return "Canceling"
        case "pending_replace":
            return "Replacing"
        case "", "new", "accepted", "pending_new", "accepted_for_bidding", "calculated", "stopped":
            return "Open"
        default:
            // held, replaced, done_for_day, suspended, and anything newer.
            return sentenceCase(o.status)
        }
    }

    /// The moment the status line is about: the fill, the cancel, else the
    /// submission.
    static func timestamp(_ o: BrokerOrder) -> Date? {
        switch o.status {
        case "filled", "partially_filled": o.filledAt ?? o.submittedAt
        case "canceled", "cancelled", "pending_cancel": o.canceledAt ?? o.submittedAt
        default: o.submittedAt
        }
    }

    /// "Just now", "5m ago", "2h ago"; after a day, "Nov 3", or "Nov 3, 2025"
    /// in an earlier year.
    static func whenText(_ date: Date?, now: Date, calendar: Calendar) -> String? {
        guard let date else { return nil }
        let seconds = now.timeIntervalSince(date)
        if seconds < 60 { return "Just now" }
        if seconds < 3600 { return "\(Int(seconds / 60))m ago" }
        if seconds < 86_400 { return "\(Int(seconds / 3600))h ago" }
        var style = Date.FormatStyle(
            date: .omitted,
            time: .omitted,
            locale: Locale(identifier: "en_US_POSIX"),
            calendar: calendar,
            timeZone: calendar.timeZone
        )
        .month(.abbreviated)
        .day()
        if calendar.component(.year, from: date) != calendar.component(.year, from: now) {
            style = style.year()
        }
        return date.formatted(style)
    }
}
