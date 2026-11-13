import SwiftUI

/// The web buttons (`ui-btn-primary`, `ui-btn`, `ui-btn-ghost`,
/// `ui-icon-btn`, and `ui-btn text-loss` for a destructive action).
///
/// - **Primary** (`.dsPrimary`): the one main action on a screen or sheet.
///   36 tall (44 with `size: .large`), off-white fill, dark 14/500 label,
///   8 radius, a 1 pt top highlight.
/// - **Secondary** (`.dsSecondary`): every other action. 32 tall (36/44
///   with `.medium`/`.large`), raised fill, 7% hairline, 13/500 zinc-100.
///   A `role: .destructive` button draws its label in loss red.
/// - **Ghost** (`.dsGhost`): a quiet text action (Cancel, Back, Show all):
///   zinc-400 text, a raised fill only while pressed.
/// - **Icon** (`.dsIcon`): a 32 pt square around a 16–18 pt SF Symbol in
///   zinc-500. Give it an `accessibilityLabel`.
///
/// All dim to 40% when disabled and press on the dashboard's 150 ms ease-out.
///
///     Button("Review order") { … }.buttonStyle(.dsPrimary)
///     Button("Start") { … }.buttonStyle(.dsSecondary)
///     Button("Stop", role: .destructive) { … }.buttonStyle(.dsSecondary)
///     Button("Cancel") { dismiss() }.buttonStyle(.dsGhost)
///     Button { refresh() } label: { Image(systemName: "arrow.clockwise") }
///         .buttonStyle(.dsIcon).accessibilityLabel("Refresh")
///
/// In a toolbar, use a plain `Button`: iOS 26 draws toolbar items on glass.
nonisolated enum DSButtonSize: Sendable {
    /// Primary 36, secondary 32 (the web's sizes).
    case regular
    /// 36 for every kind (a secondary button beside a primary one).
    case medium
    /// 44: a full-width call to action at the bottom of a screen or sheet.
    case large

    func height(primary: Bool) -> CGFloat {
        switch self {
        case .regular: primary ? 36 : 32
        case .medium: 36
        case .large: 44
        }
    }
}

struct DSPrimaryButtonStyle: ButtonStyle {
    var size: DSButtonSize = .regular
    var fullWidth = false

    func makeBody(configuration: Configuration) -> some View {
        DSButtonBody(configuration: configuration) { pressed, _ in
            configuration.label
                .font(size == .large ? DS.Font.baseMedium : DS.Font.bodyMedium)
                .foregroundStyle(DS.Palette.onAccent)
                .lineLimit(1)
                .padding(.horizontal, 16)
                .frame(maxWidth: fullWidth ? .infinity : nil)
                .frame(height: size.height(primary: true))
                .background(pressed ? Color.white : DS.Palette.accent, in: .rect(cornerRadius: DS.Radius.control, style: .continuous))
                .overlay(alignment: .top) {
                    DSTopHighlight(color: DS.Line.primaryHighlight, radius: DS.Radius.control)
                }
        }
    }
}

struct DSSecondaryButtonStyle: ButtonStyle {
    var size: DSButtonSize = .regular
    var fullWidth = false

    func makeBody(configuration: Configuration) -> some View {
        DSButtonBody(configuration: configuration, scalesOnPress: true) { pressed, _ in
            configuration.label
                .font(size == .large ? DS.Font.bodyMedium : DS.Font.smallMedium)
                .foregroundStyle(configuration.role == .destructive ? DS.Palette.loss : DS.Text.primary)
                .lineLimit(1)
                .padding(.horizontal, 12)
                .frame(maxWidth: fullWidth ? .infinity : nil)
                .frame(height: size.height(primary: false))
                .background(pressed ? DS.Surface.hover : DS.Surface.raised, in: .rect(cornerRadius: DS.Radius.control, style: .continuous))
                .overlay {
                    RoundedRectangle(cornerRadius: DS.Radius.control, style: .continuous)
                        .strokeBorder(DS.Line.card, lineWidth: DS.Line.width)
                }
                .overlay(alignment: .top) {
                    DSTopHighlight(color: Color.white.opacity(0.04), radius: DS.Radius.control)
                }
        }
    }
}

struct DSGhostButtonStyle: ButtonStyle {
    var size: DSButtonSize = .regular

    func makeBody(configuration: Configuration) -> some View {
        DSButtonBody(configuration: configuration) { pressed, _ in
            configuration.label
                .font(DS.Font.body)
                .foregroundStyle(configuration.role == .destructive ? DS.Palette.loss : (pressed ? DS.Text.primary : DS.Text.secondary))
                .lineLimit(1)
                .padding(.horizontal, 12)
                .frame(height: size.height(primary: size != .regular))
                .background(pressed ? DS.Surface.raised : Color.clear, in: .rect(cornerRadius: DS.Radius.small, style: .continuous))
        }
    }
}

