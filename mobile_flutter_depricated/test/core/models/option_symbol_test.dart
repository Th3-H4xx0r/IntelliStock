import 'package:flutter_test/flutter_test.dart';
import 'package:intellistock_mobile/core/models/option_symbol.dart';

void main() {
  test('parseOccSymbol reads root, expiry, type and strike', () {
    final put = parseOccSymbol('APH261002P00130000')!;
    expect(put.underlying, 'APH');
    expect(put.expiry, '2026-10-02');
    expect(put.optionType, 'put');
    expect(put.strike, 130.0);
    final call = parseOccSymbol('spy261218c00612500')!;
    expect(call.underlying, 'SPY');
    expect(call.optionType, 'call');
    expect(call.strike, 612.5);
  });

  test('stock tickers and junk are not options', () {
    for (final s in ['AAPL', 'BRK.B', '', null, 'APH261002X00130000',
        'TOOLONGROOT261002P00130000']) {
      expect(isOccOptionSymbol(s), isFalse, reason: '$s');
    }
  });

  test('describeOptionContract prefers explicit fields, falls back to the symbol', () {
    expect(describeOptionContract(symbol: 'APH261002P00130000'),
        'APH \$130 Put · 2026-10-02');
    expect(
        describeOptionContract(
            symbol: 'APH261002P00130000', underlying: 'APH', strike: 127.5),
        'APH \$127.50 Put · 2026-10-02');
    expect(describeOptionContract(symbol: 'AAPL'), '');
  });
}
