// Pure portfolio-analytics helpers (no Flutter/Riverpod) so they're trivially
// unit-testable. They take primitive inputs (value/sector/pct maps) rather than
// model objects, keeping them decoupled from the data layer.

import 'dart:math' as math;

/// One sector's share of invested value.
class SectorSlice {
  const SectorSlice(
      {required this.sector, required this.value, required this.pct});
  final String sector;
  final double value;
  final double pct; // 0..100
}

/// Group holdings' market value by sector, biggest first. Unknown/blank sectors
/// fold into "Other". Returns [] when there's no positive value.
List<SectorSlice> aggregateBySector(
  Map<String, double> valueBySymbol,
  Map<String, String?> sectorBySymbol,
) {
  final bySector = <String, double>{};
  var total = 0.0;
  valueBySymbol.forEach((sym, val) {
    if (val <= 0) return;
    final raw = (sectorBySymbol[sym] ?? '').trim();
    final key = raw.isEmpty ? 'Other' : raw;
    bySector[key] = (bySector[key] ?? 0) + val;
    total += val;
  });
  if (total <= 0) return const [];
  final slices = bySector.entries
      .map((e) =>
          SectorSlice(sector: e.key, value: e.value, pct: e.value / total * 100))
      .toList()
    ..sort((a, b) => b.value.compareTo(a.value));
  return slices;
}

/// Portfolio concentration: the largest holding's weight, the position count,
/// the Herfindahl-Hirschman Index (Σ wᵢ²), and a 0–100 diversification score
/// (100 = perfectly even, 0 = all in one name).
class ConcentrationStats {
  const ConcentrationStats({
    required this.topWeight,
    required this.count,
    required this.hhi,
    required this.score,
  });
  final double topWeight; // 0..100
  final int count;
  final double hhi; // 0..1
  final int score; // 0..100

  bool get isEmpty => count == 0;
}

ConcentrationStats concentration(List<double> values) {
  final pos = values.where((v) => v > 0).toList();
  final total = pos.fold<double>(0, (a, b) => a + b);
  if (total <= 0 || pos.isEmpty) {
    return const ConcentrationStats(topWeight: 0, count: 0, hhi: 0, score: 0);
  }
  final weights = [for (final v in pos) v / total];
  final hhi = weights.fold<double>(0, (a, w) => a + w * w);
  final top = weights.reduce((a, b) => a > b ? a : b);
  final score = ((1 - hhi) * 100).round().clamp(0, 100);
  return ConcentrationStats(
    topWeight: top * 100,
    count: pos.length,
    hhi: hhi,
    score: score,
  );
}

/// A holding's intraday move.
class Mover {
  const Mover({required this.symbol, required this.pct});
  final String symbol;
  final double pct;
}

/// Holdings ranked by today's % move, biggest gainer first.
List<Mover> todaysMovers(Map<String, double> pctBySymbol) {
  final movers = [
    for (final e in pctBySymbol.entries) Mover(symbol: e.key, pct: e.value),
  ]..sort((a, b) => b.pct.compareTo(a.pct));
  return movers;
}

/// First→last percent change of a price series, or null when not computable
/// (fewer than 2 points, or a zero starting value).
double? pctChangeOf(List<double> values) {
  if (values.length < 2 || values.first == 0) return null;
  return (values.last / values.first - 1) * 100;
}

/// Risk summary derived from an equity curve. Percentages are 0..100.
class RiskMetrics {
  const RiskMetrics({
    required this.volatility,
    required this.maxDrawdown,
    required this.sharpe,
    required this.points,
  });
  final double volatility; // annualized stdev of periodic returns, %
  final double maxDrawdown; // worst peak-to-trough, %
  final double? sharpe; // annualized; null when stdev is 0
  final int points;

  bool get isEmpty => points < 2;
}

/// Volatility, max drawdown and Sharpe from an equity [values] series. Returns
/// are period-over-period; annualization uses √252 as a simple, consistent
/// scale factor. Risk-free is assumed 0.
RiskMetrics riskMetrics(List<double> values) {
  if (values.length < 2) {
    return const RiskMetrics(
        volatility: 0, maxDrawdown: 0, sharpe: null, points: 0);
  }
  final returns = <double>[];
  for (var i = 1; i < values.length; i++) {
    if (values[i - 1] != 0) returns.add(values[i] / values[i - 1] - 1);
  }
  var peak = values.first;
  var maxDd = 0.0;
  for (final v in values) {
    if (v > peak) peak = v;
    if (peak > 0) {
      final dd = (peak - v) / peak;
      if (dd > maxDd) maxDd = dd;
    }
  }
  if (returns.isEmpty) {
    return RiskMetrics(
        volatility: 0,
        maxDrawdown: maxDd * 100,
        sharpe: null,
        points: values.length);
  }
  final mean = returns.reduce((a, b) => a + b) / returns.length;
  final variance = returns
          .map((r) => (r - mean) * (r - mean))
          .reduce((a, b) => a + b) /
      returns.length;
  final stdev = variance <= 0 ? 0.0 : math.sqrt(variance);
  const annualize = 15.874507866; // √252
  return RiskMetrics(
    volatility: stdev * annualize * 100,
    maxDrawdown: maxDd * 100,
    sharpe: stdev == 0 ? null : (mean / stdev) * annualize,
    points: values.length,
  );
}
