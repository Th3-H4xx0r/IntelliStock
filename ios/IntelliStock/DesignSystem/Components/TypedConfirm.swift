import SwiftUI

/// The "type HALT / CLOSE {SYMBOL} / instance-id to confirm" rule from
/// `typed_confirm_field.dart`: the trimmed text must equal `phrase` exactly,
/// and a change is reported only when the match state flips.
nonisolated struct TypedConfirmMatcher: Sendable {
    let phrase: String
    private(set) var matched = false

    init(phrase: String) {
        self.phrase = phrase
    }

    static func matches(_ text: String, phrase: String) -> Bool {
        text.trimmingCharacters(in: .whitespacesAndNewlines) == phrase
    }

    /// Feeds new text; returns the new match state when it changed.
    mutating func update(_ text: String) -> Bool? {
        let m = Self.matches(text, phrase: phrase)
        guard m != matched else { return nil }
        matched = m
        return m
    }
}

/// The safety gate as a form field, for confirmations that live inside a
/// sheet with other content — `TypedConfirmField`.
struct TypedConfirmField: View {
    let phrase: String
    var label: String?
    let onMatchChanged: (Bool) -> Void

    @State private var text = ""
    @State private var matcher: TypedConfirmMatcher

    init(phrase: String, label: String? = nil, onMatchChanged: @escaping (Bool) -> Void) {
        self.phrase = phrase
        self.label = label
        self.onMatchChanged = onMatchChanged
        _matcher = State(initialValue: TypedConfirmMatcher(phrase: phrase))
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 6) {
            Text(label ?? "Type \"\(phrase)\" to confirm")
                .font(.caption)
                .foregroundStyle(.secondary)
            TextField(phrase, text: $text)
                .font(.body.monospaced())
                .textInputAutocapitalization(.never)
                .autocorrectionDisabled()
                .padding(10)
                .background(DS.Surface.inset, in: .rect(cornerRadius: DS.Radius.small, style: .continuous))
                .onChange(of: text) { _, new in
                    if let changed = matcher.update(new) { onMatchChanged(changed) }
                }
        }
    }
}

/// A typed confirmation presented as an alert with a text field; the confirm
/// button stays disabled until the text matches. Present with
/// `.typedConfirmAlert($request)`.
struct TypedConfirmRequest: Identifiable {
    let id = UUID()
    let title: String
    let body: String
    let phrase: String
    var label: String?
    var confirmLabel = "Confirm"
    var role: ButtonRole? = .destructive
    let onConfirm: () async throws -> Void
    var onError: ((any Error) -> Void)?

    init(
        title: String,
        body: String,
        phrase: String,
        label: String? = nil,
        confirmLabel: String = "Confirm",
        role: ButtonRole? = .destructive,
        onConfirm: @escaping () async throws -> Void,
        onError: ((any Error) -> Void)? = nil
    ) {
        self.title = title
        self.body = body
        self.phrase = phrase
        self.label = label
        self.confirmLabel = confirmLabel
        self.role = role
        self.onConfirm = onConfirm
        self.onError = onError
    }
}

extension View {
    /// Presents `request` while non-nil. `isRunning` is true from the confirm
    /// tap until the action finishes — disable the trigger while it is; a
    /// request raised meanwhile is dropped (see `ConfirmRequest`).
    func typedConfirmAlert(_ request: Binding<TypedConfirmRequest?>, isRunning: Binding<Bool> = .constant(false)) -> some View {
        modifier(TypedConfirmAlert(request: request, isRunning: isRunning))
    }
}

/// The alert behind `.typedConfirmAlert(_:)` — a text field whose content
/// must match the phrase before the confirm button enables.
struct TypedConfirmAlert: ViewModifier {
    @Binding var request: TypedConfirmRequest?
    @Binding var isRunning: Bool
    @State private var typed = ""
    @State private var runner = ConfirmRunner()

    init(request: Binding<TypedConfirmRequest?>, isRunning: Binding<Bool> = .constant(false)) {
        _request = request
        _isRunning = isRunning
    }

    func body(content: Content) -> some View {
        content
            .alert(
                request?.title ?? "",
                isPresented: Binding(
                    get: { request != nil && !runner.isRunning },
                    set: { if !$0 { request = nil } }
                ),
                presenting: request
            ) { r in
                TextField(r.phrase, text: $typed)
                    .font(.body.monospaced())
                    .textInputAutocapitalization(.never)
                    .autocorrectionDisabled()
                Button(r.confirmLabel, role: r.role) {
                    runner.run(r.onConfirm, onError: r.onError, isRunning: $isRunning)
                }
                .disabled(!TypedConfirmMatcher.matches(typed, phrase: r.phrase))
                Button("Cancel", role: .cancel) {}
            } message: { r in
                Text("\(r.body)\n\n\(r.label ?? "Type \"\(r.phrase)\" to confirm")")
            }
            .onChange(of: request?.id) { _, id in
                typed = ""
                if id != nil, runner.isRunning { request = nil }
            }
            .toast($runner.failure)
    }
}
