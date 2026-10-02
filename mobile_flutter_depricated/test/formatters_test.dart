import 'package:flutter_test/flutter_test.dart';
import 'package:intellistock_mobile/core/formatters/formatters.dart';

void main() {
  group('fmtMoney', () {
    test('positive', () => expect(fmtMoney(1234.5), r'$1,234.50'));
    test('negative', () => expect(fmtMoney(-12), r'-$12.00'));
    test('null', () => expect(fmtMoney(null), '—'));
  });

  group('fmtPnl', () {
    test('positive has +', () => expect(fmtPnl(1234.5), r'+$1,234.50'));
    test('negative', () => expect(fmtPnl(-12), r'-$12.00'));
    test('zero is +', () => expect(fmtPnl(0), r'+$0.00'));
    test('null', () => expect(fmtPnl(null), '—'));
  });

  group('fmtPct', () {
    test('rounds to 2dp', () => expect(fmtPct(12.345), '+12.35%'));
    test('negative', () => expect(fmtPct(-5), '-5.00%'));
    test('null', () => expect(fmtPct(null), '—'));
  });

  group('fmtUsdCost', () {
    test('under a dollar uses 4dp', () => expect(fmtUsdCost(0.0004), r'$0.0004'));
    test('over a dollar uses 2dp', () => expect(fmtUsdCost(2), r'$2.00'));
    test('zero', () => expect(fmtUsdCost(0), r'$0.00'));
  });

  group('fmtTokens', () {
    test('millions', () => expect(fmtTokens(1200000), '1.2M'));
    test('thousands', () => expect(fmtTokens(3400), '3.4k'));
    test('raw', () => expect(fmtTokens(950), '950'));
  });

  group('fmtElapsed', () {
    test('seconds', () => expect(fmtElapsed(45), '45s'));
    test('minutes', () => expect(fmtElapsed(95), '1m 35s'));
    test('hours', () => expect(fmtElapsed(3661), '1h 1m 1s'));
    test('days', () => expect(fmtElapsed(90061), '1d 1h 1m'));
  });

  group('fmtDuration', () {
    test('sub-minute integer', () => expect(fmtDuration(3), '3s'));
    test('sub-minute fractional', () => expect(fmtDuration(1.5), '1.5s'));
    test('minutes', () => expect(fmtDuration(200), '3m 20s'));
    test('hours', () => expect(fmtDuration(7500), '2h 5m'));
  });

  group('parseDateTime', () {
    test('epoch seconds', () {
      final dt = parseDateTime(1700000000);
      expect(dt!.year, 2023);
    });
    test('null', () => expect(parseDateTime(null), isNull));
  });
}
