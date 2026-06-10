import 'package:flutter_test/flutter_test.dart';
import 'package:intellistock_mobile/features/live_trading/presentation/manual_order_sheet.dart';

void main() {
  group('validateOrderForm — qty/notional XOR', () {
    test('empty symbol → error', () {
      final err = validateOrderForm(const OrderForm(symbol: '', qty: '1'));
      expect(err, isNotNull);
      expect(err, contains('Symbol'));
    });

    test('neither qty nor notional → error', () {
      final err = validateOrderForm(const OrderForm(symbol: 'AAPL'));
      expect(err, isNotNull);
      expect(err, contains('qty or notional'));
    });

    test('both qty and notional → error', () {
      final err = validateOrderForm(
        const OrderForm(symbol: 'AAPL', qty: '10', notional: '500'),
      );
      expect(err, isNotNull);
      expect(err, contains('not both'));
    });

    test('qty only, valid → null', () {
      final err = validateOrderForm(
        const OrderForm(symbol: 'AAPL', qty: '10'),
      );
      expect(err, isNull);
    });

    test('notional only, valid → null', () {
      final err = validateOrderForm(
        const OrderForm(symbol: 'AAPL', notional: '500'),
      );
      expect(err, isNull);
    });

    test('qty <= 0 → error', () {
      final err = validateOrderForm(
        const OrderForm(symbol: 'AAPL', qty: '-5'),
      );
      expect(err, isNotNull);
      expect(err, contains('positive'));
    });

    test('qty not a number → error', () {
      final err = validateOrderForm(
        const OrderForm(symbol: 'AAPL', qty: 'abc'),
      );
      expect(err, isNotNull);
    });

    test('notional <= 0 → error', () {
      final err = validateOrderForm(
        const OrderForm(symbol: 'AAPL', notional: '0'),
      );
      expect(err, isNotNull);
    });

    test('limit order missing limit_price → error', () {
      final err = validateOrderForm(
        const OrderForm(symbol: 'AAPL', qty: '5', orderType: 'limit'),
      );
      expect(err, isNotNull);
      expect(err, contains('limit price'));
    });

    test('limit order with valid limit_price → null', () {
      final err = validateOrderForm(
        const OrderForm(
          symbol: 'AAPL',
          qty: '5',
          orderType: 'limit',
          limitPrice: '150.00',
        ),
      );
      expect(err, isNull);
    });

    test('extended_hours with non-limit type → error', () {
      final err = validateOrderForm(
        const OrderForm(
          symbol: 'AAPL',
          qty: '5',
          orderType: 'market',
          extendedHours: true,
        ),
      );
      expect(err, isNotNull);
      expect(err, contains('Extended hours'));
    });

    test('extended_hours with limit+day → null', () {
      final err = validateOrderForm(
        const OrderForm(
          symbol: 'AAPL',
          qty: '5',
          orderType: 'limit',
          limitPrice: '150',
          tif: 'day',
          extendedHours: true,
        ),
      );
      expect(err, isNull);
    });

    test('extended_hours with limit+gtc → error', () {
      final err = validateOrderForm(
        const OrderForm(
          symbol: 'AAPL',
          qty: '5',
          orderType: 'limit',
          limitPrice: '150',
          tif: 'gtc',
          extendedHours: true,
        ),
      );
      expect(err, isNotNull);
      expect(err, contains('day'));
    });
  });

  group('buildOrderPayload', () {
    test('qty order builds correct payload', () {
      final p = buildOrderPayload(const OrderForm(
        symbol: 'AAPL',
        side: 'buy',
        orderType: 'market',
        qty: '10.5',
        tif: 'day',
      ));
      expect(p['symbol'], 'AAPL');
      expect(p['side'], 'buy');
      expect(p['qty'], 10.5);
      expect(p.containsKey('notional'), isFalse);
    });

    test('notional order excludes qty', () {
      final p = buildOrderPayload(const OrderForm(
        symbol: 'TSLA',
        side: 'sell',
        orderType: 'market',
        notional: '1000',
        tif: 'gtc',
      ));
      expect(p['notional'], 1000.0);
      expect(p.containsKey('qty'), isFalse);
    });

    test('limit order includes limit_price', () {
      final p = buildOrderPayload(const OrderForm(
        symbol: 'SPY',
        side: 'buy',
        orderType: 'limit',
        qty: '2',
        limitPrice: '450.50',
        tif: 'day',
      ));
      expect(p['limit_price'], 450.50);
    });

    test('symbol is upper-cased', () {
      final p = buildOrderPayload(const OrderForm(
        symbol: 'msft',
        qty: '1',
      ));
      expect(p['symbol'], 'MSFT');
    });
  });
}
