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
}
