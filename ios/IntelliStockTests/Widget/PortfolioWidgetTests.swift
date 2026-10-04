import Foundation
import SwiftUI
import Testing
import UIKit
import WidgetKit

// The widget's shared sources (ios/WidgetShared) are compiled into this
// bundle, so these tests reach them without importing the extension.

// MARK: - Series cleaning

struct PortfolioSeriesTests {
    private func series(_ values: [Double], start: Double = 1_790_000_000, step: Double = 900) -> [SeriesPoint] {
        values.enumerated().map { SeriesPoint(t: start + Double($0.offset) * step, v: $0.element) }
    }

    @Test func dropsZerosNonFiniteAndSortsAndDedupes() {
        let raw = [
            SeriesPoint(t: 300, v: 12), SeriesPoint(t: 100, v: 0), SeriesPoint(t: 200, v: .nan),
            SeriesPoint(t: 400, v: 13), SeriesPoint(t: 300, v: 11), SeriesPoint(t: 0, v: 10),
            SeriesPoint(t: 500, v: -1), SeriesPoint(t: 600, v: 14),
        ]
        #expect(PortfolioSeries.clean(raw) == [
            SeriesPoint(t: 300, v: 11), SeriesPoint(t: 400, v: 13), SeriesPoint(t: 600, v: 14),
        ])
    }

    /// The operator's weekend series: four after-hours marks, Alpaca's
    /// overnight re-mark, a flat day, and the live tip. The leading step was
    /// the "vertical spike" at the chart's left edge.
    @Test func dropsTheLeadingAfterHoursStep() {
        let raw = series(Array(repeating: 10_133.24, count: 4) + Array(repeating: 10_122.59, count: 92) + [10_123.41])
        let cleaned = PortfolioSeries.clean(raw)
        #expect(cleaned.count == 93)
        #expect(cleaned.first?.v == 10_122.59)
        #expect(cleaned.last?.v == 10_123.41)
    }

    @Test func dropsAStaleLeadBeforeATimeGap() {
        var raw = [SeriesPoint(t: 1_000, v: 50)]
        raw += series(Array(stride(from: 100.0, to: 112.0, by: 1)), start: 100_000)
        let cleaned = PortfolioSeries.clean(raw)
        #expect(cleaned.first == SeriesPoint(t: 100_000, v: 100))
        #expect(cleaned.count == 12)
    }

    @Test func keepsARealMoveAndATrendingStart() {
        // A drop a quarter of the way in (the open) stays.
        let open = series(Array(repeating: 100, count: 24) + Array(repeating: 96, count: 72))
        #expect(PortfolioSeries.clean(open).count == 96)
        // A steady trend has no dominant leading step.
        let trend = series((0..<40).map { 100 + Double($0) * 0.5 })
        #expect(PortfolioSeries.clean(trend).count == 40)
        // A late live tip on a flat day stays.
        let tip = series(Array(repeating: 100, count: 40) + [100.5])
        #expect(PortfolioSeries.clean(tip).count == 41)
    }

    @Test func domainHasAMinimumSpanForAFlatDay() throws {
        let flat = series(Array(repeating: 10_000, count: 10) + [10_000.82])
        let d = try #require(PortfolioSeries.domain(for: flat))
        // At least 0.2% of the level, so $0.82 draws as a near-flat line.
        #expect(d.upperBound - d.lowerBound >= 20)
        #expect(d.contains(10_000) && d.contains(10_000.82))

        let wide = series([100, 110, 90, 105])
        let w = try #require(PortfolioSeries.domain(for: wide))
        #expect(w.lowerBound < 90 && w.lowerBound > 85)
        #expect(w.upperBound > 110 && w.upperBound < 115)

        #expect(PortfolioSeries.domain(for: series([5])) == nil)
    }
}

// MARK: - Formatting

struct PortfolioFormatTests {
    @Test func money() {
        #expect(PortfolioFormat.money(10_123.41) == "$10,123.41")
        #expect(PortfolioFormat.money(-12) == "\u{2212}$12.00")
        #expect(PortfolioFormat.signedMoney(-9.83) == "\u{2212}$9.83")
        #expect(PortfolioFormat.signedMoney(0) == "+$0.00")
        #expect(PortfolioFormat.signedPercent(1.0749) == "+1.07%")
        #expect(PortfolioFormat.signedPercent(-0.097) == "\u{2212}0.10%")
        #expect(PortfolioFormat.change(abs: -9.83, pct: -0.097) == "\u{2212}$9.83 (\u{2212}0.10%)")
        #expect(PortfolioFormat.arrow(up: true) == "arrow.up.right")
        #expect(PortfolioFormat.arrow(up: false) == "arrow.down.right")
    }

