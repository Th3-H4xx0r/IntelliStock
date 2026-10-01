import SwiftUI

/// The auth flow's main action — `AuthPillButton` in `auth_pill_button.dart`,
/// shared by the lock and login screens so they cannot drift.
///
/// Native form: a full-width `.glassProminent` capsule at the large control
/// size (a floating, functional control, so Liquid Glass is allowed). Both
/// ends reserve the same width so the label stays centred with or without a
/// leading glyph.
struct AuthPillButton: View {
    let label: String
    /// SF Symbol shown before the label (`faceid`, `touchid`).
    var systemImage: String?
    var showChevron = true
    var busy = false
    /// When set, `busy` swaps the label for a spinner and this text. When nil,
    /// a busy button just dims (a system prompt is already on screen).
    var busyLabel: String?
    let action: () -> Void

    private let endWidth: CGFloat = 26

    var body: some View {
        Button(action: action) {
            HStack(spacing: 0) {
                Group {
                    if let systemImage {
                        Image(systemName: systemImage)
                            .font(.title3)
                    }
                }
                .frame(width: endWidth, alignment: .leading)

                Group {
                    if busy, let busyLabel {
                        HStack(spacing: 10) {
                            ProgressView()
                            Text(busyLabel)
                        }
                    } else {
                        Text(label)
                    }
                }
                .font(.headline)
                .frame(maxWidth: .infinity)

                Group {
                    if showChevron {
                        Image(systemName: Symbol.named("chevron_right"))
                            .font(.subheadline.weight(.semibold))
                    }
                }
                .frame(width: endWidth, alignment: .trailing)
                .accessibilityHidden(true)
            }
            .padding(.horizontal, 8)
            .frame(minHeight: 44)
        }
        .buttonStyle(.glassProminent)
        .controlSize(.large)
        .disabled(busy)
        .opacity(busy && busyLabel == nil ? 0.6 : 1)
    }
}
