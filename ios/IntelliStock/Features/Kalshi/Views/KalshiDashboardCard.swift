import Observation
import SwiftUI

/// Compact Kalshi glance for the dashboard — `KalshiDashboardCard` in
/// `kalshi_dashboard_card.dart` — as a "Kalshi" list section with one row:
/// the account's name and open positions, its value and day change. The
/// row (the Dart card and its "Open" button alike) selects the Kalshi tab.
/// The dashboard shows it only when a Kalshi account is linked, and runs
/// the fetch (`KalshiDashboardCardModel.load`).
struct KalshiDashboardCard: View {
    let account: BrokerageAccount
    let model: KalshiDashboardCardModel

    @Environment(AppServices.self) private var services

    var body: some View {
        Section {
            Button {
                services.router.go("/kalshi")
            } label: {
                HStack(spacing: 12) {
                    EntityRow(
                        account.accountName,
                        subtitle: KalshiDashboardCard.positionsText(model.positions ?? 0)
                    ) {
                        value
                    }
                    Image(systemName: "chevron.forward")
                        .font(.footnote.weight(.semibold))
                        .foregroundStyle(.tertiary)
                        .accessibilityHidden(true)
                }
                .foregroundStyle(Color.primary)
                .contentShape(Rectangle())
            }
            .accessibilityHint("Opens the Kalshi tab")
        } header: {
            DashboardGroupHeader(group: "Kalshi")
        }
    }

    @ViewBuilder
    private var value: some View {
        switch model.portfolio {
        case .loading:
            EntityRowValue("$000.00", detail: "+$0.00")
                .redacted(reason: .placeholder)
                .accessibilityLabel("Loading…")
        case .failed:
            EntityRowValue("—", color: .secondary)
        case .loaded(let p):
            EntityRowValue(
                KalshiDashboardCard.valueText(p.value),
                detail: KalshiDashboardCard.dayChangeText(p.dayChange),
                detailColor: ChangeDirection(p.dayChange).color
            )
        }
    }

    /// `$12.34` (2 dp, no grouping), as the Dart card printed it.
    static func valueText(_ value: Double) -> String {
        "$\(dartToStringAsFixed(value, 2))"
    }

    /// `+$1.23` / `-$1.23`.
    static func dayChangeText(_ change: Double) -> String {
        "\(change >= 0 ? "+" : "-")$\(dartToStringAsFixed(abs(change), 2))"
    }

    /// `N open position(s)`.
    static func positionsText(_ count: Int) -> String {
        "\(count) open position\(count == 1 ? "" : "s")"
    }
}

/// The Kalshi glance's data: the account's portfolio and its open-position
/// count, fetched once per account (the Dart providers kept their data alive
/// after a successful fetch). A new account starts over.
@Observable
final class KalshiDashboardCardModel {
    private(set) var accountId: String?
    private(set) var portfolio: Loadable<KalshiPortfolio> = .loading
    private(set) var positions: Int?

    /// Fetches what `accountId` is missing; a failed portfolio is retried on
    /// the next call.
    func load(_ accountId: String, repository repo: KalshiRepository) async {
        if self.accountId != accountId {
            self.accountId = accountId
            portfolio = .loading
            positions = nil
        }
        if portfolio.value == nil {
            do {
                let p = try await repo.portfolio(accountId)
                guard self.accountId == accountId else { return }
                portfolio = .loaded(p)
            } catch {
                if !error.isCancellationOrTaskCancelled, self.accountId == accountId { portfolio = .failed(error) }
            }
        }
        if positions == nil, let list = try? await repo.positions(accountId), self.accountId == accountId {
            positions = list.count
        }
    }
}
