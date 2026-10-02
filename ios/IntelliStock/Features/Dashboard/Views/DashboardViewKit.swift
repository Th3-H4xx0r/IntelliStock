import Charts
import SafariServices
import SwiftUI

// Small view pieces shared by the dashboard, stock and search screens.

/// An SF Symbol for a Material name, with a local fallback where `Symbol`
/// has no mapping yet (requested in the trading report).
func dashboardSymbol(_ material: String, fallback: String) -> String {
    let sf = Symbol.named(material)
    return sf == Symbol.fallback ? fallback : sf
}

/// The small upper-case card eyebrow (`_tileLabel` / `_label`).
struct DashboardEyebrow: View {
    let text: String

    init(_ text: String) {
        self.text = text
    }

    var body: some View {
        Text(text)
            .font(.caption2.weight(.bold))
            .tracking(0.8)
            .foregroundStyle(.secondary)
            .accessibilityAddTraits(.isHeader)
    }
}

/// A card header: a tinted glyph and an eyebrow, with optional trailing text.
struct DashboardCardHeader: View {
    let symbol: String
    let tint: Color
    let title: String
    var trailing: String?

    var body: some View {
        HStack(spacing: 6) {
            Image(systemName: symbol)
                .font(.footnote)
                .foregroundStyle(tint)
                .accessibilityHidden(true)
            DashboardEyebrow(title)
            if let trailing {
                Spacer(minLength: 8)
                Text(trailing)
                    .font(.caption2)
                    .foregroundStyle(.secondary)
            }
        }
    }
}

/// A circular allocation ring: the arc is this item's share of the
/// portfolio, with the percentage in the centre (`_AllocationRing`,
/// `_DiversityGauge`). A flat stroke, no gradient.
struct DashboardAllocationRing: View {
    let fraction: Double
    let color: Color
    var size: CGFloat = 44
    var lineWidth: CGFloat = 3.5
    var labelColor: Color?

    var body: some View {
        let f = min(max(fraction, 0), 1)
        ZStack {
            Circle()
                .stroke(Color(uiColor: .systemFill), lineWidth: lineWidth)
            Circle()
                .trim(from: 0, to: f)
                .stroke(color, style: StrokeStyle(lineWidth: lineWidth, lineCap: .round))
                .rotationEffect(.degrees(-90))
            Text(DashboardFormat.allocationLabel(fraction))
                .font(.caption2.weight(.bold))
                .monospacedDigit()
                .minimumScaleFactor(0.7)
                .lineLimit(1)
                .foregroundStyle(labelColor ?? color)
                .padding(.horizontal, 4)
        }
        .padding(lineWidth / 2)
        .frame(width: size, height: size)
        .accessibilityElement(children: .ignore)
        .accessibilityLabel("\(DashboardFormat.allocationLabel(fraction)) of portfolio")
    }
}

/// A tiny price line (`_MiniSpark`): green when the last value is at or
/// above the first, red otherwise. Draws itself in from the left on appear;
/// give it a new `.id` to replay.
struct DashboardMiniSpark: View {
    let values: [Double]
    var height: CGFloat = 28

    @State private var progress: CGFloat = 0
    @Environment(\.accessibilityReduceMotion) private var reduceMotion

    var body: some View {
        if values.count < 2 {
            Color.clear.frame(height: height)
        } else {
            let up = values[values.count - 1] >= values[0]
            let lo = values.min()!
            let hi = values.max()!
            let span = abs(hi - lo) < 1e-9 ? 1 : hi - lo
            Chart {
                ForEach(values.indices, id: \.self) { i in
                    LineMark(x: .value("i", i), y: .value("v", values[i]))
                        .foregroundStyle(up ? DS.Palette.up : DS.Palette.down)
                        .lineStyle(StrokeStyle(lineWidth: 1.5, lineCap: .round, lineJoin: .round))
                }
            }
            .chartXAxis(.hidden)
            .chartYAxis(.hidden)
            .chartLegend(.hidden)
            .chartXScale(domain: 0...(values.count - 1))
            .chartYScale(domain: lo...(lo + span))
            .chartPlotStyle { $0.padding(.vertical, 2) }
            .mask(alignment: .leading) {
                GeometryReader { geo in
                    Rectangle().frame(width: geo.size.width * progress)
                }
            }
            .frame(height: height)
            .accessibilityHidden(true)
            .onAppear {
                if reduceMotion {
                    progress = 1
                } else {
                    withAnimation(.easeOut(duration: 0.65)) { progress = 1 }
                }
            }
        }
    }
}

/// A coloured tag on a 15 % tint of its colour (side chips, event types).
struct DashboardTintTag: View {
    let text: String
    let color: Color
    var weight: Font.Weight = .heavy

    @Environment(\.colorScheme) private var colorScheme

    var body: some View {
        Text(text)
            .font(.caption2.weight(weight))
            .foregroundStyle(DS.Palette.onTint(color, in: colorScheme))
            .padding(.horizontal, 7)
            .padding(.vertical, 3)
            .background(color.opacity(DS.tintFill), in: .rect(cornerRadius: 5, style: .continuous))
    }
}

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
