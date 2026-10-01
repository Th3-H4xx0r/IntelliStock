import Foundation

/// Equity history for a brokerage account or instance, ported from
/// core/models/portfolio_history.dart. Matches
/// `GET /brokerages/{id}/portfolio-history` and
/// `GET /instances/{id}/portfolio-history`.
nonisolated struct PortfolioHistory: Hashable, Sendable {
    var timestamps: [Date]
    var values: [Double]
    var currentValue: Double?
    var openValue: Double?
    var changeAbs: Double?
    var changePct: Double?

    init(
        timestamps: [Date],
        values: [Double],
        currentValue: Double? = nil,
        openValue: Double? = nil,
        changeAbs: Double? = nil,
        changePct: Double? = nil
    ) {
        self.timestamps = timestamps
        self.values = values
        self.currentValue = currentValue
        self.openValue = openValue
        self.changeAbs = changeAbs
        self.changePct = changePct
    }

    init(json: JSON) {
        let rawTs = json["timestamps"].arrayValue
        let rawVals = json["values"].arrayValue
        self.init(
            timestamps: rawTs.compactMap(PortfolioHistory.toDate),
            values: rawVals.map { $0.double ?? 0 },
            currentValue: json["current_value"].double,
            openValue: json["open_value"].double,
            changeAbs: json["change_abs"].double,
            changePct: json["change_pct"].double
        )
    }

    var isEmpty: Bool { values.isEmpty }

    /// Re-baselines a 1D series to the device's LOCAL midnight (the overnight
    /// day view): the open/baseline becomes the equity at 00:00 local (the
    /// last sample at or before midnight, carried forward), the series is
    /// trimmed to samples from midnight onward with a baseline point planted
    /// exactly at midnight, and the P&L is recomputed against that baseline.
    /// No-op when empty. `now` defaults to the current time (Dart read
    /// `DateTime.now()`).
    func sinceLocalMidnight(now: Date = Date(), calendar: Calendar = .current) -> PortfolioHistory {
        if timestamps.isEmpty || values.isEmpty { return self }
        let midnight = calendar.startOfDay(for: now)
        let midnightMs = DartDateTime.millisecondsSinceEpoch(midnight)

        // Baseline = the last value at/just before midnight (carried forward);
        // if every sample is after midnight, fall back to the earliest value.
        var baseline = values[0]
        var startIdx = timestamps.count // default: nothing at/after midnight
        for i in timestamps.indices {
            if DartDateTime.millisecondsSinceEpoch(timestamps[i]) < midnightMs {
                // Dart indexed values[i] directly; a shorter values list threw.
                if i < values.count { baseline = values[i] }
            } else {
                startIdx = i
                break
            }
        }

        // Plant the baseline exactly at midnight, then keep samples after it.
        var ts: [Date] = [midnight]
        var vals: [Double] = [baseline]
        var i = startIdx
        while i < timestamps.count {
            defer { i += 1 }
            if DartDateTime.millisecondsSinceEpoch(timestamps[i]) <= midnightMs { continue }
            guard i < values.count else { break }
            ts.append(timestamps[i])
            vals.append(values[i])
        }

        let cur = currentValue ?? vals[vals.count - 1]
        let abs = cur - baseline
        let pct: Double? = baseline != 0 ? (abs / baseline) * 100 : nil
        return PortfolioHistory(
            timestamps: ts,
            values: vals,
            currentValue: cur,
            openValue: baseline,
            changeAbs: abs,
            changePct: pct
        )
    }

    /// Epoch seconds or milliseconds (above 1e12), or an ISO string.
    static func toDate(_ v: JSON) -> Date? {
        switch v {
        case .int(let i):
            let ms = i > 1_000_000_000_000 ? i : i * 1000
            return DartDateTime.fromMillisecondsSinceEpoch(ms)
        case .double(let d):
            guard d.isFinite else { return nil }
            let ms = d > 1_000_000_000_000 ? Int(d) : Int(d * 1000)
            return DartDateTime.fromMillisecondsSinceEpoch(ms)
        case .string(let s):
            return DartDateTime.tryParse(s)
        default:
            return nil
        }
    }
}
