import 'package:flutter_test/flutter_test.dart';
import 'package:intellistock_mobile/features/live_trading/data/models/live_state.dart';

// A live-state position after plan A-live: Alpaca had no quote for the put.
const _shortPut = <String, dynamic>{
  'symbol': 'APH261002P00130000',
  'qty': -1,
  'avg_entry_price': 1.23,
  'last_price': null,
  'market_value': null,
  'unrealized_pnl': null,
  'unrealized_pnl_pct': null,
  'asset_class': 'us_option',
  'side': 'short',
  'multiplier': 100,
  'underlying': 'APH',
  'strike': 130,
  'expiry': '2026-10-02',
};

void main() {
  group('Position', () {
    test('a short put with no quote keeps its nulls', () {
      final p = Position.fromJson(_shortPut);
      expect(p.isOption, isTrue);
      expect(p.isShort, isTrue);
      expect(p.contractMultiplier, 100);
      expect(p.quantityLabel, 'CONTRACTS');
      expect(p.quantityText, '1');
      expect(p.canClose, isFalse);
      expect(p.lastPrice, isNull);
      expect(p.marketValue, isNull);
      expect(p.unrealizedPnl, isNull);
      expect(p.optionDescription, 'APH \$130 Put · 2026-10-02');
    });

    test('an equity row from today\'s API parses as before', () {
      final p = Position.fromJson(const {
        'symbol': 'AAPL',
        'qty': 12.5,
        'avg_entry_price': 190.0,
        'last_price': 200.0,
        'market_value': 2500.0,
        'unrealized_pnl': 125.0,
        'unrealized_pnl_pct': 5.26,
      });
      expect(p.isOption, isFalse);
      expect(p.isShort, isFalse);
      expect(p.contractMultiplier, 1);
      expect(p.quantityLabel, 'SHARES');
      expect(p.quantityText, '12.5000');
      expect(p.canClose, isTrue);
      expect(p.marketValue, 2500.0);
      expect(p.optionDescription, '');
    });

    test('an OCC symbol without asset_class is still an option', () {
      final p = Position.fromJson(const {'symbol': 'APH261002P00130000', 'qty': -2});
      expect(p.isOption, isTrue);
      expect(p.isShort, isTrue);
      expect(p.contractMultiplier, 100);
    });
  });

  group('Trade', () {
    test('an option fill totals x100 and counts contracts', () {
      final t = Trade.fromJson(const {
        'symbol': 'APH261002P00130000', 'side': 'sell', 'qty': 1, 'price': 1.25,
      });
      expect(t.isOption, isTrue);
      expect(t.quantityLabel, 'CONTRACTS');
      expect(t.quantityText, '1');
      expect(t.total, 125.0);
    });

    test('a stock fill is unchanged', () {
      final t = Trade.fromJson(const {
        'symbol': 'AAPL', 'side': 'buy', 'qty': 12, 'price': 200.0,
      });
      expect(t.isOption, isFalse);
      expect(t.quantityLabel, 'SHARES');
      expect(t.quantityText, '12.0000');
      expect(t.total, 2400.0);
    });
  });
}
