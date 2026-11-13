import SwiftUI
import UIKit

// The web modal form, for the sheets of Brokerages, Models, Crypto and Kalshi:
// `ui-modal-head` / `ui-modal-body` / `ui-modal-foot`, a `ui-input` select and
// secure field, the `.msg` result box and the warning-dot note. These are not
// design-system components yet (the trade ticket draws the same shapes inline);
// they are candidates to move into `DesignSystem/Components`.

/// A form sheet shaped like the web's modal: a header (16/500 title, an
/// optional 14 pt zinc-500 subtitle, a close icon button), the fields 20 pt
/// apart on the card surface, and a footer bar over a 6% divider holding the
/// actions (`FormSheetActions`). The grabber stays; the navigation bar is
/// hidden, so a pushed picker still gets its own bar.
///
///     FormSheet("Link brokerage account", onClose: { dismiss() }) {
///         DSField("Account name") { FormTextField("e.g. My paper trading", text: $name) }
///     } footer: {
///         FormSheetActions(primary: "Link account", busy: saving) { … } onCancel: { dismiss() }
///     }
struct FormSheet<Content: View, Footer: View>: View {
    let title: String
    var subtitle: String?
    var closeDisabled = false
    let onClose: () -> Void
    private let content: Content
    private let footer: Footer

    init(
        _ title: String,
        subtitle: String? = nil,
        closeDisabled: Bool = false,
        onClose: @escaping () -> Void,
        @ViewBuilder content: () -> Content,
        @ViewBuilder footer: () -> Footer
    ) {
        self.title = title
        self.subtitle = subtitle
        self.closeDisabled = closeDisabled
        self.onClose = onClose
        self.content = content()
        self.footer = footer()
    }

    var body: some View {
        NavigationStack {
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
            .safeAreaInset(edge: .bottom, spacing: 0) { footerBar }
            .toolbar(.hidden, for: .navigationBar)
            .toolbar {
                ToolbarItemGroup(placement: .keyboard) {
                    Spacer()
                    Button("Done") { formEndEditing() }
                }
            }
            .navigationTitle(title)
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
                    Text(subtitle)
                        .font(DS.Font.body)
                        .foregroundStyle(DS.Text.muted)
                        .fixedSize(horizontal: false, vertical: true)
                }
            }
            .frame(maxWidth: .infinity, alignment: .leading)
            Button(action: onClose) {
                Image(systemName: "xmark")
            }
            .buttonStyle(.dsIcon)
            .disabled(closeDisabled)
            .accessibilityLabel("Close")
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

/// Fonts the design system has no role for yet.
nonisolated enum FormFont {
    /// Monospaced input text at the input's 14 pt (`ui-input font-mono`);
    /// `DS.Font.mono` is 12 pt, sized for log lines.
    static let monoInput = Font.system(size: 14, design: .monospaced)
    /// A one-time code to read off and type elsewhere: 20/500 monospaced.
    static let monoCode = Font.system(size: 20, weight: .medium, design: .monospaced)
}

/// Resigns the first responder (the keyboard's Done).
@MainActor
func formEndEditing() {
    UIApplication.shared.sendAction(#selector(UIResponder.resignFirstResponder), to: nil, from: nil, for: nil)
}

/// The modal footer's buttons (`ui-modal-foot`): a ghost Cancel, an optional
/// secondary action (Test), and the primary action, which shows a spinner
/// and `busyLabel` while it runs.
struct FormSheetActions: View {
    let primary: String
    var busyLabel: String?
    var busy = false
    var primaryDisabled = false
    var cancelLabel = "Cancel"
    var cancelDisabled = false
    var secondary: String?
    var secondaryBusy = false
    var secondaryDisabled = false
    var onSecondary: (() -> Void)?
    let onPrimary: () -> Void
    let onCancel: () -> Void

    init(
        primary: String,
        busyLabel: String? = nil,
        busy: Bool = false,
        primaryDisabled: Bool = false,
        cancelLabel: String = "Cancel",
        cancelDisabled: Bool = false,
        secondary: String? = nil,
        secondaryBusy: Bool = false,
        secondaryDisabled: Bool = false,
        onSecondary: (() -> Void)? = nil,
        onPrimary: @escaping () -> Void,
        onCancel: @escaping () -> Void
    ) {
        self.primary = primary
        self.busyLabel = busyLabel
        self.busy = busy
        self.primaryDisabled = primaryDisabled
        self.cancelLabel = cancelLabel
        self.cancelDisabled = cancelDisabled
        self.secondary = secondary
        self.secondaryBusy = secondaryBusy
        self.secondaryDisabled = secondaryDisabled
        self.onSecondary = onSecondary
        self.onPrimary = onPrimary
        self.onCancel = onCancel
    }

    var body: some View {
        Button(cancelLabel, action: onCancel)
            .buttonStyle(.dsGhost(size: .medium))
            .disabled(cancelDisabled)
        if let secondary, let onSecondary {
            Button(action: onSecondary) {
                HStack(spacing: 6) {
                    if secondaryBusy { ProgressView().controlSize(.mini) }
                    Text(secondary)
                }
            }
            .buttonStyle(.dsSecondary(size: .medium))
            .disabled(secondaryDisabled)
        }
        Button(action: onPrimary) {
            HStack(spacing: 6) {
                if busy { ProgressView().controlSize(.mini).tint(DS.Palette.onAccent) }
                Text(busy ? (busyLabel ?? primary) : primary)
            }
        }
        .buttonStyle(.dsPrimary)
        .disabled(busy || primaryDisabled)
        .accessibilityLabel(primary)
    }
}

/// A field label with an optional tap-to-reveal explanation (the web's
/// `ui-field-label` + `InfoTip`), over the control and an optional help
/// line: `DSField`'s metrics with an info button beside the label.
struct FormField<Content: View>: View {
    let label: String
    var info: String?
    var help: String?
    private let content: Content

    init(_ label: String, info: String? = nil, help: String? = nil, @ViewBuilder content: () -> Content) {
        self.label = label
        self.info = info
        self.help = help
        self.content = content()
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            if let info {
                MarketsInfoLabel(text: label, info: info)
            } else {
                Text(label)
                    .font(DS.Font.body)
                    .foregroundStyle(DS.Text.label)
            }
            DSField(nil, help: help) { content }
        }
    }
}

/// A text input (`ui-input`) that tracks its focus: plain, monospaced (keys,
/// ids, URLs) or secure. Auto-capitalisation and correction are off, as for
/// every credential and id field.
struct FormTextField: View {
    let placeholder: String
    @Binding var text: String
    var mono = false
    var secure = false
    var keyboard: UIKeyboardType = .default
    var axis: Axis = .horizontal
    var prefix: String?

