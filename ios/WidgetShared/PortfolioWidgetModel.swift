// The widget's data model and pure helpers: parsing the App-Group JSON,
// cleaning the intraday series, and formatting. No WidgetKit or SwiftUI here,
// so the unit tests compile this file directly (see project.yml).

import Foundation

/// One intraday sample: `t` epoch seconds, `v` account value.
nonisolated struct SeriesPoint: Equatable, Sendable {
    let t: Double
    let v: Double
}

nonisolated struct HoldingItem: Equatable, Sendable {
    let symbol: String
    let pnlPct: Double
    let marketValue: Double
}

/// One selectable portfolio, as the widget draws it.
nonisolated struct PortfolioSnapshot: Equatable, Sendable {
    let id: String
    let name: String
    let value: Double
    let changeAbs: Double
    let changePct: Double
    /// Cleaned with `PortfolioSeries.clean`, oldest first.
    let points: [SeriesPoint]
    /// Largest market value first.
    let holdings: [HoldingItem]
    let syncedAt: Date?

    var isUp: Bool { changeAbs >= 0 }

    /// Parses the `accounts_data` App-Group string (the `/widget/accounts`
    /// `accounts` array). Unreadable input is an empty list.
    static func decodeAccounts(_ raw: String?, syncedAt: Date?) -> [PortfolioSnapshot] {
        guard let raw, let data = raw.data(using: .utf8),
              let arr = (try? JSONSerialization.jsonObject(with: data)) as? [[String: Any]]
        else { return [] }
        return arr.map { j in
            let pts = (j["intradayPoints"] as? [[String: Any]] ?? [])
                .map { SeriesPoint(t: num($0["t"]), v: num($0["v"])) }
            let holdings = (j["positions"] as? [[String: Any]] ?? [])
                .map {
                    HoldingItem(symbol: $0["symbol"] as? String ?? "",
                                pnlPct: num($0["unrealizedPnlPct"]),
                                marketValue: num($0["marketValue"]))
                }
                .filter { !$0.symbol.isEmpty }
                .sorted { $0.marketValue > $1.marketValue }
            let id = j["id"] as? String ?? ""
            let label = (j["label"] as? String).flatMap { $0.isEmpty ? nil : $0 } ?? (id.isEmpty ? "Portfolio" : id)
            return PortfolioSnapshot(
                id: id, name: label,
                value: num(j["accountValue"]),
                changeAbs: num(j["dayPnlAbs"]),
                changePct: num(j["dayPnlPct"]),
                points: PortfolioSeries.clean(pts),
                holdings: holdings,
                syncedAt: syncedAt)
        }
    }

    /// Gallery and placeholder data.
    static let sample: PortfolioSnapshot = {
        let start = 1_790_000_000.0
        let shape: [Double] = [0, -6, -14, -9, -18, -25, -21, -30, -26, -19, -23, -15,
                               -20, -12, -16, -9, -13, -6, -11, -4, -8, -2, -6, -10]
        let pts = shape.enumerated().map { SeriesPoint(t: start + Double($0.offset) * 900, v: 10_133.24 + $0.element * 0.6) }
        return PortfolioSnapshot(
            id: "sample", name: "Alpaca Paper", value: 10_123.41,
            changeAbs: -9.83, changePct: -0.0970,
            points: pts,
            holdings: [
                HoldingItem(symbol: "GLD", pnlPct: -6.39, marketValue: 4_210),
                HoldingItem(symbol: "XLE", pnlPct: -0.74, marketValue: 2_980),
                HoldingItem(symbol: "GDX", pnlPct: -10.29, marketValue: 1_870),
                HoldingItem(symbol: "TQQQ", pnlPct: 4.12, marketValue: 1_020),
            ],
            syncedAt: Date(timeIntervalSince1970: start + 23 * 900))
    }()
}