    @Test func accessibilityLabel() {
        #expect(PortfolioFormat.accessibilityLabel(name: "alpaca paper", value: 10_123.41, changePct: -0.097)
            == "Alpaca Paper, $10,123.41, down 0.10% today")
        #expect(PortfolioFormat.accessibilityLabel(name: "Main", value: 5, changePct: 1.2)
            == "Main, $5.00, up 1.20% today")
    }

    @Test func displayNameRaisesWithoutForcingCaps() {
        #expect(PortfolioFormat.displayName("Strategy EB lab (backtest only)") == "Strategy EB Lab (Backtest Only)")
        #expect(PortfolioFormat.displayName("swing trader (paper)") == "Swing Trader (Paper)")
        #expect(PortfolioFormat.displayName("the state of the art") == "The State of the Art")
        #expect(PortfolioFormat.displayName("Alpaca Paper — FOMC") == "Alpaca Paper — FOMC")
    }

    @Test func updatedShowsATimeTodayAndAWeekdayBefore() {
        var cal = Calendar(identifier: .gregorian)
        cal.timeZone = TimeZone(identifier: "America/New_York")!
        let locale = Locale(identifier: "en_US")
        let synced = cal.date(from: DateComponents(year: 2026, month: 10, day: 2, hour: 19, minute: 37))!
        let sameDay = synced.addingTimeInterval(3_600)
        let nextDay = synced.addingTimeInterval(86_400)
        #expect(PortfolioFormat.updated(synced, now: sameDay, calendar: cal, locale: locale)
            .replacingOccurrences(of: "\u{202F}", with: " ") == "Updated 7:37 PM")
        #expect(PortfolioFormat.updated(synced, now: nextDay, calendar: cal, locale: locale)
            .replacingOccurrences(of: "\u{202F}", with: " ") == "Updated Fri 7:37 PM")
    }
}

// MARK: - Decoding

struct PortfolioDecodeTests {
    @Test func decodesAccountsHoldingsAndCleansPoints() {
        let raw = """
        [{"id":"a","label":"alpaca paper","accountValue":10123.41,"dayPnlAbs":-9.83,"dayPnlPct":-0.097,
          "intradayPoints":[{"t":200,"v":0},{"t":100,"v":10133.24},{"t":300,"v":10123.41}],
          "positions":[{"symbol":"XLE","unrealizedPnlPct":-0.74,"marketValue":200},
                       {"symbol":"GLD","unrealizedPnlPct":-6.39,"marketValue":900},
                       {"symbol":"","unrealizedPnlPct":1,"marketValue":5000}]},
         {"id":"b","label":"","accountValue":5}]
        """
        let synced = Date(timeIntervalSince1970: 1_000)
        let all = PortfolioSnapshot.decodeAccounts(raw, syncedAt: synced)
        #expect(all.count == 2)
        #expect(all[0].name == "alpaca paper")
        #expect(all[0].holdings.map(\.symbol) == ["GLD", "XLE"])
        #expect(all[0].points.map(\.v) == [10_133.24, 10_123.41])
        #expect(all[0].isUp == false)
        #expect(all[0].syncedAt == synced)
        #expect(all[1].name == "b")
        #expect(PortfolioSnapshot.decodeAccounts("not json", syncedAt: nil).isEmpty)
        #expect(PortfolioSnapshot.decodeAccounts(nil, syncedAt: nil).isEmpty)
    }

    @Test func decodesInstances() {
        let items = InstanceItem.decode(#"[{"name":"Strategy EB","running":true,"pnlPct":1.5},{"name":""},{"running":true}]"#)
        #expect(items == [InstanceItem(name: "Strategy EB", running: true, pnlPct: 1.5)])
    }
}

// MARK: - Rendering

/// Renders every family in light and dark with `ImageRenderer`. Set
/// WIDGET_SNAPSHOT_DIR (TEST_RUNNER_WIDGET_SNAPSHOT_DIR through xcodebuild)
/// to also write the PNGs there.
@MainActor
struct PortfolioWidgetRenderTests {
    private static let small = CGSize(width: 170, height: 170)
    private static let medium = CGSize(width: 364, height: 170)
    private static let large = CGSize(width: 364, height: 382)
    private static let rect = CGSize(width: 172, height: 76)

