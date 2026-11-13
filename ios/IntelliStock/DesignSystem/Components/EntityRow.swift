import SwiftUI

/// The row for one thing in a list: an instance, a backtest, a brokerage, a
/// model, a holding. Use it as the label of a `NavigationLink` inside a
/// `List`, which adds the chevron and makes the whole row the tap target.
///
/// The web row (`ui-row`):
///
/// - **Leading** (optional): a muted glyph (`IconTile`), a brand logo, or a ring.
/// - **Title:** 14 pt zinc-100, one line. An optional pin glyph follows it.
/// - **Subtitle:** 12 pt zinc-500, 2 pt below, one or two lines.
/// - **Trailing:** a value (`EntityRowValue`, right-aligned, monospaced
///   digits), a `StatusDot`, or one `StatusBadge` (a dot and a word). Never a
///   row of buttons: secondary actions go in `.swipeActions` and `.contextMenu`.
///
/// At accessibility text sizes the trailing slot moves under the text, so
/// nothing truncates (`typography.md › Supporting Dynamic Type`). VoiceOver
/// reads the row as one element.
///
///     NavigationLink(value: Route.instance(inst.id)) {
///         EntityRow(inst.name, subtitle: "Strategy 194 · Swing Trade Paper",
///                   systemImage: "cpu", isPinned: inst.pinned) {
///             StatusDot("Running", color: DS.Palette.success, pulsing: true)
///         }
///     }
struct EntityRow<Leading: View, Trailing: View>: View {
    let title: String
    var subtitle: String?
    /// 1 or 2.
    var subtitleLineLimit: Int
    var isPinned: Bool
    private let leading: Leading
    private let trailing: Trailing

    @Environment(\.dynamicTypeSize) private var dynamicTypeSize
    @Environment(\.dsEntityTitleMedium) private var titleMedium

    /// A row with custom leading and trailing views.
    init(
        _ title: String,
        subtitle: String? = nil,
        subtitleLineLimit: Int = 1,
        isPinned: Bool = false,
        @ViewBuilder leading: () -> Leading,
        @ViewBuilder trailing: () -> Trailing
    ) {
        self.title = title
        self.subtitle = subtitle
        self.subtitleLineLimit = subtitleLineLimit
        self.isPinned = isPinned
        self.leading = leading()
        self.trailing = trailing()
    }

    var body: some View {
        let stacked = dynamicTypeSize.isAccessibilitySize
        let layout = stacked
            ? AnyLayout(VStackLayout(alignment: .leading, spacing: 6))
            : AnyLayout(HStackLayout(alignment: .center, spacing: 12))
        layout {
            HStack(spacing: 12) {
                leading
                VStack(alignment: .leading, spacing: 2) {
                    HStack(alignment: .firstTextBaseline, spacing: 6) {
                        Text(title)
                            .font(titleMedium ? DS.Font.bodyMedium : DS.Font.body)
                            .foregroundStyle(DS.Text.primary)
                            .lineLimit(stacked ? nil : 1)
                        if isPinned {
                            Image(systemName: "pin.fill")
                                .font(.system(size: 10))
                                .foregroundStyle(DS.Text.muted)
                                .accessibilityLabel("Pinned")
                        }
                    }
                    if let subtitle {
                        Text(subtitle)
                            .font(DS.Font.caption)
                            .foregroundStyle(DS.Text.muted)
                            .monospacedDigit()
                            .lineLimit(stacked ? nil : min(max(subtitleLineLimit, 1), 2))
                    }
                }
                .frame(maxWidth: .infinity, alignment: .leading)
            }
            trailing
                .layoutPriority(1)
        }
        .accessibilityElement(children: .combine)
    }
}

extension EntityRow where Leading == EmptyView {
    /// A row with no leading image.
    init(
        _ title: String,
        subtitle: String? = nil,
        subtitleLineLimit: Int = 1,
        isPinned: Bool = false,
        @ViewBuilder trailing: () -> Trailing
    ) {
        self.init(title, subtitle: subtitle, subtitleLineLimit: subtitleLineLimit, isPinned: isPinned) {
            EmptyView()
        } trailing: {
            trailing()
        }
    }
}

extension EntityRow where Leading == EmptyView, Trailing == EmptyView {
    /// A text-only row.
    init(_ title: String, subtitle: String? = nil, subtitleLineLimit: Int = 1, isPinned: Bool = false) {
        self.init(title, subtitle: subtitle, subtitleLineLimit: subtitleLineLimit, isPinned: isPinned) {
            EmptyView()
        } trailing: {
            EmptyView()
        }
    }
}