/// One trading instance for the status widget.
nonisolated struct InstanceItem: Equatable, Sendable {
    let name: String
    let running: Bool
    let pnlPct: Double

    /// Parses the `instances_data` App-Group string.
    static func decode(_ raw: String?) -> [InstanceItem] {
        guard let raw, let data = raw.data(using: .utf8),
              let arr = (try? JSONSerialization.jsonObject(with: data)) as? [[String: Any]]
        else { return [] }
        return arr.compactMap { j in
            guard let name = j["name"] as? String, !name.isEmpty else { return nil }
            return InstanceItem(name: name, running: j["running"] as? Bool ?? false, pnlPct: num(j["pnlPct"]))
        }
    }
}

nonisolated private func num(_ v: Any?) -> Double {
    (v as? NSNumber)?.doubleValue ?? 0
}

// MARK: - Series

nonisolated enum PortfolioSeries {
    /// Cleans the `/widget/accounts` intraday series before it is drawn.
    ///
    /// The 1D series is Alpaca's continuous portfolio history (15-minute bars
    /// over the last 24 hours) plus one live `/v2/account` tip. Its first bars
    /// are the previous evening's after-hours marks; Alpaca re-marks the
    /// account when the overnight session opens, so on a quiet or closed day
    /// the series is one small step at the very start and then flat. Scaled to
    /// fill the chart, that step was the "vertical spike" at the left edge.
    ///
    /// 1. Drops non-finite and non-positive values (Alpaca reports 0 equity
    ///    for bars before an account had any) and non-positive times.
    /// 2. Sorts by time and keeps the last sample for a repeated timestamp.
    /// 3. Drops a stale lead-in: samples before a time gap wider than four
    ///    median bar spacings, when the gap falls in the first half.
    /// 4. Drops a leading step: the first few samples (at most a tenth of the
    ///    series) when the jump after them is the series' largest by five
    ///    times the next largest. A real move inside the session is never a
    ///    lone step in the first tenth that dwarfs every other bar.
    static func clean(_ raw: [SeriesPoint]) -> [SeriesPoint] {
        // Sort by time, ties in arrival order, so "last sample wins" is stable.
        let pts = raw.enumerated()
            .filter { $0.element.v.isFinite && $0.element.v > 0 && $0.element.t.isFinite && $0.element.t > 0 }
            .sorted { ($0.element.t, $0.offset) < ($1.element.t, $1.offset) }
            .map(\.element)
        var unique: [SeriesPoint] = []
        unique.reserveCapacity(pts.count)
        for p in pts {
            if let last = unique.last, last.t == p.t {
                unique[unique.count - 1] = p
            } else {
                unique.append(p)
            }
        }
        return dropLeadingStep(dropStaleLead(unique))
    }

    private static func dropStaleLead(_ pts: [SeriesPoint]) -> [SeriesPoint] {
        guard pts.count >= 4 else { return pts }
        let gaps = (1..<pts.count).map { pts[$0].t - pts[$0 - 1].t }
        let median = gaps.sorted()[gaps.count / 2]
        guard median > 0 else { return pts }
        // The last oversized gap in the first half marks where the session starts.
        guard let cut = (0..<(gaps.count / 2)).last(where: { gaps[$0] > median * 4 }) else { return pts }
        return Array(pts[(cut + 1)...])
    }

    private static func dropLeadingStep(_ pts: [SeriesPoint]) -> [SeriesPoint] {
        guard pts.count >= 10 else { return pts }
        let steps = (1..<pts.count).map { abs(pts[$0].v - pts[$0 - 1].v) }
        guard let biggest = steps.indices.max(by: { steps[$0] < steps[$1] }) else { return pts }
        let lead = biggest + 1                       // samples before the jump
        guard lead <= pts.count / 10, steps[biggest] > 0 else { return pts }
        let others = steps.enumerated().filter { $0.offset != biggest }.map(\.element)
        let runnerUp = others.max() ?? 0
        guard steps[biggest] >= runnerUp * 5 else { return pts }
        return Array(pts[lead...])
    }

    /// The y-domain for `points`: their range, widened to at least
    /// `minimumSpanFraction` of the level so a cents-sized wobble on a flat
    /// day draws as a near-flat line rather than a full-height zigzag, plus a
    /// little headroom above and below. Nil without two points.
    static func domain(for points: [SeriesPoint], minimumSpanFraction: Double = 0.002) -> ClosedRange<Double>? {
        guard points.count >= 2,
              let lo = points.map(\.v).min(), let hi = points.map(\.v).max()
        else { return nil }
        let mid = (lo + hi) / 2
        let span = max(hi - lo, abs(mid) * minimumSpanFraction, 0.01)
        let pad = span * 0.12
        return (mid - span / 2 - pad)...(mid + span / 2 + pad)
    }
}

