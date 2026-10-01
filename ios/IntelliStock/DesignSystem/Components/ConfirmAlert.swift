import SwiftUI

/// A confirmation request — the native form of `showConfirmDialog` in
/// `confirm_dialog.dart`. Present it with `.confirmAlert($request)`.
///
/// Native-form changes: an `.alert` (or a `.confirmationDialog` for an
/// action sheet) with Cancel and a role replaces the custom dialog; the icon
/// header is dropped; `confirmColor` becomes `role` (danger → destructive,
/// anything else → default). The system dismisses the alert on tap, so a
/// failing `onConfirm` reports through `onError` instead of keeping a dialog
/// open with a spinner.
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

    fileprivate func confirm() {
        Task {
            do {
                try await onConfirm()
            } catch {
                onError?(error)
            }
        }
    }
}

extension View {
    /// Presents `request` while non-nil, as an alert or an action sheet.
    func confirmAlert(_ request: Binding<ConfirmRequest?>) -> some View {
        modifier(ConfirmAlert(request: request))
    }
}

/// The alert or action sheet behind `.confirmAlert(_:)`.
struct ConfirmAlert: ViewModifier {
    @Binding var request: ConfirmRequest?

    func body(content: Content) -> some View {
        content
            .alert(
                request?.title ?? "",
                isPresented: isPresented(.alert),
                presenting: request
            ) { r in
                Button(r.confirmLabel, role: r.role) { r.confirm() }
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
                Button(r.confirmLabel, role: r.role) { r.confirm() }
                Button("Cancel", role: .cancel) {}
            } message: { r in
                Text(r.body)
            }
    }

    private func isPresented(_ kind: ConfirmRequest.Presentation) -> Binding<Bool> {
        Binding(
            get: { request?.presentation == kind },
            set: { if !$0 { request = nil } }
        )
    }
}
