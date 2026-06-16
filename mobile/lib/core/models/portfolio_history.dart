/// Equity history for a brokerage account / instance.
/// Matches `GET /brokerages/{id}/portfolio-history` and
/// `GET /instances/{id}/portfolio-history`.
class PortfolioHistory {
  PortfolioHistory({
    required this.timestamps,
    required this.values,
    this.currentValue,
    this.openValue,
    this.changeAbs,
    this.changePct,
  });

  final List<DateTime> timestamps;
  final List<double> values;
  final double? currentValue;
  final double? openValue;
  final double? changeAbs;
  final double? changePct;

  bool get isEmpty => values.isEmpty;

  /// Re-baselines a 1D series to the device's LOCAL midnight (Robinhood's
  /// overnight day view): the open/baseline becomes the equity at 00:00 local
  /// (the last sample at or before midnight, carried forward), the series is
  /// trimmed to samples from midnight onward with a baseline point planted
  /// exactly at midnight, and the P&L is recomputed against that baseline.
  /// No-op when empty.
  PortfolioHistory sinceLocalMidnight() {
    if (timestamps.isEmpty || values.isEmpty) return this;
    final now = DateTime.now();
    final midnight = DateTime(now.year, now.month, now.day);
    final midnightMs = midnight.millisecondsSinceEpoch;

    // Baseline = the last value at/just before midnight (carried forward); if
    // every sample is after midnight, fall back to the earliest value.
    var baseline = values.first;
    var startIdx = timestamps.length; // default: nothing at/after midnight
    for (var i = 0; i < timestamps.length; i++) {
      if (timestamps[i].millisecondsSinceEpoch < midnightMs) {
        baseline = values[i];
      } else {
        startIdx = i;
        break;
      }
    }

    // Plant the baseline exactly at midnight, then keep samples after it.
    final ts = <DateTime>[midnight];
    final vals = <double>[baseline];
    for (var i = startIdx; i < timestamps.length; i++) {
      if (timestamps[i].millisecondsSinceEpoch <= midnightMs) continue;
      ts.add(timestamps[i]);
      vals.add(values[i]);
    }

    final cur = currentValue ?? vals.last;
    final abs = cur - baseline;
    final pct = baseline != 0 ? (abs / baseline) * 100 : null;
    return PortfolioHistory(
      timestamps: ts,
      values: vals,
      currentValue: cur,
      openValue: baseline,
      changeAbs: abs,
      changePct: pct,
    );
  }

  factory PortfolioHistory.fromJson(Map<String, dynamic> json) {
    final rawTs = (json['timestamps'] as List? ?? const []);
    final rawVals = (json['values'] as List? ?? const []);
    return PortfolioHistory(
      timestamps: rawTs.map(_toDate).whereType<DateTime>().toList(),
      values: rawVals.map((v) => (v as num?)?.toDouble() ?? 0).toList(),
      currentValue: (json['current_value'] as num?)?.toDouble(),
      openValue: (json['open_value'] as num?)?.toDouble(),
      changeAbs: (json['change_abs'] as num?)?.toDouble(),
      changePct: (json['change_pct'] as num?)?.toDouble(),
    );
  }

  static DateTime? _toDate(dynamic v) {
    if (v is num) {
      final ms = v > 1000000000000 ? v.toInt() : (v * 1000).toInt();
      return DateTime.fromMillisecondsSinceEpoch(ms);
    }
    if (v is String) return DateTime.tryParse(v);
    return null;
  }
}
