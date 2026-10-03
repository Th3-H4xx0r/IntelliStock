import Foundation
import Observation

/// One instance's detail (`GET /instances/{id}`) and when it arrived. The
/// list endpoint carries no strategy, brokerage or uptime; the detail does.
nonisolated struct InstanceRowDetail: Hashable, Sendable {
    let instance: Instance
    let fetchedAt: Date
}

/// What the Instances rows and the instance hero show beyond the list's own
/// fields, cached for the session (it lives on `AppServices`):
///
/// - **Details:** each instance's strategy name and lanes, its brokerage's
///   paper flag and its uptime, one `Task` per instance.
/// - **Accounts:** each linked account's equity, today's change and 1D curve,
///   through the dashboard's own per-account fetch (`DashboardPortfoliosModel`,
///   one `Task` per account).
///
/// Nothing here blocks the list: rows draw from the list at once and fill in
/// as each answer lands. A failure keeps what a row showed before.
@Observable
final class InstanceRowsModel {
    typealias FetchDetail = @Sendable (String) async throws -> Instance

    /// A second refresh inside this window is skipped unless forced (a pull).
    static let maxAge: TimeInterval = 60

    /// Instance id → its last detail.
    private(set) var details: [String: InstanceRowDetail] = [:]
    /// Linked accounts' figures.
    private(set) var accounts: DashboardPortfoliosModel

    @ObservationIgnored private var detailsInFlight: Set<String> = []
    @ObservationIgnored private var accountsFetchedAt: [String: Date] = [:]
    @ObservationIgnored private let detailFetcher: () -> FetchDetail
    @ObservationIgnored private let accountFetcher: () -> DashboardPortfoliosModel.Fetch
    @ObservationIgnored private let now: () -> Date

    init(
        detailFetcher: @escaping () -> FetchDetail,
        accountFetcher: @escaping () -> DashboardPortfoliosModel.Fetch,
        now: @escaping () -> Date = { Date() }
    ) {
        self.detailFetcher = detailFetcher
        self.accountFetcher = accountFetcher
        self.now = now
        self.accounts = DashboardPortfoliosModel(fetcher: accountFetcher)
    }

    /// The instance as a row shows it: the list's own run state over the
    /// detail's strategy, brokerage and uptime.
    func merged(_ inst: Instance) -> Instance {
        guard let detail = details[inst.id]?.instance else { return inst }
        var out = inst
        // A detail from before a relink describes the old strategy or account.
        if out.strategy == nil, detail.strategyId == inst.strategyId { out.strategy = detail.strategy }
        if out.brokerage == nil, detail.brokerageId == inst.brokerageId { out.brokerage = detail.brokerage }
        if out.uptimeSeconds == nil { out.uptimeSeconds = detail.uptimeSeconds }
        return out
    }

    /// Seconds the instance has been up, counted on from its detail, or nil
    /// when it is not running.
    func uptime(_ inst: Instance) -> Int? {
        let detail = details[inst.id]
        return instanceLiveUptime(
            running: inst.runCommand && !inst.crashed,
            detail: detail?.instance,
            fetchedAt: detail?.fetchedAt,
            now: now()
        )
    }

    /// Fetches what is missing or older than `maxAge` (everything when
    /// `force`), all at once, applying each answer as it arrives.
    func refresh(_ instances: [Instance], brokerages: [BrokerageAccount]?, force: Bool = false) async {
        let accountTask = Task { await refreshAccounts(instanceRowAccounts(instances, brokerages: brokerages), force: force) }
        await refreshDetails(instances.map(\.id), force: force)
        await accountTask.value
    }

    /// `refresh` in a task of its own, so leaving the screen cancels nothing.
    func refreshDetached(_ instances: [Instance], brokerages: [BrokerageAccount]?, force: Bool = false) {
        Task { await refresh(instances, brokerages: brokerages, force: force) }
    }

    /// Fetches the accounts not fetched within `maxAge` (all when `force`).
    func refreshAccounts(_ due: [BrokerageAccount], force: Bool = false) async {
        let at = now()
        let stale = due.filter { account in
            guard !force, let last = accountsFetchedAt[account.id] else { return true }
            return at.timeIntervalSince(last) >= Self.maxAge
        }
        guard !stale.isEmpty else { return }
        for account in stale { accountsFetchedAt[account.id] = at }
        await accounts.refresh(stale)
    }

    private func refreshDetails(_ ids: [String], force: Bool) async {
        let at = now()
        var seen = Set<String>()
        let due = ids.filter { id in
            guard !id.isEmpty, !detailsInFlight.contains(id), seen.insert(id).inserted else { return false }
            guard !force, let last = details[id]?.fetchedAt else { return true }
            return at.timeIntervalSince(last) >= Self.maxAge
        }
        guard !due.isEmpty else { return }
        detailsInFlight.formUnion(due)
        let fetch = detailFetcher()
        // One task per instance rather than a task group: a task-group child
        // calling a closure-typed fetcher crashed the Release build
        // (`DashboardPortfoliosModel.refresh`).
        let tasks = due.map { id in
            Task { [weak self] in
                let result: Instance?
                do {
                    result = try await fetch(id)
                } catch {
                    result = nil
                }
                self?.applyDetail(id, result)
            }
        }
        for task in tasks {
            await task.value
        }
    }

