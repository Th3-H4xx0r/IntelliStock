import 'package:flutter_test/flutter_test.dart';
import 'package:intellistock_mobile/features/dashboard/application/market_hours.dart';

void main() {
  // Inputs are wall-clock ET (the caller converts via etFromUtc).
  test('open during the regular weekday session', () {
    expect(isMarketOpenAtEt(DateTime(2026, 6, 18, 10, 0)), isTrue); // Thu 10:00
    expect(isMarketOpenAtEt(DateTime(2026, 6, 18, 9, 30)), isTrue);
    expect(isMarketOpenAtEt(DateTime(2026, 6, 18, 15, 59)), isTrue);
  });

  test('closed before open, at/after close, and on weekends', () {
    expect(isMarketOpenAtEt(DateTime(2026, 6, 18, 9, 29)), isFalse);
    expect(isMarketOpenAtEt(DateTime(2026, 6, 18, 16, 0)), isFalse);
    expect(isMarketOpenAtEt(DateTime(2026, 6, 20, 12, 0)), isFalse); // Sat
    expect(isMarketOpenAtEt(DateTime(2026, 6, 21, 12, 0)), isFalse); // Sun
  });

  test('etFromUtc subtracts 4h in summer (EDT)', () {
    // 18:00 UTC in June → 14:00 ET.
    final et = etFromUtc(DateTime.utc(2026, 6, 18, 18, 0));
    expect(et.hour, 14);
  });
}
