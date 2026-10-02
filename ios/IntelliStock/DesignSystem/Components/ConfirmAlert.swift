import SwiftUI

/// A confirmation request — the native form of `showConfirmDialog` in
/// `confirm_dialog.dart`. Present it with `.confirmAlert($request)`.
///
/// Native-form changes: an `.alert` (or a `.confirmationDialog` for an
/// action sheet) with Cancel and a role replaces the custom dialog; the icon
/// header is dropped; `confirmColor` becomes `role` (danger → destructive,
/// anything else → default).
///
/// The system dismisses the alert on tap, so where Dart kept the dialog open
/// with a spinner until `onConfirm` finished, the modifier exposes
/// `isRunning` instead. Bind it and disable the button that raises the
/// request while it is true:
///
///     @State private var confirm: ConfirmRequest?
///     @State private var rerunning = false
///
///     Button("Rerun") { confirm = ConfirmRequest(title: …, body: …) { try await model.rerun() } }
///         .disabled(rerunning)
///         .confirmAlert($confirm, isRunning: $rerunning)
///
/// A request raised while an action is still running is dropped, so a
/// confirmed action never runs twice in parallel. A failure goes to
/// `onError`, or, when there is none, to an error toast.
struct ConfirmRequest: Identifiable {
    enum Presentation {
        case alert
        case actionSheet
    }

    let id = UUID()
    let title: String
    let body: String
    var confirmLabel = "Confirm"
    var role: ButtonRole? = .destructive
    var presentation: Presentation = .alert
    let onConfirm: () async throws -> Void
    var onError: ((any Error) -> Void)?

    init(
        title: String,
        body: String,
        confirmLabel: String = "Confirm",
        role: ButtonRole? = .destructive,
        presentation: Presentation = .alert,
        onConfirm: @escaping () async throws -> Void,
        onError: ((any Error) -> Void)? = nil
    ) {
        self.title = title
        self.body = body
        self.confirmLabel = confirmLabel
        self.role = role
        self.presentation = presentation
        self.onConfirm = onConfirm
        self.onError = onError
    }
}

extension View {
    /// Presents `request` while non-nil, as an alert or an action sheet.
    /// `isRunning` is true from the confirm tap until the action finishes.
    func confirmAlert(_ request: Binding<ConfirmRequest?>, isRunning: Binding<Bool> = .constant(false)) -> some View {
        modifier(ConfirmAlert(request: request, isRunning: isRunning))
    }
}

/// The alert or action sheet behind `.confirmAlert(_:isRunning:)`.
struct ConfirmAlert: ViewModifier {
    @Binding var request: ConfirmRequest?
    @Binding var isRunning: Bool
    @State private var runner = ConfirmRunner()

    init(request: Binding<ConfirmRequest?>, isRunning: Binding<Bool> = .constant(false)) {
        _request = request
        _isRunning = isRunning
    }

    private func confirm(_ r: ConfirmRequest) {
        runner.run(r.onConfirm, onError: r.onError, isRunning: $isRunning)
    }

    func body(content: Content) -> some View {
        content
            .alert(
                request?.title ?? "",
                isPresented: isPresented(.alert),
                presenting: request
            ) { r in
                Button(r.confirmLabel, role: r.role) { confirm(r) }
                Button("Cancel", role: .cancel) {}
            } message: { r in
                Text(r.body)
            }
            .confirmationDialog(
                request?.title ?? "",
                isPresented: isPresented(.actionSheet),
                titleVisibility: .visible,
                presenting: request
            ) { r in
                Button(r.confirmLabel, role: r.role) { confirm(r) }
                Button("Cancel", role: .cancel) {}
            } message: { r in
                Text(r.body)
            }
            // A second request while the first action runs is dropped.
            .onChange(of: request?.id) { _, id in
                if id != nil, runner.isRunning { request = nil }
            }
            .toast($runner.failure)
    }

    private func isPresented(_ kind: ConfirmRequest.Presentation) -> Binding<Bool> {
        Binding(
            get: { request?.presentation == kind && !runner.isRunning },
            set: { if !$0 { request = nil } }
        )
    }
}

/// Runs a confirmed action at most once at a time, mirrors that into the
/// caller's `isRunning`, and turns an unhandled failure into an error toast.
/// Shared by `ConfirmAlert` and `TypedConfirmAlert`.
@Observable
final class ConfirmRunner {
    private(set) var isRunning = false
    /// The error toast for a failure the request did not handle.
    var failure: Toast?

    func run(
        _ action: @escaping () async throws -> Void,
        onError: ((any Error) -> Void)?,
        isRunning binding: Binding<Bool>
    ) {
        guard !isRunning else { return }
        isRunning = true
        binding.wrappedValue = true
        Task {
            do {
                try await action()
            } catch where error.isCancellation {
                // Not a failure.
            } catch {
                if let onError {
                    onError(error)
                } else {
                    failure = Toast((error as? ApiError)?.message ?? error.localizedDescription, style: .error)
                }
            }
            isRunning = false
            binding.wrappedValue = false
        }
    }
}
