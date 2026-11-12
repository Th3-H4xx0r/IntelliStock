import SwiftUI

extension View {
    /// Presents the "Update Available" sheet whenever `center` has an offer to
    /// show. Attached to the signed-in shell, so it never rises over Connect,
    /// Login or the lock.
    func appUpdatePresenter(_ center: AppUpdateCenter) -> some View {
        modifier(AppUpdatePresenter(center: center))
    }
}

private struct AppUpdatePresenter: ViewModifier {
    let center: AppUpdateCenter

    func body(content: Content) -> some View {
        content.sheet(item: Binding(
            get: { center.presented },
            set: { if $0 == nil { center.sheetDismissed() } }
        )) { offer in
            AppUpdateSheet(
                offer: offer,
                installing: center.installing,
                error: center.error,
                onInstall: { Task { await center.install() } },
                onLater: { center.later() }
            )
        }
    }
}

/// The sheet: the app's mark, the build, what's new, and one prominent action —
/// in the manner of the system's own update prompts. Separate from the
/// presenter so it can be drawn without a live center.
struct AppUpdateSheet: View {
    let offer: AppUpdateOffer
    var installing = false
    var error: String?
    var onInstall: () -> Void = {}
    var onLater: () -> Void = {}

    var body: some View {
        VStack(spacing: 0) {
            ScrollView {
                AppUpdateSheetContent(offer: offer)
                    .padding(.horizontal, 24)
                    .padding(.top, 32)
                    .padding(.bottom, 16)
            }
            .scrollBounceBehavior(.basedOnSize)
            AppUpdateSheetActions(installing: installing, error: error, onInstall: onInstall, onLater: onLater)
                .padding(.horizontal, 24)
                .padding(.bottom, 8)
        }
        .presentationDetents([.medium, .large])
        .presentationDragIndicator(.visible)
    }
}

/// The header and the notes.
struct AppUpdateSheetContent: View {
    let offer: AppUpdateOffer

    var body: some View {
        VStack(spacing: 24) {
            VStack(spacing: 6) {
                AppLogoView(size: 64)
                    .padding(.bottom, 8)
                Text("Update Available")
                    .font(.title2.bold())
                Text(offer.subtitle)
                    .font(.subheadline)
                    .foregroundStyle(.secondary)
                    .multilineTextAlignment(.center)
            }
            .frame(maxWidth: .infinity)
            .accessibilityElement(children: .combine)

            let items = offer.noteItems
            if !items.isEmpty {
                VStack(alignment: .leading, spacing: 10) {
                    Text("What\u{2019}s New")
                        .font(.headline)
                    ForEach(Array(items.enumerated()), id: \.offset) { _, item in
                        HStack(alignment: .firstTextBaseline, spacing: 10) {
                            Text(verbatim: "•")
                                .foregroundStyle(.secondary)
                                .accessibilityHidden(true)
                            Text(verbatim: item)
                                .fixedSize(horizontal: false, vertical: true)
                        }
                        .font(.body)
                    }
                }
                .frame(maxWidth: .infinity, alignment: .leading)
            }
        }
    }
}

/// Install Now and Later, with the last install error above them.
struct AppUpdateSheetActions: View {
    var installing = false
    var error: String?
    var onInstall: () -> Void = {}
    var onLater: () -> Void = {}

    var body: some View {
        VStack(spacing: 4) {
            if let error {
                Label(error, systemImage: "exclamationmark.circle.fill")
                    .font(.footnote)
                    .foregroundStyle(DS.Palette.danger)
                    .frame(maxWidth: .infinity, alignment: .leading)
                    .padding(.bottom, 8)
            }
            Button(action: onInstall) {
                Group {
                    if installing {
                        ProgressView()
                    } else {
                        Text("Install Now")
                    }
                }
                .frame(maxWidth: .infinity)
            }
            .dsProminentButton()
            .controlSize(.large)
            .disabled(installing)
            .accessibilityLabel(installing ? "Installing" : "Install Now")

            Button("Later", action: onLater)
                .frame(maxWidth: .infinity, minHeight: 44)
                .contentShape(Rectangle())
        }
    }
}

#Preview("Update available") {
    Color(uiColor: .systemGroupedBackground)
        .sheet(isPresented: .constant(true)) {
            AppUpdateSheet(offer: AppUpdateOffer(
                build: 263161830, version: "1.0.0",
                notes: "• Open orders and order history on the dashboard\n• Over-the-air updates",
                installURL: URL(string: "itms-services://x")!, size: 12_345_678))
        }
}
