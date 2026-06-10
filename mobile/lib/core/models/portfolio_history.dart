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
