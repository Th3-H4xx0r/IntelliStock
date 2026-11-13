import SwiftUI

// MARK: - Segmented control (`ui-seg`)

/// The web's segmented control (`ui-seg` + `ui-seg-btn`): a 2 pt-padded
/// surface track with a 7% hairline and 8 radius; 28 pt segments in 12/500
/// zinc-500, the active one raised with zinc-100 text. Use it instead of
/// `Picker(.segmented)` wherever it fits (a section header, a ticket).
///
///     DSSegmentedControl("P&L", selection: $mode, options: HoldingsPnlMode.allCases) { $0.label }
///     DSSegmentedControl("Side", selection: $side, options: TradeSide.allCases, fullWidth: true) { $0.label }
///
/// `isDisabled` greys out an option that does not apply (a limit order on a
/// dollar amount). VoiceOver reads each segment as a button, the active one
/// selected.
struct DSSegmentedControl<Value: Hashable>: View {
    let title: String
    @Binding var selection: Value
    let options: [Value]
    var fullWidth = false
    /// 28 on the web; 32 for a full-width control in a form.
    var segmentHeight: CGFloat = 28
    var isDisabled: (Value) -> Bool = { _ in false }
    let label: (Value) -> String

    @Environment(\.accessibilityReduceMotion) private var reduceMotion

    init(
        _ title: String,
        selection: Binding<Value>,
        options: [Value],
        fullWidth: Bool = false,
        segmentHeight: CGFloat = 28,
        isDisabled: @escaping (Value) -> Bool = { _ in false },
        label: @escaping (Value) -> String
    ) {
        self.title = title
        self._selection = selection
        self.options = options
        self.fullWidth = fullWidth
        self.segmentHeight = segmentHeight
        self.isDisabled = isDisabled
        self.label = label
    }

    var body: some View {
        HStack(spacing: 0) {
            ForEach(options, id: \.self) { option in
                let active = option == selection
                let disabled = isDisabled(option)
                Button {
                    guard !disabled, selection != option else { return }
                    withAnimation(DSMotion.standard(reduceMotion: reduceMotion)) { selection = option }
                } label: {
                    Text(label(option))
                        .font(DS.Font.captionMedium)
                        .monospacedDigit()
                        .lineLimit(1)
                        .foregroundStyle(active ? DS.Text.primary : DS.Text.muted)
                        .padding(.horizontal, 12)
                        .frame(maxWidth: fullWidth ? .infinity : nil)
                        .frame(height: segmentHeight)
                        .background {
                            if active {
                                RoundedRectangle(cornerRadius: DS.Radius.small, style: .continuous)
                                    .fill(DS.Surface.raised)
                                    .overlay(alignment: .top) {
                                        DSTopHighlight(color: DS.Line.highlight, radius: DS.Radius.small)
                                    }
                            }
                        }
                        .contentShape(Rectangle())
                }
                .buttonStyle(.plain)
                .opacity(disabled ? 0.4 : 1)
                .disabled(disabled)
                .accessibilityAddTraits(active ? .isSelected : [])
            }
        }
        .padding(2)
        .background(DS.Surface.card, in: .rect(cornerRadius: DS.Radius.control, style: .continuous))
        .overlay {
            RoundedRectangle(cornerRadius: DS.Radius.control, style: .continuous)
                .strokeBorder(DS.Line.card, lineWidth: DS.Line.width)
        }
        .fixedSize(horizontal: !fullWidth, vertical: true)
        .accessibilityElement(children: .contain)
        .accessibilityLabel(title)
    }
}

extension DSSegmentedControl where Value: CustomStringConvertible {
    /// Options labelled by their `description`.
    init(_ title: String, selection: Binding<Value>, options: [Value], fullWidth: Bool = false) {
        self.init(title, selection: selection, options: options, fullWidth: fullWidth) { $0.description }
    }
}

// MARK: - Inputs (`ui-input`, `ui-field-label`, `ui-help`)

extension View {
    /// The web input (`ui-input`) on a `TextField`, `SecureField` or a
    /// `Picker` label: 36 tall, page fill, 8% hairline (25% when `focused`),
    /// 8 radius, 14 pt zinc-200 text. `prefix` draws a muted symbol inside
    /// the leading edge ("$"). Give the field a zinc-600 placeholder with
    /// `prompt: Text("…").foregroundStyle(DS.Text.faint)`, or use
    /// `DSTextField`, which does all of this.
    func dsInput(focused: Bool = false, prefix: String? = nil, multiline: Bool = false) -> some View {
        modifier(DSInputModifier(focused: focused, prefix: prefix, multiline: multiline))
    }
}

private struct DSInputModifier: ViewModifier {
    let focused: Bool
    let prefix: String?
    let multiline: Bool

    @Environment(\.isEnabled) private var isEnabled
    @Environment(\.accessibilityReduceMotion) private var reduceMotion

    func body(content: Content) -> some View {
        HStack(spacing: 6) {
            if let prefix {
                Text(prefix)
                    .font(DS.Font.body)
                    .foregroundStyle(DS.Text.muted)
                    .accessibilityHidden(true)
            }
            content
                .font(DS.Font.body)
                .foregroundStyle(DS.Text.soft)
                .tint(DS.Palette.accent)
        }
        .padding(.horizontal, 12)
        .padding(.vertical, multiline ? 10 : 0)
        .frame(minHeight: 36)
        .background(DS.Surface.page, in: .rect(cornerRadius: DS.Radius.control, style: .continuous))
        .overlay {
            RoundedRectangle(cornerRadius: DS.Radius.control, style: .continuous)
                .strokeBorder(focused ? DS.Line.focus : DS.Line.input, lineWidth: DS.Line.width)
        }
        .opacity(isEnabled ? 1 : 0.5)
        .animation(DSMotion.standard(reduceMotion: reduceMotion), value: focused)
    }
}

