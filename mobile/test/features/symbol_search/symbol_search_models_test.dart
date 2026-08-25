import 'package:flutter_test/flutter_test.dart';
import 'package:intellistock_mobile/features/symbol_search/data/symbol_search_models.dart';

void main() {
  test('matches ticker and company name without losing the result metadata', () {
    final result = SearchInstrument.fromJson({
      'symbol': 'BTC-USD',
      'name': 'Bitcoin USD',
      'type': 'Crypto',
    });

    expect(result.matches('btc'), isTrue);
    expect(result.matches('bitcoin'), isTrue);
    expect(result.matches('ethereum'), isFalse);
    expect(result.symbol, 'BTC-USD');
    expect(result.type, 'Crypto');
  });

  test('builds a unique symbol batch for result sparklines', () {
    const results = [
      SearchInstrument(symbol: 'AAPL', name: 'Apple', type: 'Stock'),
      SearchInstrument(symbol: 'SPY', name: 'SPDR S&P 500 ETF Trust', type: 'ETF'),
      SearchInstrument(symbol: 'AAPL', name: 'Apple Inc.', type: 'Stock'),
    ];

    expect(searchSymbolsForSparklines(results), ['AAPL', 'SPY']);
  });
}
