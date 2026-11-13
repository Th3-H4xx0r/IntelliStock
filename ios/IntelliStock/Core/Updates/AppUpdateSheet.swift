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

/// The sheet: the app's mark, the build, what's new, and one primary action,
/// in the web app's style (Inter, graphite, the off-white primary button).
/// Separate from the presenter so it can be drawn without a live center.
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
                .padding(.top, 12)
                .padding(.bottom, 8)
                .overlay(alignment: .top) { DSDivider() }
        }
        .background(DS.Surface.card)
        .presentationBackground(DS.Surface.card)
        .presentationDetents([.medium, .large])
        .presentationDragIndicator(.visible)
    }
}

/// The header and the notes.
struct AppUpdateSheetContent: View {
    let offer: AppUpdateOffer

    var body: some View {
        VStack(alignment: .leading, spacing: 24) {
            VStack(spacing: 4) {
                AppLogoView(size: 56)
                    .padding(.bottom, 12)
                Text("Update available")
                    .font(DS.Font.figure)
                    .foregroundStyle(DS.Text.primary)
                Text(offer.subtitle)
                    .font(DS.Font.body)
                    .foregroundStyle(DS.Text.muted)
                    .monospacedDigit()
                    .multilineTextAlignment(.center)
            }
            .frame(maxWidth: .infinity)
            .accessibilityElement(children: .combine)

            let items = offer.noteItems
            if !items.isEmpty {
                VStack(alignment: .leading, spacing: 0) {
                    Text("What\u{2019}s new")
                        .font(DS.Font.caption)
                        .foregroundStyle(DS.Text.muted)
                        .padding(.horizontal, 16)
                        .padding(.vertical, 10)
                        .frame(maxWidth: .infinity, alignment: .leading)
                        .overlay(alignment: .bottom) { DSDivider() }
                    ForEach(Array(items.enumerated()), id: \.offset) { i, item in
                        Text(verbatim: item)
                            .font(DS.Font.body)
                            .foregroundStyle(DS.Text.label)
                            .fixedSize(horizontal: false, vertical: true)
                            .frame(maxWidth: .infinity, alignment: .leading)
                            .padding(.horizontal, 16)
                            .padding(.vertical, 10)
                            .overlay(alignment: .top) { if i > 0 { DSDivider() } }
                    }
                }
                .dsCardSurface(fill: DS.Surface.page)
            }
        }
    }
}

/// Install now and Later, with the last install error above them.
struct AppUpdateSheetActions: View {
    var installing = false
    var error: String?
    var onInstall: () -> Void = {}
    var onLater: () -> Void = {}

    var body: some View {
        VStack(spacing: 4) {
            if let error {
                Text(error)
                    .font(DS.Font.small)
                    .foregroundStyle(DS.Palette.loss)
                    .frame(maxWidth: .infinity, alignment: .leading)
                    .padding(12)
                    .dsErrorSurface(radius: DS.Radius.control)
                    .padding(.bottom, 8)
            }
            Button(action: onInstall) {
                Group {
                    if installing {
                        ProgressView()
                            .controlSize(.small)
                            .tint(DS.Surface.page)
                    } else {
                        Text("Install now")
                    }
                }
                .frame(maxWidth: .infinity)
            }
            .buttonStyle(.dsPrimary)
            .disabled(installing)
            .accessibilityLabel(installing ? "Installing" : "Install now")

            Button("Later", action: onLater)
                .buttonStyle(.dsGhost)
                .frame(maxWidth: .infinity, minHeight: 44)
                .contentShape(Rectangle())
        }
    }
}

#Preview("Update available") {
    DS.Surface.page
        .sheet(isPresented: .constant(true)) {
            AppUpdateSheet(offer: AppUpdateOffer(
                build: 263161830, version: "1.0.0",
                notes: "• Open orders and order history on the dashboard\n• Over-the-air updates",
                installURL: URL(string: "itms-services://x")!, size: 12_345_678))
        }
}
