import SwiftUI

/// The More tab — the Flutter "More" sheet (`more_sheet.dart`) as a real tab
/// with a list: the same nine destinations in the same order on one card,
/// each led by a muted line glyph (the web sidebar's), then the account card.
struct MoreTabView: View {
    @Environment(AppServices.self) private var services

    /// Material symbol name (kept for traceability), label, route, then the
    /// SF Symbol drawn as a muted outline (the web sidebar's line icons).
    /// Circled symbols drop the circle; Backtests takes a line chart, since
    /// `chart.bar.xaxis` draws solid bars beside the outlines.
    private static let items: [(icon: String, label: String, route: Route, tile: String)] = [
        ("currency_bitcoin", "Crypto", .crypto, "bitcoinsign"),
        ("analytics", "Backtests", .backtests, "chart.line.uptrend.xyaxis"),
        ("account_balance", "Brokerages", .brokerages, Symbol.named("account_balance")),
        ("smart_toy", "Agent runs", .agentRuns, Symbol.named("smart_toy")),
        ("hub", "Nexus graph", .nexus, Symbol.named("hub")),
        ("lightbulb", "Learning", .learning, Symbol.named("lightbulb")),
        ("psychology", "Models", .models, Symbol.named("psychology")),
        ("payments", "Token usage", .tokenUsage, Symbol.named("payments")),
        ("settings", "Settings", .settings, Symbol.named("settings")),
    ]

    var body: some View {
        List {
            DSSection {
                ForEach(Self.items, id: \.label) { item in
                    NavigationLink(value: item.route) {
                        Label {
                            Text(item.label)
                                .foregroundStyle(DS.Text.primary)
                        } icon: {
                            SettingsIconTile(systemImage: item.tile)
                        }
                    }
                }
            }

            DSSection("Account") {
                Label {
                    Text(services.session.username)
                        .foregroundStyle(DS.Text.primary)
                } icon: {
                    // Dart: a CircleAvatar with `person_outline`. Sized to the
                    // glyph column so the rows line up.
                    MoreAvatar()
                }
                InlineActionRow("Sign out", systemImage: Symbol.named("logout"), role: .destructive) {
                    Task { await services.session.clear() }
                }
            }
        }
        .dsList()
        .navigationTitle("More")
    }
}

/// The account row's glyph, in the `SettingsIconTile` column.
private struct MoreAvatar: View {
    var body: some View {
        SettingsIconTile(systemImage: "person.crop.circle")
    }
}
