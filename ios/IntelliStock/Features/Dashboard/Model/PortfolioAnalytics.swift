import Foundation

// Pure portfolio-analytics helpers, ported from
// features/dashboard/application/portfolio_analytics.dart. They take
// primitive inputs rather than model objects, keeping them decoupled from the
// data layer.
//
// The Dart took `Map<String, double>` built from the positions list, and Dart
// maps keep insertion order, which decided the order of equal values. Swift
// dictionaries do not keep order, so the value inputs here are ordered
// `(symbol, value)` pairs in position order.

/// One sector's share of invested value.
nonisolated struct SectorSlice: Hashable, Sendable {
    let sector: String
    let value: Double
    /// 0..100
    let pct: Double
}

/// Group holdings' market value by sector, biggest first. Unknown/blank
/// sectors fold into "Other". Returns [] when there's no positive value.
///
/// Option contracts (OCC symbols) are left out on purpose, long or short. An
/// option's market value is its premium, not sector exposure: a short put
/// reports a negative value while it commits strike x 100 of the
/// underlying's sector. Counting either number would misstate the
/// breakdown, so the chart shows the stock book only.
nonisolated func aggregateBySector(
    _ valueBySymbol: [(symbol: String, value: Double)],
    _ sectorBySymbol: [String: String?]
) -> [SectorSlice] {
    var order: [String] = []
    var bySector: [String: Double] = [:]
    var total = 0.0
    for (sym, val) in valueBySymbol {
        if isOccOptionSymbol(sym) { continue }
        if val <= 0 { continue }
        let raw = ((sectorBySymbol[sym] ?? nil) ?? "").trimmingCharacters(in: .whitespacesAndNewlines)
        let key = raw.isEmpty ? "Other" : raw
        if bySector[key] == nil { order.append(key) }
        bySector[key, default: 0] += val
        total += val
    }
    if total <= 0 { return [] }
    return order
        .map { SectorSlice(sector: $0, value: bySector[$0]!, pct: bySector[$0]! / total * 100) }
        .sorted { $0.value > $1.value }
}

/// Portfolio concentration: the largest holding's weight, the position count,
/// the Herfindahl-Hirschman Index (Σ wᵢ²), and a 0–100 diversification score
/// (100 = perfectly even, 0 = all in one name).
nonisolated struct ConcentrationStats: Hashable, Sendable {
    /// 0..100
    let topWeight: Double
    let count: Int
    /// 0..1
    let hhi: Double
    /// 0..100
    let score: Int

    var isEmpty: Bool { count == 0 }
}

nonisolated func concentration(_ values: [Double]) -> ConcentrationStats {
    let pos = values.filter { $0 > 0 }
    let total = pos.reduce(0, +)
    if total <= 0 || pos.isEmpty {
        return ConcentrationStats(topWeight: 0, count: 0, hhi: 0, score: 0)
    }
    let weights = pos.map { $0 / total }
    let hhi = weights.reduce(0) { $0 + $1 * $1 }
    let top = weights.reduce(weights[0]) { $0 > $1 ? $0 : $1 }
    // Dart `.round()` rounds half away from zero, as Swift's `.rounded()` does.
    let score = min(max(Int(((1 - hhi) * 100).rounded()), 0), 100)
    return ConcentrationStats(topWeight: top * 100, count: pos.count, hhi: hhi, score: score)
}

/// A holding's intraday move.
nonisolated struct Mover: Hashable, Sendable {
    let symbol: String
    let pct: Double
}

/// Holdings ranked by today's % move, biggest gainer first. Equal moves keep
/// their input order.
nonisolated func todaysMovers(_ pctBySymbol: [(symbol: String, pct: Double)]) -> [Mover] {
    pctBySymbol.map { Mover(symbol: $0.symbol, pct: $0.pct) }.sorted { $0.pct > $1.pct }
}

/// First→last percent change of a price series, or nil when not computable
/// (fewer than 2 points, or a zero starting value).
nonisolated func pctChangeOf(_ values: [Double]) -> Double? {
    if values.count < 2 || values[0] == 0 { return nil }
    return (values[values.count - 1] / values[0] - 1) * 100
}

/// Risk summary derived from an equity curve. Percentages are 0..100.
nonisolated struct RiskMetrics: Hashable, Sendable {
    /// Annualized stdev of periodic returns, %.
    let volatility: Double
    /// Worst peak-to-trough, %.
    let maxDrawdown: Double
    /// Annualized; nil when stdev is 0.
    let sharpe: Double?
    let points: Int

    var isEmpty: Bool { points < 2 }
}

/// Volatility, max drawdown and Sharpe from an equity `values` series.
/// Returns are period-over-period; annualization uses √252 as a simple,
/// consistent scale factor. Risk-free is assumed 0.
nonisolated func riskMetrics(_ values: [Double]) -> RiskMetrics {
    if values.count < 2 {
        return RiskMetrics(volatility: 0, maxDrawdown: 0, sharpe: nil, points: 0)
    }
    var returns: [Double] = []
    for i in 1..<values.count where values[i - 1] > 0 {
        let ret = values[i] / values[i - 1] - 1
        // Skip funding/transfer artifacts: a >50% single-period jump is a
        // deposit or a data gap, not a market move, and would otherwise blow
        // up the annualized volatility/Sharpe (e.g. a garbage "997%").
        if abs(ret) <= 0.5 { returns.append(ret) }
    }
    var peak = values[0]
    var maxDd = 0.0
    for v in values {
        if v > peak { peak = v }
        if peak > 0 {
            let dd = (peak - v) / peak
            if dd > maxDd { maxDd = dd }
        }
    }
    if returns.isEmpty {
        return RiskMetrics(volatility: 0, maxDrawdown: maxDd * 100, sharpe: nil, points: values.count)
    }
    let mean = returns.reduce(0, +) / Double(returns.count)
    let variance = returns.map { ($0 - mean) * ($0 - mean) }.reduce(0, +) / Double(returns.count)
    let stdev = variance <= 0 ? 0.0 : variance.squareRoot()
    let annualize = 15.874507866 // √252
    return RiskMetrics(
        volatility: stdev * annualize * 100,
        maxDrawdown: maxDd * 100,
        sharpe: stdev == 0 ? nil : (mean / stdev) * annualize,
        points: values.count
    )
}
