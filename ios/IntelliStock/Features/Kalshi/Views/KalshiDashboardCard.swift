import SwiftUI

/// Compact Kalshi glance card for the dashboard — `KalshiDashboardCard` in
/// `kalshi_dashboard_card.dart`. Hidden when no Kalshi account is linked;
/// "Open" and the card select the Kalshi tab.
struct KalshiDashboardCard: View {
    @Environment(AppServices.self) private var services

    var body: some View {
        let accounts = services.dashboard.brokeragesValue ?? []
        if let kalshi = accounts.first(where: { $0.brokerageType == "kalshi" }) {
            KalshiDashboardCardContent(account: kalshi)
                .id(kalshi.id)
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

private struct KalshiDashboardCardContent: View {
    let account: BrokerageAccount

    @Environment(AppServices.self) private var services
    @State private var portfolio: Loadable<KalshiPortfolio> = .loading
    @State private var positions: Int?

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            HStack(spacing: 8) {
                Image(systemName: Symbol.named("sports_soccer"))
                    .font(.title3)
                    .foregroundStyle(.tint)
                    .accessibilityHidden(true)
                Text("Kalshi")
                    .font(.title3.bold())
                    .accessibilityAddTraits(.isHeader)
                Spacer()
                Button {
                    services.router.go("/kalshi")
                } label: {
                    HStack(spacing: 2) {
                        Text("Open")
                        Image(systemName: Symbol.named("arrow_forward"))
                    }
                    .font(.footnote.weight(.semibold))
                }
                .buttonStyle(.borderless)
            }

            Button {
                services.router.go("/kalshi")
            } label: {
                Card(padding: 16) {
                    VStack(alignment: .leading, spacing: 8) {
                        Text(account.accountName.uppercased())
                            .font(.footnote.weight(.bold))
                            .tracking(1.2)
                            .foregroundStyle(.tint)
                        value
                        Text(KalshiDashboardCard.positionsText(positions ?? 0))
                            .font(.footnote)
                            .foregroundStyle(.secondary)
                    }
                }
            }
            .buttonStyle(.plain)
            .accessibilityHint("Opens the Kalshi tab")
        }
        .task {
            // Fetched once per card (the Dart providers kept their data alive
            // after a successful fetch).
            let repo = services.kalshiRepository
            let bid = account.id
            if portfolio.value == nil {
                do {
                    portfolio = .loaded(try await repo.portfolio(bid))
                } catch {
                    if !tradingIsCancellation(error) { portfolio = .failed(error) }
                }
            }
            if positions == nil, let list = try? await repo.positions(bid) {
                positions = list.count
            }
        }
    }

    @ViewBuilder
    private var value: some View {
        switch portfolio {
        case .loading:
            Text("Loading…")
                .foregroundStyle(.secondary)
        case .failed:
            Text("—")
                .font(.title2.monospacedDigit())
                .foregroundStyle(.secondary)
        case .loaded(let p):
            let positive = p.dayChange >= 0
            let color = positive ? DS.Palette.success : DS.Palette.danger
            HStack(alignment: .firstTextBaseline, spacing: 8) {
                Text(KalshiDashboardCard.valueText(p.value))
                    .font(.title2.weight(.semibold).monospacedDigit())
                HStack(spacing: 2) {
                    Image(systemName: Symbol.named(positive ? "trending_up" : "trending_down"))
                        .accessibilityHidden(true)
                    Text(KalshiDashboardCard.dayChangeText(p.dayChange))
                }
                .font(.footnote.weight(.bold))
                .foregroundStyle(color)
            }
        }
    }
}
