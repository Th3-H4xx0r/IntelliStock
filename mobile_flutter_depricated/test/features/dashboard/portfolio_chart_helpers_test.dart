import 'package:flutter_test/flutter_test.dart';
import 'package:intellistock_mobile/features/dashboard/presentation/portfolio_chart.dart';
import 'package:intellistock_mobile/core/models/portfolio_history.dart';

void main() {
  group('computeChange', () {
    PortfolioHistory makeHistory({
      required List<double> values,
      double? openValue,
      double? currentValue,
    }) {
      final now = DateTime(2026, 1, 1);
      return PortfolioHistory(
        timestamps: List.generate(values.length,
            (i) => now.add(Duration(hours: i))),
        values: values,
        openValue: openValue,
        currentValue: currentValue,
      );
    }

    test('returns (null, null) for empty history', () {
      final h = makeHistory(values: []);
      final (a, p) = computeChange(h);
      expect(a, isNull);
      expect(p, isNull);
    });

    test('positive change uses open_value as baseline', () {
      final h = makeHistory(
          values: [100.0, 110.0, 120.0], openValue: 100.0, currentValue: 120.0);
      final (a, p) = computeChange(h);
      expect(a, closeTo(20.0, 0.001));
      expect(p, closeTo(20.0, 0.001));
    });

    test('negative change is computed correctly', () {
      final h = makeHistory(
          values: [200.0, 190.0, 180.0], openValue: 200.0, currentValue: 180.0);
      final (a, p) = computeChange(h);
      expect(a, closeTo(-20.0, 0.001));
      expect(p, closeTo(-10.0, 0.001));
    });

    test('scrubIndex overrides current_value', () {
      final h = makeHistory(
          values: [100.0, 105.0, 110.0, 115.0], openValue: 100.0);
      // scrubIndex=1 → active=105, change=+5
      final (a, p) = computeChange(h, scrubIndex: 1);
      expect(a, closeTo(5.0, 0.001));
      expect(p, closeTo(5.0, 0.001));
    });

    test('falls back to first value as baseline when no open_value', () {
      final h = makeHistory(values: [50.0, 60.0, 70.0]);
      final (a, p) = computeChange(h);
      expect(a, closeTo(20.0, 0.001));
      expect(p, closeTo(40.0, 0.001));
    });

    test('returns null pct when baseline is zero', () {
      final h = makeHistory(values: [0.0, 10.0], openValue: 0.0);
      final (a, p) = computeChange(h);
      expect(a, closeTo(10.0, 0.001));
      expect(p, isNull);
    });
  });

  group('nearestIndex', () {
    late List<DateTime> timestamps;

    setUp(() {
      final base = DateTime(2026, 1, 1);
      timestamps = List.generate(
          5, (i) => base.add(Duration(hours: i))); // 0h, 1h, 2h, 3h, 4h
    });

    test('fraction=0 → index 0', () {
      expect(nearestIndex(timestamps, 0.0), 0);
    });

    test('fraction=1 → last index', () {
      expect(nearestIndex(timestamps, 1.0), 4);
    });

    test('fraction=0.5 → middle index', () {
      expect(nearestIndex(timestamps, 0.5), 2);
    });

    test('fraction slightly below midpoint chooses lower index', () {
      // 0.375 is 1.5h into 4h span → 1h is closer than 2h
      expect(nearestIndex(timestamps, 0.375), 1);
    });

    test('returns 0 for empty list', () {
      expect(nearestIndex([], 0.5), 0);
    });

    test('returns 0 for single-element list', () {
      expect(nearestIndex([DateTime.now()], 0.9), 0);
    });
  });

  group('sinceLocalMidnight', () {
    test('baselines at local midnight and trims to the day', () {
      final now = DateTime.now();
      final midnight = DateTime(now.year, now.month, now.day);
      final h = PortfolioHistory(
        timestamps: [
          midnight.subtract(const Duration(hours: 2)),
          midnight.subtract(const Duration(hours: 1)),
          midnight.add(const Duration(hours: 1)),
          midnight.add(const Duration(hours: 2)),
        ],
        values: const [100.0, 110.0, 120.0, 130.0],
        currentValue: 130.0,
      );
      final r = h.sinceLocalMidnight();
      // baseline = last pre-midnight value, carried to a point at 00:00
      expect(r.openValue, 110.0);
      expect(r.timestamps.first, midnight);
      expect(r.values.first, 110.0);
      // pre-midnight samples dropped, post-midnight kept
      expect(r.values, [110.0, 120.0, 130.0]);
      expect(r.changeAbs, closeTo(20.0, 0.001));
      expect(r.changePct, closeTo(20.0 / 110.0 * 100, 0.001));
    });

    test('falls back to first value when no pre-midnight sample exists', () {
      final now = DateTime.now();
      final midnight = DateTime(now.year, now.month, now.day);
      final h = PortfolioHistory(
        timestamps: [
          midnight.add(const Duration(hours: 1)),
          midnight.add(const Duration(hours: 2)),
        ],
        values: const [200.0, 210.0],
        currentValue: 210.0,
      );
      final r = h.sinceLocalMidnight();
      expect(r.openValue, 200.0);
      expect(r.timestamps.first, midnight);
      expect(r.values, [200.0, 200.0, 210.0]);
      expect(r.changeAbs, closeTo(10.0, 0.001));
    });

    test('is a no-op for empty history', () {
      final r = PortfolioHistory(timestamps: const [], values: const [])
          .sinceLocalMidnight();
      expect(r.isEmpty, isTrue);
    });
  });
}