/// A text field in the web style that tracks its own focus: `dsInput` with
/// the focus ring and a zinc-600 placeholder.
///
///     DSTextField("0.00", text: $amount, prefix: "$")
///         .keyboardType(.decimalPad)
struct DSTextField: View {
    let placeholder: String
    @Binding var text: String
    var prefix: String?
    var axis: Axis = .horizontal

    @FocusState private var focused: Bool

    init(_ placeholder: String, text: Binding<String>, prefix: String? = nil, axis: Axis = .horizontal) {
        self.placeholder = placeholder
        self._text = text
        self.prefix = prefix
        self.axis = axis
    }

    var body: some View {
        TextField(text: $text, prompt: Text(placeholder).foregroundStyle(DS.Text.faint), axis: axis) {
            Text(placeholder)
        }
        .focused($focused)
        .dsInput(focused: focused, prefix: prefix, multiline: axis == .vertical)
    }
}

/// A form field: a 14 pt zinc-300 label 8 pt above the control, and an
/// optional 12 pt zinc-500 help line 8 pt below (`ui-field-label`,
/// `ui-help`). `accessory` sits at the label's trailing end (a segmented
/// "Shares / Dollars").
///
///     DSField("Account", help: "You hold 4.25 sh") {
///         DSTextField("0", text: $qty)
///     }
struct DSField<Content: View, Accessory: View>: View {
    let label: String?
    var help: String?
    private let accessory: Accessory
    private let content: Content

    init(_ label: String?, help: String? = nil, @ViewBuilder accessory: () -> Accessory, @ViewBuilder content: () -> Content) {
        self.label = label
        self.help = help
        self.accessory = accessory()
        self.content = content()
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            if label != nil || Accessory.self != EmptyView.self {
                HStack(alignment: .center, spacing: 12) {
                    if let label {
                        Text(label)
                            .font(DS.Font.body)
                            .foregroundStyle(DS.Text.label)
                    }
                    Spacer(minLength: 0)
                    accessory
                }
            }
            content
            if let help {
                Text(help)
                    .font(DS.Font.caption)
                    .foregroundStyle(DS.Text.muted)
                    .fixedSize(horizontal: false, vertical: true)
            }
        }
    }
}

extension DSField where Accessory == EmptyView {
    init(_ label: String?, help: String? = nil, @ViewBuilder content: () -> Content) {
        self.init(label, help: help, accessory: { EmptyView() }, content: content)
    }
}

// MARK: - Switch (`ui-switch`)

/// The web switch, used for every `Toggle` (set at the root by
/// `dsAppearance()`): a 36 × 20 track, raised with a zinc-400 knob when off,
/// off-white with a dark knob when on. VoiceOver still hears a switch.
struct DSSwitchToggleStyle: ToggleStyle {
    @Environment(\.isEnabled) private var isEnabled
    @Environment(\.accessibilityReduceMotion) private var reduceMotion

    func makeBody(configuration: Configuration) -> some View {
        HStack(spacing: 12) {
            configuration.label
                .frame(maxWidth: .infinity, alignment: .leading)
            DSSwitch(isOn: configuration.isOn)
                .onTapGesture {
                    withAnimation(DSMotion.standard(reduceMotion: reduceMotion)) { configuration.isOn.toggle() }
                }
        }
        .contentShape(Rectangle())
        .opacity(isEnabled ? 1 : 0.4)
        .accessibilityRepresentation {
            Toggle(isOn: configuration.$isOn) { configuration.label }
        }
    }
}

/// The switch's track and knob, drawn alone.
struct DSSwitch: View {
    let isOn: Bool

    var body: some View {
        ZStack(alignment: isOn ? .trailing : .leading) {
            Capsule()
                .fill(isOn ? DS.Palette.accent : DS.Surface.raised)
                .overlay {
                    Capsule().strokeBorder(isOn ? Color.clear : DS.Line.input, lineWidth: DS.Line.width)
                }
            Circle()
                .fill(isOn ? DS.Surface.page : DS.Text.secondary)
                .frame(width: 14, height: 14)
                .padding(.horizontal, 3)
        }
        .frame(width: 36, height: 20)
        .frame(minWidth: 44, minHeight: 44)
        .contentShape(Rectangle())
    }
}

#Preview {
    struct Demo: View {
        @State private var side = "Buy"
        @State private var mode = "Shares"
        @State private var on = true
        @State private var text = ""

        var body: some View {
            VStack(alignment: .leading, spacing: 20) {
                DSSegmentedControl("Side", selection: $side, options: ["Buy", "Sell"], fullWidth: true) { $0 }
                DSField("Shares") {
                    DSSegmentedControl("In", selection: $mode, options: ["Shares", "Dollars"]) { $0 }
                } content: {
                    DSTextField("0", text: $text)
                }
                DSField("Limit price", help: "Fills only at your price or better") {
                    DSTextField("0.00", text: $text, prefix: "$")
                }
                Toggle("Extended hours", isOn: $on)
            }
            .padding()
            .background(DS.Surface.card)
            .dsAppearance()
        }
    }
    return Demo()
}
