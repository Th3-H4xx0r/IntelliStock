import SwiftUI

/// The auth flow's main action — `AuthPillButton` in `auth_pill_button.dart`,
/// shared by the lock and login screens so they cannot drift.
///
/// The web's primary button, full width at 44 pt (`.dsPrimary(size:
/// .large)`). Both ends reserve the same width so the label stays centred
/// with or without a leading glyph.
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
                            .font(.system(size: 18))
                    }
                }
                .frame(width: endWidth, alignment: .leading)

                Group {
                    if busy, let busyLabel {
                        HStack(spacing: 10) {
                            ProgressView()
                                .tint(DS.Palette.onAccent)
                            Text(busyLabel)
                        }
                    } else {
                        Text(label)
                    }
                }
                .frame(maxWidth: .infinity)

                Group {
                    if showChevron {
                        Image(systemName: Symbol.named("chevron_right"))
                            .font(.system(size: 14, weight: .medium))
                    }
                }
                .frame(width: endWidth, alignment: .trailing)
                .accessibilityHidden(true)
            }
            .padding(.horizontal, -8)
        }
        .buttonStyle(.dsPrimary(size: .large, fullWidth: true))
        .disabled(busy)
        .opacity(busy && busyLabel == nil ? 0.6 : 1)
    }
}