    private func render(_ content: some View, size: CGSize, scheme: ColorScheme, margin: CGFloat = 16) -> UIImage? {
        let view = content
            .padding(margin)
            .frame(width: size.width, height: size.height)
            .background(Color(uiColor: .systemBackground))
            .clipShape(RoundedRectangle(cornerRadius: 24, style: .continuous))
            .padding(16)
            .background(Color(uiColor: .systemGroupedBackground))
            .environment(\.colorScheme, scheme)
        let renderer = ImageRenderer(content: view)
        renderer.scale = 3
        return renderer.uiImage
    }

    private func save(_ image: UIImage, _ name: String) throws {
        guard let dir = ProcessInfo.processInfo.environment["WIDGET_SNAPSHOT_DIR"], !dir.isEmpty else { return }
        let url = URL(fileURLWithPath: dir, isDirectory: true)
        try FileManager.default.createDirectory(at: url, withIntermediateDirectories: true)
        try image.pngData()?.write(to: url.appendingPathComponent("\(name).png"))
    }

    private var now: Date { PortfolioSnapshot.sample.syncedAt!.addingTimeInterval(600) }

    @Test(arguments: [ColorScheme.light, .dark])
    func rendersEveryFamily(scheme: ColorScheme) throws {
        let tag = scheme == .dark ? "dark" : "light"
        let s = PortfolioSnapshot.sample
        let cases: [(String, WidgetFamily, CGSize)] = [
            ("small", .systemSmall, Self.small),
            ("medium", .systemMedium, Self.medium),
            ("large", .systemLarge, Self.large),
        ]
        for (name, family, size) in cases {
            let image = try #require(render(PortfolioWidgetContent(snapshot: s, family: family, now: now), size: size, scheme: scheme))
            #expect(image.size.width == size.width + 32)
            try save(image, "portfolio-\(name)-\(tag)")
        }

        // Up day, tinted (monochrome) medium, empty state, Lock Screen, instances.
        let up = PortfolioSnapshot(id: "u", name: "swing trader (paper)", value: 99_515.10, changeAbs: 412.5,
                                   changePct: 0.416, points: s.points.reversed().enumerated().map {
                                       SeriesPoint(t: s.points[$0.offset].t, v: $0.element.v + 89_400)
                                   },
                                   holdings: s.holdings.map { HoldingItem(symbol: $0.symbol, pnlPct: abs($0.pnlPct), marketValue: $0.marketValue) },
                                   syncedAt: s.syncedAt)
        try save(try #require(render(PortfolioWidgetContent(snapshot: up, family: .systemMedium, now: now), size: Self.medium, scheme: scheme)),
                 "portfolio-medium-up-\(tag)")
        try save(try #require(render(PortfolioWidgetContent(snapshot: s, family: .systemMedium, now: now, fullColor: false),
                                     size: Self.medium, scheme: scheme)), "portfolio-medium-mono-\(tag)")
        try save(try #require(render(PortfolioWidgetContent(snapshot: nil, family: .systemSmall, now: now), size: Self.small, scheme: scheme)),
                 "portfolio-small-empty-\(tag)")
        try save(try #require(render(PortfolioWidgetContent(snapshot: s, family: .accessoryRectangular, now: now, fullColor: false),
                                     size: Self.rect, scheme: scheme, margin: 4)), "portfolio-rect-\(tag)")
        let items = [
            InstanceItem(name: "Alpaca Live Main", running: true, pnlPct: 0.58),
            InstanceItem(name: "swing-paper", running: true, pnlPct: -0.02),
            InstanceItem(name: "Strategy EB lab (backtest only)", running: false, pnlPct: 0),
            InstanceItem(name: "Strategy X", running: false, pnlPct: 0),
        ]
        try save(try #require(render(InstanceStatusContent(items: items, family: .systemSmall), size: Self.small, scheme: scheme)),
                 "instances-small-\(tag)")
        try save(try #require(render(InstanceStatusContent(items: items, family: .systemMedium), size: Self.medium, scheme: scheme)),
                 "instances-medium-\(tag)")
    }
}