// MARK: - Formatting

nonisolated enum PortfolioFormat {
    static let minus = "\u{2212}"

    private static func grouped(_ v: Double) -> String {
        let f = NumberFormatter()
        f.locale = Locale(identifier: "en_US_POSIX")
        f.numberStyle = .decimal
        f.usesGroupingSeparator = true
        f.groupingSeparator = ","
        f.groupingSize = 3
        f.minimumFractionDigits = 2
        f.maximumFractionDigits = 2
        return f.string(from: NSNumber(value: v)) ?? String(format: "%.2f", v)
    }

    /// `$10,123.41`, or `−$12.00` below zero.
    static func money(_ v: Double) -> String {
        (v < 0 ? minus : "") + "$" + grouped(abs(v))
    }

    /// `+$62.13` / `−$9.83`. Zero counts as up, as in the app.
    static func signedMoney(_ v: Double) -> String {
        (v < 0 ? minus : "+") + "$" + grouped(abs(v))
    }

    /// `+1.07%` / `−0.10%`.
    static func signedPercent(_ v: Double) -> String {
        (v < 0 ? minus : "+") + grouped(abs(v)) + "%"
    }

    /// `−$9.83 (−0.10%)`, as the app's hero shows the day change.
    static func change(abs: Double, pct: Double) -> String {
        "\(signedMoney(abs)) (\(signedPercent(pct)))"
    }

    /// The arrow before a change, the same glyphs as the app's `ChangeDirection`.
    static func arrow(up: Bool) -> String {
        up ? "arrow.up.right" : "arrow.down.right"
    }

    /// "Alpaca Paper, $10,123.41, down 0.10% today".
    static func accessibilityLabel(name: String, value: Double, changePct: Double) -> String {
        let dir = changePct < 0 ? "down" : "up"
        return "\(displayName(name)), \(money(value)), \(dir) \(grouped(abs(changePct)))% today"
    }

    /// "Updated 7:37 PM" for a sync earlier today, "Updated Fri 7:37 PM"
    /// otherwise. The time follows the device's 12/24-hour setting.
    static func updated(_ synced: Date, now: Date, calendar: Calendar = .current, locale: Locale = .current) -> String {
        let f = DateFormatter()
        f.locale = locale
        f.timeZone = calendar.timeZone
        f.setLocalizedDateFormatFromTemplate(calendar.isDate(synced, inSameDayAs: now) ? "jmm" : "EEEjmm")
        return "Updated \(f.string(from: synced))"
    }

    private static let minorWords: Set<String> = ["a", "an", "and", "as", "at", "by", "for", "in", "of", "on", "or", "the", "to", "vs"]

    /// Title case without forcing caps: the first letter of each word is
    /// raised and nothing is lowered, so "Strategy EB lab (backtest only)"
    /// reads "Strategy EB Lab (Backtest Only)". Minor words after the first
    /// stay lower case.
    static func displayName(_ raw: String) -> String {
        let words = raw.split(separator: " ", omittingEmptySubsequences: false)
        return words.enumerated().map { i, word -> String in
            let w = String(word)
            if i > 0, minorWords.contains(w) { return w }
            guard let idx = w.firstIndex(where: { $0.isLetter }), w[idx].isLowercase else { return w }
            return w.replacingCharacters(in: idx...idx, with: w[idx].uppercased())
        }
        .joined(separator: " ")
    }
}
