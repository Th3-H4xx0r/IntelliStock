import Foundation
import WidgetKit

/// Writes widget data to the App Group container and reloads the WidgetKit
/// timelines — `WidgetSyncService` in `widget_sync_service.dart`, plus the
/// credential mirror `SessionStore._syncWidgetCreds` used.
///
/// The keys and JSON shapes are the contract `PortfolioWidget.swift` reads, so
/// they must not change. Every write is best-effort: a widget failure never
/// reaches the user.
final class WidgetSync {
    static let appGroup = "group.dev.pkrishna.intellistock"

    /// App Group keys (must match the widget extension).
    nonisolated enum Key {
        /// JSON `WidgetPortfolio`.
        static let portfolio = "portfolio_data"
        /// JSON `[WidgetPosition]`.
        static let positions = "positions_data"
        /// JSON `[WidgetInstance]`.
        static let instances = "instances_data"
        /// JSON `[WidgetAccount]` — the selectable portfolios.
        static let accounts = "accounts_data"
        /// Epoch seconds of the last accounts sync (Int).
        static let syncedAt = "synced_at"
        /// The API origin the widget self-fetches from.
        static let apiBase = "widget_api_base"
        /// The bearer token the widget self-fetches with.
        static let token = "widget_token"
    }

    /// Widget kinds (the `kind:` of each `StaticConfiguration`).
    nonisolated enum Kind {
        static let portfolio = "PortfolioWidget"
        static let instance = "InstanceWidget"
    }

    private let defaults: UserDefaults?
    private let reload: (String) -> Void
    private let now: () -> Date

    init(
        defaults: UserDefaults? = UserDefaults(suiteName: WidgetSync.appGroup),
        reload: @escaping (String) -> Void = { WidgetCenter.shared.reloadTimelines(ofKind: $0) },
        now: @escaping () -> Date = Date.init
    ) {
        self.defaults = defaults
        self.reload = reload
        self.now = now
    }

    /// Persists every section of `payload`, then reloads both widget kinds.
    func sync(_ payload: WidgetPayload) {
        guard let defaults else { return }
        set(payload.portfolio.toJSON(), forKey: Key.portfolio, in: defaults)
        set(.array(payload.positions.map { $0.toJSON() }), forKey: Key.positions, in: defaults)
        set(.array(payload.instances.map { $0.toJSON() }), forKey: Key.instances, in: defaults)
        reload(Kind.portfolio)
        reload(Kind.instance)
    }

    /// Writes the selectable portfolios plus the first as the primary
    /// fallback, stamps `synced_at`, then reloads the portfolio widget.
    func syncAccounts(_ accounts: [WidgetAccount]) {
        guard let defaults else { return }
        set(.array(accounts.map { $0.toJSON() }), forKey: Key.accounts, in: defaults)
        if let first = accounts.first {
            set(first.toPortfolioJSON(), forKey: Key.portfolio, in: defaults)
        }
        defaults.set(Int(now().timeIntervalSince1970), forKey: Key.syncedAt)
        reload(Kind.portfolio)
    }

    /// Shares the API base and token with the widget so it can self-refresh
    /// `/widget/accounts` while the app is closed, then reloads it so a fresh
    /// login shows immediately.
    func syncCredentials(apiBase: String, token: String) {
        guard let defaults else { return }
        defaults.set(apiBase, forKey: Key.apiBase)
        defaults.set(token, forKey: Key.token)
        reload(Kind.portfolio)
    }

    private func set(_ json: JSON, forKey key: String, in defaults: UserDefaults) {
        guard let data = try? json.data() else { return }
        defaults.set(String(decoding: data, as: UTF8.self), forKey: key)
    }
}
