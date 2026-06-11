import 'package:flutter_test/flutter_test.dart';
import 'package:intellistock_mobile/core/charts/chart_geometry.dart';

void main() {
  group('fractionToIndex', () {
    test('returns 0 for empty / single-point series', () {
      expect(fractionToIndex(0.5, 0), 0);
      expect(fractionToIndex(0.5, 1), 0);
    });

    test('maps fraction 0 -> first, 1 -> last', () {
      expect(fractionToIndex(0.0, 5), 0);
      expect(fractionToIndex(1.0, 5), 4);
    });

    test('rounds to nearest index', () {
      // 5 points span fractions 0, .25, .5, .75, 1
      expect(fractionToIndex(0.5, 5), 2);
      expect(fractionToIndex(0.6, 5), 2); // 0.6*4 = 2.4 -> 2
      expect(fractionToIndex(0.7, 5), 3); // 0.7*4 = 2.8 -> 3
    });

    test('clamps out-of-range fractions', () {
      expect(fractionToIndex(-0.3, 5), 0);
      expect(fractionToIndex(1.9, 5), 4);
    });
  });

  group('indexToFraction', () {
    test('returns 0 for empty / single-point series', () {
      expect(indexToFraction(0, 0), 0.0);
      expect(indexToFraction(3, 1), 0.0);
    });

    test('maps first -> 0, last -> 1, middle -> 0.5', () {
      expect(indexToFraction(0, 5), closeTo(0.0, 1e-9));
      expect(indexToFraction(4, 5), closeTo(1.0, 1e-9));
      expect(indexToFraction(2, 5), closeTo(0.5, 1e-9));
    });

    test('round-trips with fractionToIndex', () {
      for (var i = 0; i < 7; i++) {
        expect(fractionToIndex(indexToFraction(i, 7), 7), i);
      }
    });

    test('clamps out-of-range index', () {
      expect(indexToFraction(-2, 5), 0.0);
      expect(indexToFraction(99, 5), 1.0);
    });
  });

  group('paddedBounds', () {
    test('expands min/max by pad fraction of the span', () {
      final b = paddedBounds([10.0, 20.0], padFraction: 0.1);
      expect(b.min, closeTo(9.0, 1e-9)); // 10 - 0.1*10
      expect(b.max, closeTo(21.0, 1e-9)); // 20 + 0.1*10
    });

    test('handles a flat series without collapsing to zero height', () {
      final b = paddedBounds([5.0, 5.0, 5.0]);
      expect(b.max, greaterThan(b.min));
    });

    test('returns a unit band for an empty series', () {
      final b = paddedBounds(const []);
      expect(b.min, 0.0);
      expect(b.max, 1.0);
    });
  });

  group('nearestIndexByTime / timeFractionOf', () {
    final base = DateTime(2026, 1, 1);
    // Unevenly spaced: 0h, 1h, 5h (so index fraction != time fraction).
    final ts = [base, base.add(const Duration(hours: 1)), base.add(const Duration(hours: 5))];

    test('empty / single returns 0', () {
      expect(nearestIndexByTime(const [], 0.5), 0);
      expect(nearestIndexByTime([base], 0.9), 0);
      expect(timeFractionOf(const [], 0), 0.0);
      expect(timeFractionOf([base], 0), 0.0);
    });

    test('fraction 0 -> first, 1 -> last', () {
      expect(nearestIndexByTime(ts, 0.0), 0);
      expect(nearestIndexByTime(ts, 1.0), 2);
    });

    test('maps by time, not index', () {
      // 0.5 of a 5h span = 2.5h -> closest is the 1h point (index 1), since the
      // next point is at 5h. An index-based mapping would have chosen index 1
      // too here, so use a clearer case: 0.7*5h = 3.5h -> closer to 5h (index 2).
      expect(nearestIndexByTime(ts, 0.7), 2);
      expect(nearestIndexByTime(ts, 0.1), 0); // 0.5h -> closer to 0h
    });

    test('timeFractionOf returns the point time fraction', () {
      expect(timeFractionOf(ts, 0), closeTo(0.0, 1e-9));
      expect(timeFractionOf(ts, 1), closeTo(0.2, 1e-9)); // 1h / 5h
      expect(timeFractionOf(ts, 2), closeTo(1.0, 1e-9));
    });

    test('round-trips: nearestIndexByTime(timeFractionOf(i)) == i', () {
      for (var i = 0; i < ts.length; i++) {
        expect(nearestIndexByTime(ts, timeFractionOf(ts, i)), i);
      }
    });
  });

  group('valueToY', () {
    test('max value maps to top (y=0), min to bottom (y=height)', () {
      expect(valueToY(20.0, 10.0, 20.0, 100.0), closeTo(0.0, 1e-9));
      expect(valueToY(10.0, 10.0, 20.0, 100.0), closeTo(100.0, 1e-9));
    });

    test('midpoint maps to half height', () {
      expect(valueToY(15.0, 10.0, 20.0, 100.0), closeTo(50.0, 1e-9));
    });

    test('clamps values outside the range', () {
      expect(valueToY(30.0, 10.0, 20.0, 100.0), closeTo(0.0, 1e-9));
      expect(valueToY(0.0, 10.0, 20.0, 100.0), closeTo(100.0, 1e-9));
    });

    test('degenerate range maps to mid-height', () {
      expect(valueToY(5.0, 5.0, 5.0, 100.0), closeTo(50.0, 1e-9));
    });
  });
}