    private func applyDetail(_ id: String, _ instance: Instance?) {
        detailsInFlight.remove(id)
        // A failure keeps the last detail; the next refresh tries again.
        guard let instance else { return }
        details[id] = InstanceRowDetail(instance: instance, fetchedAt: now())
    }

    /// A sign-out or a server change: nothing cached may show.
    func reset() {
        details = [:]
        detailsInFlight = []
        accountsFetchedAt = [:]
        accounts = DashboardPortfoliosModel(fetcher: accountFetcher)
    }
}

// MARK: - Row text

/// The accounts to fetch for these instances, once each: the loaded
/// brokerage when the list has it, else a bare account (any type but Kalshi
/// reads the portfolio history, which is what an equity instance trades).
nonisolated func instanceRowAccounts(_ instances: [Instance], brokerages: [BrokerageAccount]?) -> [BrokerageAccount] {
    var seen = Set<String>()
    var out: [BrokerageAccount] = []
    for inst in instances {
        guard let id = inst.brokerageId, !id.isEmpty, seen.insert(id).inserted else { continue }
        out.append(brokerages?.first(where: { $0.id == id })
            ?? BrokerageAccount(id: id, accountName: "", brokerageType: "", status: ""))
    }
    return out
}

/// Uptime counted on from the detail's figure, or nil when the instance is
/// not running (by the list, which polls) or the detail has no figure.
nonisolated func instanceLiveUptime(running: Bool, detail: Instance?, fetchedAt: Date?, now: Date) -> Int? {
    guard running, let detail, detail.runCommand, let up = detail.uptimeSeconds, let fetchedAt else { return nil }
    return up + max(0, Int(now.timeIntervalSince(fetchedAt)))
}

/// Uptime to the minute: "3d 4h", "6h 12m", "45m", "Under a minute".
nonisolated func instanceUptimeShort(_ secs: Int) -> String {
    let d = secs / 86_400
    let h = (secs % 86_400) / 3600
    let m = (secs % 3600) / 60
    if d > 0 { return "\(d)d \(h)h" }
    if h > 0 { return "\(h)h \(m)m" }
    if m > 0 { return "\(m)m" }
    return "Under a minute"
}

/// What the instance trades: "Crypto", "Kalshi", "Swing" (a swing or wheel
/// lane) or "Equity". nil while a linked strategy is not loaded yet, so the
/// tag never flips from Equity to Swing; `detailLoaded` says the detail came
/// back without one (a deleted strategy), which reads as Equity.
nonisolated func instanceKindLabel(_ inst: Instance, detailLoaded: Bool = false) -> String? {
    switch inst.kind?.lowercased() {
    case "crypto": return "Crypto"
    case "kalshi": return "Kalshi"
    default: break
    }
    if inst.strategyId != nil, inst.strategy == nil, !detailLoaded { return nil }
    return swingLanesOf(inst.strategy).any ? "Swing" : "Equity"
}

/// Paper (true) or live (false) money, from the nested brokerage or the
/// loaded account; nil when the app cannot tell.
nonisolated func instanceIsPaper(_ inst: Instance, brokerages: [BrokerageAccount]?) -> Bool? {
    guard let id = inst.brokerageId, !id.isEmpty else { return nil }
    if case .bool(let paper)? = inst.brokerage?["alpaca_paper"] { return paper }
    guard let account = brokerages?.first(where: { $0.id == id }) else { return nil }
    switch account.brokerageType.lowercased() {
    case "alpaca": return account.alpacaPaper
    case "kalshi": return account.kalshiEnvironment.isEmpty ? nil : account.kalshiEnvironment.lowercased() != "live"
    default: return nil
    }
}

/// The row's quiet second line: the uptime while it runs, then the kind.
/// "6h 12m · Swing", "Equity", "".
nonisolated func instanceRowMeta(uptime: Int?, kind: String?) -> String {
    var parts: [String] = []
    if let uptime { parts.append(instanceUptimeShort(uptime)) }
    if let kind { parts.append(kind) }
    return parts.joined(separator: " · ")
}

/// Today's change in the holdings rows' format, "+$12.34 · +0.07%", or nil
/// without a change.
nonisolated func instanceRowChangeText(_ summary: DashboardAccountSummary) -> String? {
    guard let change = summary.dayChange else { return nil }
    return "\(fmtPnl(change)) · \(fmtPct(summary.dayChangePct))"
}
