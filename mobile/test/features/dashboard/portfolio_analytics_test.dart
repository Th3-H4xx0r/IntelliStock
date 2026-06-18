import 'package:flutter_test/flutter_test.dart';
import 'package:intellistock_mobile/features/dashboard/application/portfolio_analytics.dart';

void main() {
  group('aggregateBySector', () {
    test('groups by sector, sorts desc, computes pct', () {
      final slices = aggregateBySector(
        {'AAPL': 60, 'MSFT': 30, 'JPM': 10},
        {'AAPL': 'Technology', 'MSFT': 'Technology', 'JPM': 'Financials'},
      );
      expect(slices.first.sector, 'Technology');
      expect(slices.first.value, 90);
      expect(slices.first.pct, closeTo(90, 0.001));
      expect(slices.last.sector, 'Financials');
      expect(slices.last.pct, closeTo(10, 0.001));
    });

    test('blank/unknown sector folds into Other; non-positive ignored', () {
      final slices = aggregateBySector(
        {'AAPL': 50, 'XYZ': 50, 'ZERO': 0},
        {'AAPL': 'Technology', 'XYZ': null},
      );
      expect(slices.map((s) => s.sector).toSet(), {'Technology', 'Other'});
      expect(slices.every((s) => s.value == 50), isTrue);
    });

    test('empty input → empty list', () {
      expect(aggregateBySector({}, {}), isEmpty);
      expect(aggregateBySector({'A': 0}, {'A': 'Tech'}), isEmpty);
    });
  });

  group('concentration', () {
    test('even split → high diversification score, low top weight', () {
      final s = concentration([25, 25, 25, 25]);
      expect(s.count, 4);
      expect(s.topWeight, closeTo(25, 0.001));
      expect(s.hhi, closeTo(0.25, 0.001)); // 4 * 0.25^2
      expect(s.score, 75); // (1 - 0.25) * 100
    });

    test('single holding → concentrated (score 0, top 100%)', () {
      final s = concentration([1000]);
      expect(s.count, 1);
      expect(s.topWeight, closeTo(100, 0.001));
      expect(s.hhi, closeTo(1.0, 0.001));
      expect(s.score, 0);
    });

    test('ignores non-positive and handles empty', () {
      expect(concentration([0, -5]).isEmpty, isTrue);
      expect(concentration([]).isEmpty, isTrue);
      final s = concentration([100, 0]);
      expect(s.count, 1);
    });
  });

  group('todaysMovers', () {
    test('sorts by pct descending (gainers first)', () {
      final m = todaysMovers({'A': -2.0, 'B': 5.0, 'C': 1.0});
      expect(m.map((x) => x.symbol).toList(), ['B', 'C', 'A']);
      expect(m.first.pct, 5.0);
      expect(m.last.pct, -2.0);
    });

    test('empty → empty', () {
      expect(todaysMovers({}), isEmpty);
    });
  });

  group('pctChangeOf', () {
    test('computes first→last percent change', () {
      expect(pctChangeOf([100, 110]), closeTo(10.0, 0.001));
      expect(pctChangeOf([100, 90, 95]), closeTo(-5.0, 0.001));
    });

    test('null when not computable', () {
      expect(pctChangeOf([100]), isNull);
      expect(pctChangeOf([]), isNull);
      expect(pctChangeOf([0, 50]), isNull);
    });
  });

  group('riskMetrics', () {
    test('flat curve → zero vol, zero drawdown, null sharpe', () {
      final r = riskMetrics([100, 100, 100, 100]);
      expect(r.volatility, 0);
      expect(r.maxDrawdown, 0);
      expect(r.sharpe, isNull);
    });

    test('max drawdown is the largest peak-to-trough drop', () {
      final r = riskMetrics([100, 120, 90, 110]); // 120→90 = 25%
      expect(r.maxDrawdown, closeTo(25.0, 0.001));
    });

    test('rising curve → positive sharpe, no drawdown', () {
      final r = riskMetrics([100, 101, 102, 103, 104]);
      expect(r.sharpe, isNotNull);
      expect(r.sharpe!, greaterThan(0));
      expect(r.maxDrawdown, 0);
    });

    test('too few points → empty', () {
      expect(riskMetrics([100]).isEmpty, isTrue);
      expect(riskMetrics([]).isEmpty, isTrue);
    });

    test('a funding/deposit jump does not blow up volatility', () {
      // A ~10x deposit (a +900% single-period jump) must be ignored, not
      // produce a garbage ~1000% annualized volatility.
      final r = riskMetrics([100, 1000, 1010, 1005, 1015, 1012]);
      expect(r.volatility, lessThan(200));
    });
  });
}
