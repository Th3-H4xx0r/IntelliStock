import SwiftUI

/// A transient confirmation — the native form of a SnackBar. Shows as a glass
/// capsule at the top for 2.5 s (tap to dismiss early) and is announced to
/// VoiceOver. Use inline `ErrorRow`s, not toasts, for failures that need
/// action (`feedback.md`).
nonisolated struct Toast: Identifiable, Equatable, Sendable {
    enum Style: Sendable {
        case info, success, error
    }

    let id = UUID()
    let message: String
    var style: Style = .info

    init(_ message: String, style: Style = .info) {
        self.message = message
        self.style = style
    }

    static let duration: Duration = .milliseconds(2500)
}

extension View {
    /// Presents `toast` while non-nil; clears it after `Toast.duration`.
    func toast(_ toast: Binding<Toast?>) -> some View {
        modifier(ToastModifier(toast: toast))
    }
}

private struct ToastModifier: ViewModifier {
    @Binding var toast: Toast?

    func body(content: Content) -> some View {
        content
            .overlay(alignment: .top) {
                if let current = toast {
                    ToastCapsule(toast: current)
                        .padding(.top, 8)
                        .padding(.horizontal, 16)
                        .transition(.move(edge: .top).combined(with: .opacity))
                        .onTapGesture { toast = nil }
                        .task(id: current.id) {
                            AccessibilityNotification.Announcement(current.message).post()
                            try? await Task.sleep(for: Toast.duration)
                            if toast?.id == current.id { toast = nil }
                        }
                }
            }
            .animation(.snappy, value: toast)
    }
}

private struct ToastCapsule: View {
    let toast: Toast

    var body: some View {
        HStack(spacing: 8) {
            Image(systemName: symbol)
                .foregroundStyle(tint)
            Text(toast.message)
                .font(.subheadline.weight(.medium))
                .multilineTextAlignment(.leading)
        }
        .padding(.horizontal, 16)
        .padding(.vertical, 12)
        .glassEffect(.regular, in: .capsule)
        .accessibilityElement(children: .combine)
    }

    private var symbol: String {
        switch toast.style {
        case .info: Symbol.named("info")
        case .success: Symbol.named("check_circle")
        case .error: Symbol.named("error")
        }
    }

    private var tint: Color {
        switch toast.style {
        case .info: .secondary
        case .success: DS.Palette.success
        case .error: DS.Palette.danger
        }
    }
}
