import Foundation

// Ported from features/live_trading/presentation/manual_order_sheet.dart and
// the pure parts of equity_chart.dart / position_card.dart.

// MARK: - Manual order

/// The manual order form's fields (`OrderForm`).
nonisolated struct OrderForm: Hashable, Sendable {
    var symbol = ""
    /// "buy" | "sell"
    var side = "buy"
    /// "market" | "limit"
    var orderType = "market"
    var qty = ""
    var notional = ""
    var limitPrice = ""
    /// "day" | "gtc" | "ioc" | "fok" | "opg" | "cls"
    var tif = "day"
    var extendedHours = false

    /// Extended hours needs a limit order with TIF day.
    var extendedHoursAllowed: Bool { orderType == "limit" && tif == "day" }

    /// Changing the order type away from limit clears extended hours.
    mutating func setOrderType(_ v: String) {
        orderType = v
        if v != "limit", extendedHours { extendedHours = false }
    }

    /// Changing TIF away from day clears extended hours.
    mutating func setTif(_ v: String) {
        tif = v
        if v != "day", extendedHours { extendedHours = false }
    }
}

/// The TIF choices, in Dart's order.
nonisolated let liveTifOptions: [(value: String, label: String)] = [
    ("day", "Day"), ("gtc", "GTC"), ("ioc", "IOC"), ("fok", "FOK"), ("opg", "OPG"), ("cls", "CLS"),
]

/// Validates the manual order form; nil when valid, else the message.
nonisolated func validateOrderForm(_ form: OrderForm) -> String? {
    let sym = form.symbol.trimmingCharacters(in: .whitespacesAndNewlines).uppercased()
    if sym.isEmpty { return "Symbol is required." }

    let qtyStr = form.qty.trimmingCharacters(in: .whitespacesAndNewlines)
    let notionalStr = form.notional.trimmingCharacters(in: .whitespacesAndNewlines)
    let hasQty = !qtyStr.isEmpty
    let hasNotional = !notionalStr.isEmpty

    if !hasQty && !hasNotional { return "Enter either qty or notional." }
    if hasQty && hasNotional { return "Fill qty OR notional, not both." }

    if hasQty {
        guard let qty = JSON.parseDouble(qtyStr), qty > 0 else { return "Qty must be a positive number." }
    }
    if hasNotional {
        guard let notional = JSON.parseDouble(notionalStr), notional > 0 else { return "Notional must be a positive number." }
    }
    if form.orderType == "limit" {
        guard let lp = JSON.parseDouble(form.limitPrice.trimmingCharacters(in: .whitespacesAndNewlines)), lp > 0 else {
            return "Limit order requires a positive limit price."
        }
    }
    if form.extendedHours, form.orderType != "limit" || form.tif != "day" {
        return "Extended hours requires limit order type + TIF=day."
    }
    return nil
}

/// The `submit_order` payload for a validated form (numbers as doubles,
/// as Dart's `double.parse` sent them).
nonisolated func buildOrderPayload(_ form: OrderForm) -> JSONObject {
    var payload: JSONObject = [
        "symbol": .string(form.symbol.trimmingCharacters(in: .whitespacesAndNewlines).uppercased()),
        "side": .string(form.side),
        "order_type": .string(form.orderType),
        "tif": .string(form.tif),
        "extended_hours": .bool(form.extendedHours),
    ]
    let qtyStr = form.qty.trimmingCharacters(in: .whitespacesAndNewlines)
    let notionalStr = form.notional.trimmingCharacters(in: .whitespacesAndNewlines)
    if !qtyStr.isEmpty {
        payload["qty"] = .double(JSON.parseDouble(qtyStr) ?? 0)
    } else {
        payload["notional"] = .double(JSON.parseDouble(notionalStr) ?? 0)
    }
    if form.orderType == "limit" {
        payload["limit_price"] = .double(JSON.parseDouble(form.limitPrice.trimmingCharacters(in: .whitespacesAndNewlines)) ?? 0)
    }
    return payload
}

/// The halt command's reason: the typed text, or `manual halt via UI`.
nonisolated func liveHaltReason(_ typed: String) -> String {
    let t = typed.trimmingCharacters(in: .whitespacesAndNewlines)
    return t.isEmpty ? "manual halt via UI" : t
}

// MARK: - Charts

/// The equity and position chart styles (`ChartStyle`).
nonisolated enum LiveChartStyle: String, Hashable, Sendable, CaseIterable {
    case area, line, candle

    /// The toggle's glyph (Material name).
    var symbolName: String {
        switch self {
        case .area: "area_chart"
        case .line: "show_chart"
        case .candle: "candlestick_chart"
        }
    }

    var accessibilityName: String {
        switch self {
        case .area: "Area"
        case .line: "Line"
        case .candle: "Candles"
        }
    }
}

