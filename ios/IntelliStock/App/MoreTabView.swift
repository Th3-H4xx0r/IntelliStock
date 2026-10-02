import SwiftUI

/// The More tab — the Flutter "More" sheet (`more_sheet.dart`) as a real tab
/// with a list (`tab-bars.md › Best practices`: "Use a tab bar to support
/// navigation, not to provide actions"). Same nine destinations in the same
/// order, then the account row.
struct MoreTabView: View {
    @Environment(AppServices.self) private var services

    /// Material symbol name (kept for traceability), label, route, then the
    /// Settings-style tile: its SF Symbol (drawn filled) and colour. Circled
    /// symbols drop the circle, which would sit inside the tile's square.
    private static let items: [(icon: String, label: String, route: Route, tile: String, color: Color)] = [
        ("currency_bitcoin", "Crypto", .crypto, "bitcoinsign", .orange),
        ("analytics", "Backtests", .backtests, Symbol.named("analytics"), .blue),
        ("account_balance", "Brokerages", .brokerages, Symbol.named("account_balance"), .green),
        ("smart_toy", "Agent Runs", .agentRuns, Symbol.named("smart_toy"), .purple),
        ("hub", "Nexus Graph", .nexus, Symbol.named("hub"), .teal),
        ("lightbulb", "Learning", .learning, Symbol.named("lightbulb"), .yellow),
        ("psychology", "Models", .models, Symbol.named("psychology"), .pink),
        ("payments", "Token Usage", .tokenUsage, Symbol.named("payments"), .indigo),
        ("settings", "Settings", .settings, Symbol.named("settings"), .gray),
    ]

    var body: some View {
        List {
            Section {
                ForEach(Self.items, id: \.label) { item in
                    NavigationLink(value: item.route) {
                        Label {
                            Text(item.label)
                        } icon: {
                            SettingsIconTile(systemImage: item.tile, color: item.color)
                        }
                    }
                }
            }

            Section {
                Label {
                    Text(services.session.username)
                } icon: {
                    // Dart: a CircleAvatar with `person_outline`. Sized to the
                    // tile column so the rows line up.
                    MoreAvatar()
                }
                Button(role: .destructive) {
                    Task { await services.session.clear() }
                } label: {
                    Label {
                        Text("Sign Out")
                    } icon: {
                        SettingsIconTile(systemImage: Symbol.named("logout"), color: .red)
                    }
                }
            }
        }
        .listStyle(.insetGrouped)
        .navigationTitle("More")
    }
}

/// The account row's avatar, the size of a `SettingsIconTile`.
private struct MoreAvatar: View {
    @ScaledMetric(relativeTo: .body) private var size: CGFloat = 30

    var body: some View {
        Image(systemName: "person.crop.circle.fill")
            .resizable()
            .scaledToFit()
            .foregroundStyle(.secondary)
            .frame(width: size, height: size)
            .accessibilityHidden(true)
    }
}