    @FocusState private var focused: Bool

    init(
        _ placeholder: String,
        text: Binding<String>,
        mono: Bool = false,
        secure: Bool = false,
        keyboard: UIKeyboardType = .default,
        axis: Axis = .horizontal,
        prefix: String? = nil
    ) {
        self.placeholder = placeholder
        self._text = text
        self.mono = mono
        self.secure = secure
        self.keyboard = keyboard
        self.axis = axis
        self.prefix = prefix
    }

    var body: some View {
        let font = mono ? FormFont.monoInput : DS.Font.body
        let prompt = Text(verbatim: placeholder).font(font).foregroundStyle(DS.Text.faint)
        Group {
            if secure {
                SecureField(text: $text, prompt: prompt) { Text(verbatim: placeholder) }
                    .textContentType(.password)
            } else {
                TextField(text: $text, prompt: prompt, axis: axis) { Text(verbatim: placeholder) }
            }
        }
        .font(font)
        .keyboardType(keyboard)
        .textInputAutocapitalization(.never)
        .autocorrectionDisabled()
        .focused($focused)
        .dsInput(focused: focused, prefix: prefix, multiline: axis == .vertical)
    }
}

/// A select (`ui-input` + a chevron) that opens a menu of `options`, the
/// current one checked. `display` is the selected option's text.
///
///     FormSelect("Market data feed", display: feedLabel, selection: $feed) {
///         ForEach(feeds, id: \.value) { Text($0.label).tag($0.value) }
///     }
struct FormSelect<Value: Hashable, Options: View>: View {
    let title: String
    let display: String
    @Binding var selection: Value
    private let options: Options

    init(_ title: String, display: String, selection: Binding<Value>, @ViewBuilder options: () -> Options) {
        self.title = title
        self.display = display
        self._selection = selection
        self.options = options()
    }

    var body: some View {
        Menu {
            Picker(title, selection: $selection) { options }
                .pickerStyle(.inline)
                .labelsHidden()
        } label: {
            HStack(spacing: 8) {
                Text(display)
                    .foregroundStyle(DS.Text.soft)
                    .lineLimit(1)
                    .frame(maxWidth: .infinity, alignment: .leading)
                Image(systemName: "chevron.up.chevron.down")
                    .font(.system(size: 12))
                    .foregroundStyle(DS.Text.muted)
                    .accessibilityHidden(true)
            }
            .contentShape(Rectangle())
            .dsInput()
        }
        .accessibilityLabel(title)
        .accessibilityValue(display)
    }
}

/// A switch with a label and an optional 12 pt note under it (the web's
/// switch rows: "Paper trading (paper-api.alpaca.markets)").
struct FormToggle: View {
    let title: String
    var note: String?
    var titleColor: Color = DS.Text.label
    @Binding var isOn: Bool

