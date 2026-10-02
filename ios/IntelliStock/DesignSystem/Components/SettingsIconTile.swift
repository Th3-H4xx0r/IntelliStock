import SwiftUI

/// The Settings-app row icon: a white, filled glyph on a solid rounded square.
///
/// The spec allows these tiles only in navigation lists (More, Settings), where
/// the colour tells destinations apart at a glance. Everywhere else, rows use
/// `IconTile` or no icon. The tile scales with Dynamic Type like the row's text.
struct SettingsIconTile: View {
    let systemImage: String
    let color: Color

    @ScaledMetric(relativeTo: .body) private var size: CGFloat = 30

    init(systemImage: String, color: Color) {
        self.systemImage = systemImage
        self.color = color
    }

    var body: some View {
        Image(systemName: systemImage)
            .symbolVariant(.fill)
            .font(.system(size: size * 0.52, weight: .semibold))
            .foregroundStyle(.white)
            .frame(width: size, height: size)
            .background(color, in: .rect(cornerRadius: size * 0.24, style: .continuous))
            .accessibilityHidden(true)
    }
}

#Preview {
    List {
        Section {
            Label { Text("Brokerages") } icon: { SettingsIconTile(systemImage: "building.columns", color: .green) }
            Label { Text("Models") } icon: { SettingsIconTile(systemImage: "brain.head.profile", color: .pink) }
            Label { Text("Settings") } icon: { SettingsIconTile(systemImage: "gearshape", color: .gray) }
        }
    }
    .listStyle(.insetGrouped)
}