/// One OHLC bucket.
nonisolated struct LiveCandle: Hashable, Sendable {
    let x: Int
    let open: Double
    let high: Double
    let low: Double
    let close: Double
}

/// `_bucketCandles`: `ceil(n / count)` samples per candle (clamped to
/// 1…n), open = first, close = last; fewer than 2 points → none.
nonisolated func liveBucketCandles(_ values: [Double], count: Int) -> [LiveCandle] {
    if values.count < 2 { return [] }
    let samplesPer = min(max(Int((Double(values.count) / Double(count)).rounded(.up)), 1), values.count)
    var out: [LiveCandle] = []
    var i = 0
    while i < values.count {
        let slice = Array(values[i..<min(i + samplesPer, values.count)])
        out.append(LiveCandle(
            x: out.count, open: slice[0], high: slice.max()!, low: slice.min()!, close: slice[slice.count - 1]
        ))
        i += samplesPer
    }
    return out
}

/// High/low and the first→last move over a range (`RangeStats`).
nonisolated struct RangeStats: Hashable, Sendable {
    let high: Double?
    let low: Double?
    let dollars: Double
    let pct: Double
    let isUp: Bool

    static func from(_ h: PortfolioHistory?) -> RangeStats {
        guard let h, h.values.count >= 2 else {
            return RangeStats(high: nil, low: nil, dollars: 0, pct: 0, isUp: true)
        }
        let vs = h.values
        let start = vs[0]
        let dollars = vs[vs.count - 1] - start
        let pct = start != 0 ? (dollars / start) * 100 : 0
        return RangeStats(high: vs.max(), low: vs.min(), dollars: dollars, pct: pct, isUp: dollars >= 0)
    }
}

/// The equity chart's axis and scrub mapping (`EquityChart`).
nonisolated enum LiveEquityGeometry {
    /// 1D area/line plots on the fixed minute-of-day axis.
    static func usesTimeAxis(range: String, style: LiveChartStyle) -> Bool {
        range == "1D" && style != .candle
    }

    /// The point x positions: minute of day on the time axis, else index.
    static func xs(_ history: PortfolioHistory, range: String, style: LiveChartStyle, calendar: Calendar = DartDateTime.localCalendar) -> [Double] {
        let n = min(history.timestamps.count, history.values.count)
        if usesTimeAxis(range: range, style: style) {
            return (0..<n).map { DashboardChartGeometry.minuteOfDay(history.timestamps[$0], calendar: calendar) }
        }
        return (0..<n).map(Double.init)
    }

    /// The labels under the plot.
    static func labels(_ history: PortfolioHistory, range: String, style: LiveChartStyle) -> [String] {
        if usesTimeAxis(range: range, style: style) { return [0, 8, 16, 24].map(hourAmPm) }
        let n = min(history.timestamps.count, history.values.count)
        return evenlySpacedLabelIndices(n, 4).map { formatChartDate(history.timestamps[$0], range) }
    }

    /// The data index for a horizontal `fraction` of the plot (`_handleScrub`):
    /// on the time axis the point nearest in minutes, else `fractionToIndex`.
    static func scrubIndex(fraction: Double, xs: [Double], timeAxis: Bool) -> Int {
        let n = xs.count
        if n == 0 { return 0 }
        let frac = min(max(fraction, 0), 1)
        if timeAxis {
            let target = frac * DashboardChartGeometry.dayMinutes
            var idx = 0
            var best = Double.infinity
            for i in 0..<n {
                let d = abs(xs[i] - target)
                if d < best {
                    best = d
                    idx = i
                }
            }
            return idx
        }
        return fractionToIndex(frac, n)
    }

    /// Where the hairline sits for index `idx`, as a fraction of the plot.
    static func hairlineFraction(_ idx: Int, xs: [Double], timeAxis: Bool) -> Double {
        if timeAxis, xs.indices.contains(idx) { return min(max(xs[idx] / DashboardChartGeometry.dayMinutes, 0), 1) }
        return indexToFraction(idx, xs.count)
    }
}

/// The position card's range move (`_rangePct`) and direction (`_isUp`).
nonisolated enum LivePositionMath {
    static func rangePct(_ hist: [HistPoint]) -> Double {
        guard hist.count >= 2 else { return 0 }
        let start = hist[0].value
        if start == 0 { return 0 }
        return (hist[hist.count - 1].value - start) / start * 100
    }

    static func isUp(_ hist: [HistPoint], unrealizedPnl: Double?) -> Bool {
        if hist.count < 2 { return (unrealizedPnl ?? 0) >= 0 }
        return hist[hist.count - 1].value >= hist[0].value
    }

    /// `+1.23%  1D` / `-0.50%  1W`.
    static func rangeText(_ hist: [HistPoint], range: String, unrealizedPnl: Double?) -> String {
        "\(isUp(hist, unrealizedPnl: unrealizedPnl) ? "+" : "")\(dartToStringAsFixed(rangePct(hist), 2))%  \(range)"
    }
}