    init(_ title: String, note: String? = nil, titleColor: Color = DS.Text.label, isOn: Binding<Bool>) {
        self.title = title
        self.note = note
        self.titleColor = titleColor
        self._isOn = isOn
    }

    var body: some View {
        Toggle(isOn: $isOn) {
            VStack(alignment: .leading, spacing: 2) {
                Text(title)
                    .font(DS.Font.body)
                    .foregroundStyle(titleColor)
                if let note {
                    Text(verbatim: note)
                        .font(DS.Font.caption)
                        .foregroundStyle(DS.Text.muted)
                        .fixedSize(horizontal: false, vertical: true)
                }
            }
        }
    }
}

/// A save or test result (the web's `.msg`): 14 pt text in a box with an 8
/// radius — gain on a gain tint when it worked, loss on a loss tint when it
/// failed, zinc-300 on raised while it runs.
struct FormMessage: View {
    enum Tone { case ok, error, busy }

    let text: String
    let tone: Tone

    init(_ text: String, tone: Tone) {
        self.text = text
        self.tone = tone
    }

    var body: some View {
        let (ink, fill, line): (Color, Color, Color) = switch tone {
        case .ok: (DS.Palette.gain, DS.Palette.gain.opacity(DS.tintFill), DS.Palette.gain.opacity(DS.tintBorder))
        case .error: (DS.Palette.loss, DS.Palette.loss.opacity(DS.tintFill), DS.Palette.loss.opacity(DS.tintBorder))
        case .busy: (DS.Text.label, DS.Surface.raised, DS.Line.card)
        }
        HStack(spacing: 8) {
            if tone == .busy { ProgressView().controlSize(.mini) }
            Text(text)
                .font(DS.Font.body)
                .foregroundStyle(ink)
                .frame(maxWidth: .infinity, alignment: .leading)
                .fixedSize(horizontal: false, vertical: true)
        }
        .padding(.horizontal, 16)
        .padding(.vertical, 12)
        .background(fill, in: .rect(cornerRadius: DS.Radius.control, style: .continuous))
        .overlay {
            RoundedRectangle(cornerRadius: DS.Radius.control, style: .continuous)
                .strokeBorder(line, lineWidth: DS.Line.width)
        }
        .accessibilityElement(children: .combine)
    }
}

/// A short note under a field: an amber dot and 12 pt zinc-300 text for a
/// warning ("Live account — instances bound here trade real money"), or
/// plain 12 pt zinc-500 text for information.
struct FormNote: View {
    enum Tone { case warn, info }

    let text: String
    var tone: Tone = .warn

    init(_ text: String, tone: Tone = .warn) {
        self.text = text
        self.tone = tone
    }

    var body: some View {
        HStack(alignment: .firstTextBaseline, spacing: 8) {
            if tone == .warn {
                DSStatusDot(.warn)
                    .alignmentGuide(.firstTextBaseline) { $0[VerticalAlignment.center] + 4 }
            }
            Text(text)
                .font(DS.Font.caption)
                .foregroundStyle(tone == .warn ? DS.Text.label : DS.Text.muted)
                .frame(maxWidth: .infinity, alignment: .leading)
                .fixedSize(horizontal: false, vertical: true)
        }
        .accessibilityElement(children: .combine)
    }
}

/// Two equal columns of fields (the web's `grid grid-cols-2 gap-4`), for
/// numbers that read best side by side.
struct FormGrid<Content: View>: View {
    private let content: Content

    init(@ViewBuilder content: () -> Content) {
        self.content = content()
    }

    var body: some View {
        LazyVGrid(
            // Bottom-aligned, so the inputs line up when one label wraps.
            columns: [GridItem(.flexible(), spacing: 12, alignment: .bottom), GridItem(.flexible(), alignment: .bottom)],
            alignment: .leading,
            spacing: 16
        ) {
            content
        }
    }
}

/// A group heading inside a form sheet (14/500 zinc-200), for long forms
/// split into parts.
struct FormGroupTitle: View {
    let title: String
    var subtitle: String?

    init(_ title: String, subtitle: String? = nil) {
        self.title = title
        self.subtitle = subtitle
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 2) {
            Text(title)
                .font(DS.Font.bodyMedium)
                .foregroundStyle(DS.Text.soft)
                .accessibilityAddTraits(.isHeader)
            if let subtitle {
                Text(subtitle)
                    .font(DS.Font.caption)
                    .foregroundStyle(DS.Text.muted)
                    .fixedSize(horizontal: false, vertical: true)
            }
        }
        .padding(.top, 8)
    }
}
