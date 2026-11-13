import Foundation
import Observation

/// One trade ticket, from the first field to the swipe that sends it: the
/// account, what is held there, the fields, the check, and the submit.
///
/// The ticket gets one `client_order_id` for its whole life, so a retried
/// swipe after an unconfirmed send cannot place a second order (Alpaca
/// refuses a repeated id). Nothing is retried automatically, and a placed
/// ticket never sends again.
@Observable
final class TradeTicketModel {
    let symbol: String
    /// Tradable (Alpaca) accounts, in the dashboard's order.
    let accounts: [BrokerageAccount]
    let clientOrderId: String

    /// The fields. An edit clears a failed submit's message: it was about
    /// the order before the edit.
    var draft: TradeDraft {
        didSet {
            if draft != oldValue, result?.order == nil { result = nil }
        }
    }

    private(set) var accountId: String
    /// Each account's quantity of `symbol` (0 when none is held).
    private(set) var held: [String: Loadable<Double>] = [:]
    /// nil until loaded; a failed load leaves it nil (the line is advisory).
    private(set) var instances: [Instance]?
    private(set) var submitting = false
    /// The last submit's outcome.
    private(set) var result: TradeSubmitResult?

    @ObservationIgnored private let backend: () -> any TradeBackend
    @ObservationIgnored private let price: () -> Double?
    @ObservationIgnored private let onOrdersChanged: (String) -> Void

    init(
        symbol: String,
        side: TradeSide,
        accounts: [BrokerageAccount],
        accountId: String,
        lastPrice: @escaping () -> Double?,
        backend: @escaping () -> any TradeBackend,
        clientOrderId: String = TradeTicket.newClientOrderId(),
        onOrdersChanged: @escaping (String) -> Void = { TradeEvents.postOrdersChanged($0) }
    ) {
        self.symbol = symbol.uppercased()
        self.accounts = accounts
        self.accountId = accountId
        self.clientOrderId = clientOrderId
        self.draft = TradeDraft(symbol: symbol.uppercased(), side: side)
        self.price = lastPrice
        self.backend = backend
        self.onOrdersChanged = onOrdersChanged
    }

    // MARK: What the ticket shows

    var account: BrokerageAccount? { accounts.first { $0.id == accountId } }

    var heldState: Loadable<Double>? { held[accountId] }

    var heldQty: Double? { held[accountId]?.value }

    var lastPrice: Double? { price() }

    var check: TradeCheck {
        TradeTicket.check(draft, held: heldQty, clientOrderId: clientOrderId)
    }

    var estimate: Double? { TradeTicket.estimate(draft, lastPrice: lastPrice) }

    var instanceWarning: String? {
        instances.flatMap { TradeTicket.instanceWarning($0, accountId: accountId) }
    }

    /// "Sell all" is offered: a sell, with something held.
    var canSellAll: Bool { draft.side == .sell && (heldQty ?? 0) > 0 }

    var isPlaced: Bool { result?.order != nil }

    // MARK: Edits

    func selectAccount(_ id: String) {
        guard id != accountId, accounts.contains(where: { $0.id == id }) else { return }
        accountId = id
        if result?.order == nil { result = nil }
    }

    /// Switching between shares and dollars clears the amount: "3" means
    /// something else in the other unit.
    func setAmountKind(_ kind: TradeAmountKind) {
        guard kind != draft.amountKind else { return }
        draft.amountKind = kind
        draft.amountText = ""
    }

    /// Fills the exact held quantity, in shares.
    func sellAll() {
        guard let qty = heldQty, qty > 0 else { return }
        draft.side = .sell
        draft.amountKind = .shares
        draft.amountText = TradeTicket.exactQtyText(qty)
    }

    // MARK: Loading

    /// The selected account's quantity, unless already known (`force` asks
    /// again). A failure shows only while nothing is known.
    func loadHeld(force: Bool = false) async {
        let id = accountId
        if !force, held[id]?.value != nil { return }
        if held[id]?.value == nil { held[id] = .loading }
        do {
            let holdings = try await backend().holdings(id)
            let qty = holdings.positions.first { $0.symbol.uppercased() == symbol }?.qty ?? 0
            held[id] = .loaded(qty)
        } catch {
            if error.isCancellationOrTaskCancelled { return }
            if held[id]?.value == nil { held[id] = .failed(error) }
        }
    }

    func loadInstances() async {
        guard instances == nil else { return }
        if let list = try? await backend().instances() { instances = list }
    }

    // MARK: Submit

    /// What the review shows and sends: the checked request and its account,
    /// captured when Review is tapped. nil while the ticket can't be sent.
    var reviewItem: TradeReviewItem? {
        guard let request = check.request, let account else { return nil }
        return TradeReviewItem(request: request, account: account)
    }

    /// Sends the reviewed order once. A second call while one runs, or
    /// after a placed order, sends nothing; so does a request from another
    /// ticket. A placed or unconfirmed order tells the dashboard to reload
    /// that account.
    @discardableResult
    func submit(_ item: TradeReviewItem) async -> TradeSubmitResult? {
        if let result, result.order != nil { return result }
        guard !submitting, item.request.clientOrderId == clientOrderId else { return nil }
        submitting = true
        defer { submitting = false }
        let id = item.account.id
        let request = item.request
        let outcome: TradeSubmitResult
        do {
            outcome = .placed(try await backend().placeOrder(id, request))
        } catch where error.isCancellationOrTaskCancelled {
            outcome = .unconfirmed("The request was interrupted. \(TradeTicket.unconfirmedTail)")
        } catch {
            outcome = TradeTicket.submitResult(for: error)
        }
        result = outcome
        switch outcome {
        case .placed:
            held[id] = nil
            onOrdersChanged(id)
        case .unconfirmed:
            onOrdersChanged(id)
        case .rejected:
            break
        }
        return outcome
    }
}

