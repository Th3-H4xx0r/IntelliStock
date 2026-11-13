import SwiftUI

// Small pieces the Nexus, Agent runs and Learning screens share, in the web
// style. Candidates for the design system: `ModalSheet` (the web's
// `ui-modal` head, body and foot as a sheet), `CheckboxRow` (the web's
// checkbox lists), `OpsProgressBar` (the web's 4 pt progress track) and
// `OptionList` (rows in a rounded-lg hairline box inside a modal).

/// A sheet drawn as the web's modal (`ui-modal`): a header with the title
/// (16/500) and a quieter line under it (14 zinc-500) and a close button, the
/// body scrolling on the card colour, and a footer over a hairline holding
/// the actions (`ui-modal-foot`: a ghost Cancel, then the primary action).
///
///     ModalSheet("Start Nexus", subtitle: "Choose the phases to run") {
///         DSField("13F history (quarters)") { DSTextField("1", text: $q) }
///     } footer: {
///         Button("Cancel") { dismiss() }.buttonStyle(.dsGhost(size: .medium))
///         Button("Start Nexus") { … }.buttonStyle(.dsPrimary)
///     }
struct ModalSheet<Content: View, Footer: View>: View {
    let title: String
    var subtitle: String?
    /// The header's close button; hide it while a step must finish.
    var showsClose = true
    private let content: Content
    private let footer: Footer

    @Environment(\.dismiss) private var dismiss

    init(
        _ title: String,
        subtitle: String? = nil,
        showsClose: Bool = true,
        @ViewBuilder content: () -> Content,
        @ViewBuilder footer: () -> Footer
    ) {
        self.title = title
        self.subtitle = subtitle
        self.showsClose = showsClose
        self.content = content()
        self.footer = footer()
    }

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 20) {
                content
            }
            .padding(.horizontal, 24)
            .padding(.top, 4)
            .padding(.bottom, 24)
            .frame(maxWidth: .infinity, alignment: .leading)
        }
        .scrollDismissesKeyboard(.interactively)
        .background(DS.Surface.card)
        .safeAreaInset(edge: .top, spacing: 0) { header }
        .safeAreaInset(edge: .bottom, spacing: 0) {
            if Footer.self != EmptyView.self { footerBar }
        }
        .presentationBackground(DS.Surface.card)
        .presentationDragIndicator(.visible)
    }

    private var header: some View {
        HStack(alignment: .top, spacing: 12) {
            VStack(alignment: .leading, spacing: 4) {
                Text(title)
                    .font(DS.Font.sectionTitle)
                    .foregroundStyle(DS.Text.primary)
                    .accessibilityAddTraits(.isHeader)
                if let subtitle {
                    Text(subtitle.dsTrimmingTitlePeriod)
                        .font(DS.Font.body)
                        .foregroundStyle(DS.Text.muted)
                        .fixedSize(horizontal: false, vertical: true)
                }
            }
            .frame(maxWidth: .infinity, alignment: .leading)
            if showsClose {
                Button { dismiss() } label: {
                    Image(systemName: "xmark")
                }
                .buttonStyle(.dsIcon)
                .accessibilityLabel("Close")
            }
        }
        .padding(.horizontal, 24)
        .padding(.top, 20)
        .padding(.bottom, 16)
        .background(DS.Surface.card)
    }

    private var footerBar: some View {
        HStack(spacing: 8) {
            Spacer(minLength: 0)
            footer
        }
        .padding(.horizontal, 24)
        .padding(.vertical, 16)
        .background {
            DS.Surface.card
                .overlay(alignment: .top) { DSDivider() }
                .ignoresSafeArea(edges: .bottom)
        }
    }
}

extension ModalSheet where Footer == EmptyView {
    init(_ title: String, subtitle: String? = nil, showsClose: Bool = true, @ViewBuilder content: () -> Content) {
        self.init(title, subtitle: subtitle, showsClose: showsClose, content: content) { EmptyView() }
    }
}

