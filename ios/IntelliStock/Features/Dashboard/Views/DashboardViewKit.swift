import SafariServices
import SwiftUI

// Small view pieces shared by the dashboard, stock and search screens.

/// An SF Symbol for a Material name, with a local fallback where `Symbol`
/// has no mapping yet (requested in the trading report).
func dashboardSymbol(_ material: String, fallback: String) -> String {
    let sf = Symbol.named(material)
    return sf == Symbol.fallback ? fallback : sf
}

/// The allocation ring (`_AllocationRing`, `_DiversityGauge`) now lives in the
/// design system as `AllocationRing`; the name stays so call sites compile.
/// New code uses `AllocationRing`, or `MiniAllocationRing` in a row.
typealias DashboardAllocationRing = AllocationRing

/// The mini price line (`_MiniSpark`) now lives in the design system as
/// `Sparkline`; the name stays so call sites compile.
typealias DashboardMiniSpark = Sparkline

/// A flat horizontal bar on a faint track (`LinearProgressIndicator`).
struct DashboardBar: View {
    let fraction: Double
    let color: Color
    var height: CGFloat = 5

    var body: some View {
        GeometryReader { geo in
            ZStack(alignment: .leading) {
                Capsule().fill(Color(uiColor: .systemFill))
                Capsule()
                    .fill(color)
                    .frame(width: geo.size.width * min(max(fraction, 0), 1))
            }
        }
        .frame(height: height)
        .accessibilityHidden(true)
    }
}

/// The in-app browser for news links (`LaunchMode.inAppBrowserView`).
struct DashboardSafariView: UIViewControllerRepresentable {
    let url: URL

    func makeUIViewController(context: Context) -> SFSafariViewController {
        SFSafariViewController(url: url)
    }

    func updateUIViewController(_ controller: SFSafariViewController, context: Context) {}
}

/// `_openInAppBrowser`'s guard: a trimmed http(s) URL, else nil (no-op).
func dashboardBrowserURL(_ raw: String) -> URL? {
    let u = raw.trimmingCharacters(in: .whitespacesAndNewlines)
    guard !u.isEmpty, let url = URL(string: u), let scheme = url.scheme?.lowercased(),
          scheme == "http" || scheme == "https"
    else { return nil }
    return url
}

/// A URL to present in `DashboardSafariView` with `.sheet(item:)`.
struct DashboardBrowserLink: Identifiable {
    let url: URL
    var id: String { url.absoluteString }
}
