import SwiftUI

/// The More tab — the Flutter "More" sheet (`more_sheet.dart`) as a real tab
/// with a list (`tab-bars.md › Best practices`: "Use a tab bar to support
/// navigation, not to provide actions"). Same nine destinations in the same
/// order, then the account row.
struct MoreTabView: View {
    @Environment(AppServices.self) private var services

    /// Material symbol name (kept for traceability), label, route.
    private static let items: [(icon: String, label: String, route: Route)] = [
        ("currency_bitcoin", "Crypto", .crypto),
        ("analytics", "Backtests", .backtests),
        ("account_balance", "Brokerages", .brokerages),
        ("smart_toy", "Agent Runs", .agentRuns),
        ("hub", "Nexus Graph", .nexus),
        ("lightbulb", "Learning", .learning),
        ("psychology", "Models", .models),
        ("payments", "Token Usage", .tokenUsage),
        ("settings", "Settings", .settings),
    ]

    var body: some View {
        List {
            Section {
                ForEach(Self.items, id: \.label) { item in
                    NavigationLink(value: item.route) {
                        Label(item.label, systemImage: Symbol.named(item.icon))
                    }
                }
            }

            Section {
                Label {
                    Text(services.session.username)
                } icon: {
                    // Dart: a CircleAvatar with `person_outline`.
                    Image(systemName: "person.crop.circle.fill")
                        .foregroundStyle(.secondary)
                }
                Button(role: .destructive) {
                    Task { await services.session.clear() }
                } label: {
                    Label("Sign Out", systemImage: Symbol.named("logout"))
                }
            }
        }
        .listStyle(.insetGrouped)
        .navigationTitle("More")
    }
}