extension EntityRow where Leading == IconTile<IconTileSymbol> {
    /// A row led by a muted SF Symbol in a 32 pt column (`IconTile`; `tint`
    /// is ignored: icons take their text role's colour).
    init(
        _ title: String,
        subtitle: String? = nil,
        subtitleLineLimit: Int = 1,
        systemImage: String,
        tint: Color = DS.Text.muted,
        isPinned: Bool = false,
        @ViewBuilder trailing: () -> Trailing
    ) {
        self.init(title, subtitle: subtitle, subtitleLineLimit: subtitleLineLimit, isPinned: isPinned) {
            IconTile(systemImage: systemImage, color: tint, size: EntityRowMetrics.iconSize)
        } trailing: {
            trailing()
        }
    }
}

extension EntityRow where Leading == IconTile<IconTileSymbol>, Trailing == EmptyView {
    /// A row led by a 32 pt `IconTile`, with nothing trailing.
    init(
        _ title: String,
        subtitle: String? = nil,
        subtitleLineLimit: Int = 1,
        systemImage: String,
        tint: Color = DS.Text.muted,
        isPinned: Bool = false
    ) {
        self.init(
            title,
            subtitle: subtitle,
            subtitleLineLimit: subtitleLineLimit,
            systemImage: systemImage,
            tint: tint,
            isPinned: isPinned
        ) {
            EmptyView()
        }
    }
}

extension EnvironmentValues {
    @Entry var dsEntityTitleMedium = false
}

extension View {
    /// `EntityRow` titles inside draw at 14/500, as the web sets symbols
    /// (holdings, orders, movers).
    func entityRowTitleMedium(_ medium: Bool = true) -> some View {
        environment(\.dsEntityTitleMedium, medium)
    }
}

/// Sizes shared by rows, so a custom leading view (a brand logo in an
/// `IconTile`, a `MiniAllocationRing`) lines up with the symbol tiles.
nonisolated enum EntityRowMetrics {
    /// The leading glyph column's width.
    static let iconSize: CGFloat = 24
}

/// A trailing figure for an `EntityRow`: the value in 14 pt zinc-100 with
/// monospaced digits, right-aligned, with an optional 12 pt line under it
/// (zinc-500, or gain/loss for a signed P&L), such as "+$41.48 · +2.08%" or
/// "131 sh @ $89.80". Colour only for meaning.
///
///     EntityRowValue("$2,031.08", detail: "+$41.48 · +2.08%", detailColor: DS.Palette.up)
struct EntityRowValue: View {
    let value: String
    var valueColor: Color?
    var detail: String?
    var detailColor: Color?

    init(_ value: String, color: Color? = nil, detail: String? = nil, detailColor: Color? = nil) {
        self.value = value
        self.valueColor = color
        self.detail = detail
        self.detailColor = detailColor
    }

    var body: some View {
        VStack(alignment: .trailing, spacing: 2) {
            Text(value)
                .font(DS.Font.body)
                .foregroundStyle(valueColor ?? DS.Text.primary)
            if let detail {
                Text(detail)
                    .font(DS.Font.caption)
                    .foregroundStyle(detailColor ?? DS.Text.muted)
            }
        }
        .monospacedDigit()
        .lineLimit(1)
    }
}

#Preview("Rows") {
    NavigationStack {
        List {
            DSSection("Instances") {
                NavigationLink(value: 1) {
                    EntityRow(
                        "Alpaca Paper — forward test",
                        subtitle: "Strategy 197 · Alpaca Paper",
                        systemImage: "cpu",
                        isPinned: true
                    ) {
                        StatusDot("Running", color: DS.Palette.success, pulsing: true)
                    }
                }
                NavigationLink(value: 2) {
                    EntityRow("AI Agent Testing", subtitle: "Strategy 336 · bf78ad0c…b043404", systemImage: "cpu") {
                        StatusDot("Stopped", color: .secondary)
                    }
                }
            }
            DSSection("Backtests") {
                NavigationLink(value: 3) {
                    EntityRow("swing-paper", subtitle: "#554589 · Jul 7 – Sep 18, 2026") {
                        EntityRowValue("+$1,204.18", color: DS.Palette.up, detail: "+12.04%", detailColor: DS.Palette.up)
                    }
                }
                NavigationLink(value: 4) {
                    EntityRow("eb-lab", subtitle: "#554590 · Aug 1 – Sep 1, 2026") {
                        StatusBadge(label: "Failed", color: DS.Palette.danger)
                    }
                }
            }
            DSSection("Holdings") {
                EntityRow("TQQQ", subtitle: "22.4 sh") {
                    MiniAllocationRing(fraction: 0.31, color: DS.Palette.down)
                } trailing: {
                    EntityRowValue("$1,823.74", detail: "−$12.40 · −0.68%", detailColor: DS.Palette.down)
                }
            }
            DSSection {
                EntityRow("Settings", systemImage: "gearshape")
            }
        }
        .dsList()
        .navigationTitle("Rows")
        .navigationDestination(for: Int.self) { Text("Detail \($0)") }
    }
    .dsAppearance()
}
