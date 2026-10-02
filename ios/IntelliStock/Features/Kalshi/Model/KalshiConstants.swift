import Foundation

// Constants and small formatting helpers shared by the Kalshi screens —
// `_kLeagues`, `_kRiskPresets` (kalshi_screen.dart), `_kPresets`
// (kalshi_backtest_screen.dart) and the Dart number idioms they lean on.

/// `_kLeagues`: the soccer leagues an instance or backtest can scan, in the
/// Dart order.
nonisolated let kalshiLeagues: [String] = [
    "World Cup", "World Cup Qualifiers", "Champions League", "Europa League",
    "EPL", "EFL Championship", "Serie A", "Serie B", "La Liga", "La Liga 2",
    "Bundesliga", "2. Bundesliga", "Ligue 1", "Ligue 2", "Eredivisie",
    "Primeira Liga", "MLS", "Brasileirão",
]

/// One `_kRiskPresets` entry (kalshi_screen.dart). `dlpct` is the daily-loss
/// cap as a fraction of the effective bankroll.
nonisolated struct KalshiRiskPreset: Hashable, Sendable {
    let key: String
    let label: String
    let edge: Num
    let kelly: Num
    let maxC: Num
    let exp: Num
    let lcap: Num
    let usage: Num
    let poll: Num
    let dlpct: Num
    let osmin: Num
    let osmax: Num
    let blurb: String

    /// The presets in map order (low, medium, high, max).
    static let all: [KalshiRiskPreset] = [
        KalshiRiskPreset(key: "low", label: "Low", edge: 5.0, kelly: 0.10, maxC: 25, exp: 10.0, lcap: 12.0, usage: 40.0, poll: 90, dlpct: 0.05, osmin: 1, osmax: 3,
                         blurb: "Conservative — fewer, higher-confidence trades; small stakes, tight daily-loss cap."),
        KalshiRiskPreset(key: "medium", label: "Medium", edge: 4.0, kelly: 0.125, maxC: 50, exp: 15.0, lcap: 25.0, usage: 50.0, poll: 60, dlpct: 0.08, osmin: 2, osmax: 5,
                         blurb: "Balanced — the default. Fractional-Kelly with a small exposure cap and cash reserve."),
        KalshiRiskPreset(key: "high", label: "High", edge: 3.0, kelly: 0.15, maxC: 75, exp: 25.0, lcap: 30.0, usage: 60.0, poll: 45, dlpct: 0.10, osmin: 5, osmax: 10,
                         blurb: "More active — lower edge bar, slightly bigger Kelly and exposure. More variance."),
        KalshiRiskPreset(key: "max", label: "Max", edge: 2.0, kelly: 0.20, maxC: 100, exp: 40.0, lcap: 40.0, usage: 70.0, poll: 30, dlpct: 0.15, osmin: 8, osmax: 15,
                         blurb: "Aggressive — more +EV spots at larger size. Highest variance, but capped well below the old defaults."),
    ]

    static func named(_ key: String) -> KalshiRiskPreset? {
        all.first { $0.key == key }
    }
}

/// One `_kPresets` entry (kalshi_backtest_screen.dart). All doubles; `daily`
/// is a percent.
nonisolated struct KalshiBacktestPreset: Hashable, Sendable {
    let key: String
    let edge: Double
    let kelly: Double
    let contracts: Double
    let exposure: Double
    let leagueCap: Double
    let usage: Double
    let daily: Double
    let omin: Double
    let omax: Double

    static let all: [KalshiBacktestPreset] = [
        KalshiBacktestPreset(key: "low", edge: 5.0, kelly: 0.10, contracts: 25.0, exposure: 10.0, leagueCap: 12.0, usage: 40.0, daily: 5.0, omin: 1.0, omax: 3.0),
        KalshiBacktestPreset(key: "medium", edge: 4.0, kelly: 0.125, contracts: 50.0, exposure: 15.0, leagueCap: 25.0, usage: 50.0, daily: 8.0, omin: 2.0, omax: 5.0),
        KalshiBacktestPreset(key: "high", edge: 3.0, kelly: 0.15, contracts: 75.0, exposure: 25.0, leagueCap: 30.0, usage: 60.0, daily: 10.0, omin: 5.0, omax: 10.0),
        KalshiBacktestPreset(key: "max", edge: 2.0, kelly: 0.20, contracts: 100.0, exposure: 40.0, leagueCap: 40.0, usage: 70.0, daily: 15.0, omin: 8.0, omax: 15.0),
    ]

    static func named(_ key: String) -> KalshiBacktestPreset? {
        all.first { $0.key == key }
    }

    /// `k[0].toUpperCase() + k.substring(1)`.
    var label: String { key.prefix(1).uppercased() + key.dropFirst() }
}