struct DSIconButtonStyle: ButtonStyle {
    /// The square's side (32 on the web; 44 where a larger target helps).
    var side: CGFloat = 32

    func makeBody(configuration: Configuration) -> some View {
        DSButtonBody(configuration: configuration) { pressed, _ in
            configuration.label
                .font(.system(size: 17, weight: .regular))
                .imageScale(.medium)
                .foregroundStyle(configuration.role == .destructive ? DS.Palette.loss : (pressed ? DS.Text.soft : DS.Text.muted))
                .frame(width: side, height: side)
                .background(pressed ? DS.Surface.raised : Color.clear, in: .rect(cornerRadius: DS.Radius.small, style: .continuous))
        }
    }
}

extension ButtonStyle where Self == DSPrimaryButtonStyle {
    /// `ui-btn-primary`.
    static var dsPrimary: DSPrimaryButtonStyle { DSPrimaryButtonStyle() }
    static func dsPrimary(size: DSButtonSize = .regular, fullWidth: Bool = false) -> DSPrimaryButtonStyle {
        DSPrimaryButtonStyle(size: size, fullWidth: fullWidth)
    }
}

extension ButtonStyle where Self == DSSecondaryButtonStyle {
    /// `ui-btn`. Destructive role → loss text.
    static var dsSecondary: DSSecondaryButtonStyle { DSSecondaryButtonStyle() }
    static func dsSecondary(size: DSButtonSize = .regular, fullWidth: Bool = false) -> DSSecondaryButtonStyle {
        DSSecondaryButtonStyle(size: size, fullWidth: fullWidth)
    }
}

extension ButtonStyle where Self == DSGhostButtonStyle {
    /// `ui-btn-ghost`.
    static var dsGhost: DSGhostButtonStyle { DSGhostButtonStyle() }
    static func dsGhost(size: DSButtonSize) -> DSGhostButtonStyle { DSGhostButtonStyle(size: size) }
}

extension ButtonStyle where Self == DSIconButtonStyle {
    /// `ui-icon-btn`.
    static var dsIcon: DSIconButtonStyle { DSIconButtonStyle() }
    static func dsIcon(side: CGFloat) -> DSIconButtonStyle { DSIconButtonStyle(side: side) }
}

/// The shared press, disabled and motion handling of the DS buttons.
private struct DSButtonBody<Content: View>: View {
    let configuration: ButtonStyleConfiguration
    var scalesOnPress = false
    @ViewBuilder let content: (_ pressed: Bool, _ enabled: Bool) -> Content

    @Environment(\.isEnabled) private var isEnabled
    @Environment(\.accessibilityReduceMotion) private var reduceMotion

    var body: some View {
        let pressed = configuration.isPressed && isEnabled
        content(pressed, isEnabled)
            .scaleEffect(scalesOnPress && pressed && !reduceMotion ? 0.98 : 1)
            .opacity(isEnabled ? 1 : 0.4)
            .contentShape(.rect(cornerRadius: DS.Radius.control))
            .animation(DSMotion.standard(reduceMotion: reduceMotion), value: pressed)
    }
}

/// A 1 pt highlight along a control's top edge (`shadow-[inset_0_1px_0_…]`).
struct DSTopHighlight: View {
    let color: Color
    var radius: CGFloat = DS.Radius.control

    var body: some View {
        // A straight 1 pt line inside the top edge, clear of the corners.
        Rectangle()
            .fill(color)
            .frame(height: DS.Line.width)
            .padding(.horizontal, radius * 0.75)
            .padding(.top, DS.Line.width)
            .allowsHitTesting(false)
            .accessibilityHidden(true)
    }
}

extension View {
    /// The primary action (`ui-btn-primary`). Kept for existing call sites;
    /// new code writes `.buttonStyle(.dsPrimary)`.
    func dsProminentButton() -> some View {
        buttonStyle(.dsPrimary)
    }

    /// A full-width primary action at the bottom of a screen or sheet, 44
    /// tall. It used to be Liquid Glass; the web has no glass, so it is now
    /// the primary button at the large size.
    func dsGlassProminentButton() -> some View {
        buttonStyle(.dsPrimary(size: .large))
    }
}

#Preview {
    VStack(alignment: .leading, spacing: 16) {
        HStack {
            Button("Review order") {}.buttonStyle(.dsPrimary)
            Button("Start") {}.buttonStyle(.dsSecondary)
            Button("Stop", role: .destructive) {}.buttonStyle(.dsSecondary)
            Button("Cancel") {}.buttonStyle(.dsGhost)
            Button {} label: { Image(systemName: "arrow.clockwise") }.buttonStyle(.dsIcon)
        }
        Button("Buy") {}.buttonStyle(.dsPrimary(size: .large, fullWidth: true))
        Button("Disabled") {}.buttonStyle(.dsPrimary).disabled(true)
    }
    .padding()
    .background(DS.Surface.page)
    .dsAppearance()
}
