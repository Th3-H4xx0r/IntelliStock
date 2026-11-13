import SwiftUI

/// The leading glyph of a navigation-list row (More, Settings). It used to be
/// a white glyph on a coloured square; the web has no icon tiles or coloured
/// icons, so it is now the plain SF Symbol, 17 pt regular in zinc-500,
/// centred in a column that scales with Dynamic Type so the rows line up.
/// `color` is ignored.
struct SettingsIconTile: View {
    let systemImage: String
    let color: Color

    @ScaledMetric(relativeTo: .body) private var size: CGFloat = 24

    init(systemImage: String, color: Color = DS.Text.muted) {
        self.systemImage = systemImage
        self.color = color
    }

    var body: some View {
        Image(systemName: systemImage)
            .font(.system(size: 17, weight: .regular))
            .foregroundStyle(DS.Text.muted)
            .frame(width: size, height: size)
            .accessibilityHidden(true)
    }
}

#Preview {
    List {
        DSSection {
            Label { Text("Brokerages") } icon: { SettingsIconTile(systemImage: "building.columns", color: .green) }
            Label { Text("Models") } icon: { SettingsIconTile(systemImage: "brain.head.profile", color: .gray) }
            Label { Text("Settings") } icon: { SettingsIconTile(systemImage: "gearshape", color: .gray) }
        }
    }
    .dsList()
    .dsAppearance()
}