nonisolated enum KalshiFormat {
    /// The sheets' `_num`: an integral value prints as an int, anything else
    /// through Dart's `toString()` (`0.125`, `7.000000000000001`).
    static func num(_ v: Num) -> String {
        switch v {
        case .int(let i): return String(i)
        case .double(let d):
            // `toInt()` saturates as on the Dart VM; `Int(_:)` trapped on a
            // served 1e20.
            if d.isFinite, d == d.rounded(), let i = Int(dartTruncating: d) { return String(i) }
            return JSON.dartDoubleString(d)
        }
    }

    static func num(_ v: Double) -> String { num(Num.double(v)) }

    /// `(a ?? b ?? c).toString()` — the first non-null value.
    static func firstNonNull(_ values: JSON?...) -> String {
        for v in values {
            if let v, !v.isNull { return v.dartDescription }
        }
        return "null"
    }

    /// `'$e'`: the API message, else the error's description.
    static func errorText(_ error: any Error) -> String {
        (error as? ApiError)?.message ?? error.localizedDescription
    }

    /// `\$${(cents / 100).toStringAsFixed(2)}`, or `—` for nil.
    static func money(cents: Double?) -> String {
        guard let cents else { return "—" }
        return "$\(dartToStringAsFixed(cents / 100, 2))"
    }

    /// `${(v * 100).toStringAsFixed(1)}%`, or `—` for nil.
    static func pct(_ v: Double?) -> String {
        guard let v else { return "—" }
        return "\(dartToStringAsFixed(v * 100, 1))%"
    }

    /// `'+'` for a non-negative edge, then `(edge*100).toStringAsFixed(1)%`.
    static func signedEdge(_ edge: Double) -> String {
        "\(edge >= 0 ? "+" : "")\(dartToStringAsFixed(edge * 100, 1))%"
    }

    /// `+$X.XX` / `-$X.XX` from cents (sign before the dollar).
    static func signedDollars(cents: Double) -> String {
        "\(cents >= 0 ? "+" : "-")$\(dartToStringAsFixed(abs(cents) / 100, 2))"
    }

    /// The crest fallback: letters and spaces only, first letter of each
    /// word, at most `take` of them (`_posCrest`, `_crest`).
    static func initials(_ name: String, take: Int = 2) -> String {
        let cleaned = String(name.unicodeScalars.filter { ($0.value >= 65 && $0.value <= 90) || ($0.value >= 97 && $0.value <= 122) || $0 == " " })
        return cleaned.split(separator: " ", omittingEmptySubsequences: true)
            .prefix(take)
            .map { String($0.prefix(1)) }
            .joined()
    }

    /// `_badgeFallback`: every word's initial, truncated to 3.
    static func badgeInitials(_ name: String) -> String {
        let cleaned = String(name.unicodeScalars.filter { ($0.value >= 65 && $0.value <= 90) || ($0.value >= 97 && $0.value <= 122) || $0 == " " })
        let joined = cleaned.split(separator: " ", omittingEmptySubsequences: false)
            .map { $0.isEmpty ? "" : String($0.prefix(1)) }
            .joined()
        return joined.count > 3 ? String(joined.prefix(3)) : joined
    }

    /// `yyyy-MM-dd` of a local date (`_fmtDate`).
    static func ymd(_ d: Date) -> String {
        let c = Calendar.current.dateComponents([.year, .month, .day], from: d)
        let y = String(c.year ?? 0)
        let m = String(c.month ?? 0)
        let day = String(c.day ?? 0)
        return String(repeating: "0", count: max(0, 4 - y.count)) + y + "-"
            + String(repeating: "0", count: max(0, 2 - m.count)) + m + "-"
            + String(repeating: "0", count: max(0, 2 - day.count)) + day
    }
}

/// Whether `error` means the request was cancelled (the screen went away or
/// the task was replaced) rather than failed. Cancellation is never shown as
/// an error; the state stays as it was.
nonisolated func marketsIsCancellation(_ error: any Error) -> Bool {
    if error is CancellationError { return true }
    if let u = error as? URLError, u.code == .cancelled { return true }
    return false
}

nonisolated extension Loadable {
    /// A capture that was really a cancellation: core's `Loadable.capture`
    /// returns `.loading` for a cancelled body (a finished capture is never
    /// `.loading` otherwise), and older paths surface it as a failure.
    var marketsCancelled: Bool {
        if case .loading = self { return true }
        return error.map(marketsIsCancellation) ?? false
    }
}