/// The order on the review screen: exactly what the swipe sends.
nonisolated struct TradeReviewItem: Hashable, Sendable {
    let request: TradeOrderRequest
    let account: BrokerageAccount
}

/// An order on one of the user's accounts.
nonisolated struct AccountOrder: Hashable, Sendable, Identifiable {
    let account: BrokerageAccount
    let order: BrokerOrder

    var id: String { "\(account.id)/\(order.id)" }
}

/// The stock screen's own read of the position after a trade: the route's
/// position is a snapshot from the dashboard.
nonisolated struct LivePosition: Hashable, Sendable {
    let accountId: String
    /// nil when the account no longer holds the symbol.
    let position: AccountPosition?
    /// Cash plus every position's value, for the allocation ring.
    let total: Double
}

/// A cancel waiting on its confirmation.
struct TradeCancelPrompt: Identifiable {
    let id = UUID()
    let brokerageId: String
    let order: BrokerOrder
    let message: String
}

/// The stock screen's trading state: the symbol's open orders on every
/// tradable account (each cancelable behind a confirm), and the position
/// re-read after a trade.
@Observable
final class StockTradeModel {
    let symbol: String
    /// nil until the first load lands.
    private(set) var openOrders: [AccountOrder]?
    /// Every account failed on the first load.
    private(set) var openOrdersFailed = false
    private(set) var livePosition: LivePosition?
    private(set) var canceling: Set<String> = []
    /// The cancel's failure, shown in the section.
    var cancelError: String?
    var cancelPrompt: TradeCancelPrompt?
    var toast: Toast?

    @ObservationIgnored private let backend: () -> any TradeBackend
    @ObservationIgnored private let onOrdersChanged: (String) -> Void
    @ObservationIgnored private let sleep: (Duration) async -> Void
    @ObservationIgnored private var accounts: [BrokerageAccount] = []

    init(
        symbol: String,
        backend: @escaping () -> any TradeBackend,
        onOrdersChanged: @escaping (String) -> Void = { TradeEvents.postOrdersChanged($0) },
        sleep: @escaping (Duration) async -> Void = { try? await Task.sleep(for: $0) }
    ) {
        self.symbol = symbol.uppercased()
        self.backend = backend
        self.onOrdersChanged = onOrdersChanged
        self.sleep = sleep
    }

    // MARK: Open orders

    /// The symbol's working orders on each account, newest first. Accounts
    /// are read one after another (there are a handful). An account that
    /// fails is skipped; only a load where every account failed, with
    /// nothing shown yet, reads as failed.
    func loadOpenOrders(_ accounts: [BrokerageAccount]) async {
        self.accounts = accounts
        var found: [AccountOrder] = []
        var failures = 0
        for account in accounts {
            do {
                let open = try await backend().openOrders(account.id)
                found += open
                    .filter { $0.symbol.uppercased() == symbol }
                    .map { AccountOrder(account: account, order: $0) }
            } catch {
                if error.isCancellationOrTaskCancelled { return }
                failures += 1
            }
        }
        if !accounts.isEmpty, failures == accounts.count {
            if openOrders == nil { openOrdersFailed = true }
            return
        }
        openOrdersFailed = false
        openOrders = found.sorted { ($0.order.submittedAt ?? .distantPast) > ($1.order.submittedAt ?? .distantPast) }
    }

    func reloadOpenOrders() async {
        await loadOpenOrders(accounts)
    }

    // MARK: Cancel

    func requestCancel(_ item: AccountOrder) {
        guard TradeTicket.isCancelable(item.order), !canceling.contains(item.id) else { return }
        cancelPrompt = TradeCancelPrompt(
            brokerageId: item.account.id,
            order: item.order,
            message: TradeTicket.cancelPromptBody(item.order, account: item.account)
        )
    }

    /// Cancels a REAL order (after its confirm), then re-reads the list.
    func cancel(brokerageId: String, orderId: String) async {
        let key = "\(brokerageId)/\(orderId)"
        guard !canceling.contains(key) else { return }
        canceling.insert(key)
        defer { canceling.remove(key) }
        cancelError = nil
        do {
            try await backend().cancelOrder(brokerageId, orderId: orderId)
            toast = Toast("Cancel sent to Alpaca", style: .success)
            onOrdersChanged(brokerageId)
        } catch {
            if error.isCancellation { return }
            cancelError = TradeTicket.cancelErrorText(error)
        }
        await reloadOpenOrders()
    }

    // MARK: After a trade

    /// Re-reads the account's position for the screen.
    func loadLivePosition(_ accountId: String) async {
        guard let holdings = try? await backend().holdings(accountId) else { return }
        let total = (holdings.cash ?? 0) + holdings.positions.reduce(0) { $0 + $1.marketValue }
        livePosition = LivePosition(
            accountId: accountId,
            position: holdings.positions.first { $0.symbol.uppercased() == symbol },
            total: total
        )
    }

    /// After an order on `accountId`: the open orders and, when the screen
    /// shows that account's position (or none), the position; then once
    /// more a few seconds later, when a market order has usually filled.
    func refreshAfterTrade(accountId: String, routeAccountId: String?) async {
        let readsPosition = routeAccountId == nil || routeAccountId == accountId
        for pass in 0..<2 {
            if pass > 0 { await sleep(.seconds(3)) }
            if Task.isCancelled { return }
            await reloadOpenOrders()
            if readsPosition { await loadLivePosition(accountId) }
        }
    }
}