/// Rows inside a modal (the web's `rounded-lg border border-white/[0.07]
/// divide-y divide-white/[0.05]` lists): 8 radius, a 7% hairline, 5%
/// hairlines between the rows.
struct OptionList<Content: View>: View {
    private let content: Content

    init(@ViewBuilder content: () -> Content) {
        self.content = content()
    }

    var body: some View {
        VStack(spacing: 0) {
            Group(subviews: content) { rows in
                ForEach(Array(rows.enumerated()), id: \.element.id) { index, row in
                    // Inset by the outline's width, so a hairline never
                    // crosses the box's own edge.
                    if index > 0 { DSDivider(row: true).padding(.horizontal, DS.Line.width) }
                    row
                }
            }
        }
        .dsCardSurface(radius: DS.Radius.control)
    }
}

/// One checkbox row (the web's checkbox lists): a square check in zinc-200
/// when on, zinc-600 when off, the label beside it, and the raised fill
/// behind a checked row. The whole row is the target; VoiceOver hears a
/// selected button.
struct CheckboxRow<RowLabel: View>: View {
    let isOn: Bool
    let action: () -> Void
    private let label: RowLabel

    init(isOn: Bool, action: @escaping () -> Void, @ViewBuilder label: () -> RowLabel) {
        self.isOn = isOn
        self.action = action
        self.label = label()
    }

    var body: some View {
        HStack(alignment: .firstTextBaseline, spacing: 10) {
            Image(systemName: isOn ? "checkmark.square.fill" : "square")
                .font(.system(size: 16))
                .foregroundStyle(isOn ? DS.Text.soft : DS.Text.faint)
                .accessibilityHidden(true)
            VStack(alignment: .leading, spacing: 2) { label }
                .frame(maxWidth: .infinity, alignment: .leading)
        }
        .padding(.horizontal, 12)
        .padding(.vertical, 10)
        .frame(maxWidth: .infinity, minHeight: 44, alignment: .leading)
        // A square fill edge to edge: a system button would round it.
        .background(Rectangle().fill(isOn ? DS.Surface.raised : Color.clear))
        .contentShape(Rectangle())
        .onTapGesture(perform: action)
        .accessibilityElement(children: .combine)
        .accessibilityAddTraits(isOn ? [.isButton, .isSelected] : .isButton)
        .accessibilityAction(.default, action)
    }
}

/// A thin progress track (the web's `h-1 bg-raised rounded-full` bars): the
/// raised track and a solid fill in `color` (zinc-200 while running,
/// zinc-300 done, loss when failed).
struct OpsProgressBar: View {
    let fraction: Double
    var color: Color = DS.Text.soft
    var height: CGFloat = 4

    var body: some View {
        GeometryReader { geo in
            ZStack(alignment: .leading) {
                Capsule().fill(DS.Surface.raised)
                Capsule()
                    .fill(color)
                    .frame(width: geo.size.width * min(max(fraction, 0), 1))
            }
        }
        .frame(height: height)
        .accessibilityHidden(true)
    }
}

/// A label over a figure (the web's `text-xs text-slate-500` over a value),
/// for stat rows inside cards that are not a `StatGrid`.
struct OpsStat: View {
    let label: String
    let value: String
    var detail: String?
    var valueFont: Font = DS.Font.body

    var body: some View {
        VStack(alignment: .leading, spacing: 4) {
            Text(label)
                .font(DS.Font.caption)
                .foregroundStyle(DS.Text.muted)
            Text(value)
                .font(valueFont)
                .foregroundStyle(DS.Text.primary)
                .monospacedDigit()
                .fixedSize(horizontal: false, vertical: true)
            if let detail {
                Text(detail)
                    .font(DS.Font.caption)
                    .foregroundStyle(DS.Text.muted)
                    .monospacedDigit()
            }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .accessibilityElement(children: .combine)
    }
}
