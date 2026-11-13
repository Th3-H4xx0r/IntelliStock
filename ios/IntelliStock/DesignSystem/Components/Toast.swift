import SwiftUI

/// A transient confirmation. Shows at the top for 2.5 s (tap to dismiss
/// early) on the menu surface (`ui-menu`: the card fill, an 8% hairline, 12
/// radius, a soft drop shadow), and is announced to VoiceOver. Use inline
/// `ErrorRow`s, not toasts, for failures that need action.
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
            .animation(DSMotion.ease(0.2), value: toast)
    }
}

private struct ToastCapsule: View {
    let toast: Toast

    var body: some View {
        HStack(spacing: 10) {
            Image(systemName: symbol)
                .font(.system(size: 16))
                .foregroundStyle(tint)
            Text(toast.message)
                .font(DS.Font.body)
                .foregroundStyle(DS.Text.primary)
                .multilineTextAlignment(.leading)
        }
        .padding(.horizontal, 16)
        .padding(.vertical, 12)
        .background(DS.Surface.card, in: .rect(cornerRadius: DS.Radius.card, style: .continuous))
        .overlay {
            RoundedRectangle(cornerRadius: DS.Radius.card, style: .continuous)
                .strokeBorder(DS.Line.input, lineWidth: DS.Line.width)
        }
        .shadow(color: .black.opacity(0.5), radius: 20, y: 16)
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
        case .info: DS.Text.muted
        case .success: DS.Palette.gain
        case .error: DS.Palette.loss
        }
    }
}
