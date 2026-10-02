import 'package:flutter_test/flutter_test.dart';
import 'package:intellistock_mobile/core/models/portfolio_history.dart';
import 'package:intellistock_mobile/features/live_trading/presentation/equity_chart.dart';

void main() {
  PortfolioHistory makeHistory(List<double> values) {
    final now = DateTime(2026, 6, 10, 9, 30);
    return PortfolioHistory(
      timestamps: List.generate(values.length, (i) => now.add(Duration(minutes: i))),
      values: values,
    );
  }

  group('RangeStats.from', () {
    test('empty history → zeros, isUp=true, no high/low', () {
      final stats = RangeStats.from(null);
      expect(stats.dollars, 0);
      expect(stats.pct, 0);
      expect(stats.isUp, isTrue);
      expect(stats.high, isNull);
      expect(stats.low, isNull);
    });

    test('single value → zeros, no high/low', () {
      final h = makeHistory([1000]);
      final stats = RangeStats.from(h);
      expect(stats.high, isNull); // only 1 point → <2 check
    });

    test('up trend → positive dollars + pct, isUp=true', () {
      final h = makeHistory([100, 110, 115, 120]);
      final stats = RangeStats.from(h);
      expect(stats.dollars, closeTo(20, 0.001));
      expect(stats.pct, closeTo(20, 0.001));
      expect(stats.isUp, isTrue);
      expect(stats.high, closeTo(120, 0.001));
      expect(stats.low, closeTo(100, 0.001));
    });

    test('down trend → negative dollars, isUp=false', () {
      final h = makeHistory([200, 190, 180, 175]);
      final stats = RangeStats.from(h);
      expect(stats.dollars, closeTo(-25, 0.001));
      expect(stats.isUp, isFalse);
    });

    test('flat → dollars=0, isUp=true', () {
      final h = makeHistory([500, 500, 500]);
      final stats = RangeStats.from(h);
      expect(stats.dollars, closeTo(0, 0.001));
      expect(stats.isUp, isTrue);
    });

    test('high is the max of all values', () {
      final h = makeHistory([100, 250, 150, 180]);
      final stats = RangeStats.from(h);
      expect(stats.high, closeTo(250, 0.001));
      expect(stats.low, closeTo(100, 0.001));
    });

    test('pct calculation: (end-start)/start * 100', () {
      final h = makeHistory([200, 220]);
      final stats = RangeStats.from(h);
      expect(stats.pct, closeTo(10, 0.001));
    });

    test('start is zero → pct = 0.0 (no division by zero)', () {
      final h = makeHistory([0, 100]);
      final stats = RangeStats.from(h);
      expect(stats.pct, 0);
    });
  });
}
