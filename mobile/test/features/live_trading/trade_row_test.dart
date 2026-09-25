import 'package:flutter/material.dart';
import 'package:flutter/rendering.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:intellistock_mobile/features/live_trading/data/models/live_state.dart';
import 'package:intellistock_mobile/features/live_trading/presentation/live_trading_screen.dart';

const _occ = 'APH261002P00130000';

Future<void> _pumpRow(WidgetTester tester, Trade trade, double width) async {
  tester.view.physicalSize = Size(width * 3, 800 * 3);
  tester.view.devicePixelRatio = 3.0;
  addTearDown(tester.view.resetPhysicalSize);
  addTearDown(tester.view.resetDevicePixelRatio);
  await tester.pumpWidget(MaterialApp(
    home: Scaffold(
      body: Column(children: [TradeRowForTest(trade: trade)]),
    ),
  ));
  await tester.pumpAndSettle();
}

// The trade row's top line: side, symbol (+ badge), fill price.
Finder _topRowOf(Finder child) =>
    find.ancestor(of: child, matching: find.byType(Row)).first;

void main() {
  testWidgets('stock fill: the fill price sits flush with the row\'s right edge',
      (tester) async {
    await _pumpRow(
      tester,
      Trade.fromJson(const {
        'symbol': 'AAPL', 'side': 'buy', 'qty': 12, 'price': 200.0,
      }),
      360,
    );

    expect(tester.takeException(), isNull);
    final price = find.text('\$200.00');
    expect(tester.getRect(price).right,
        moreOrLessEquals(tester.getRect(_topRowOf(price)).right, epsilon: 0.01));
  });

  testWidgets('option fill: the OCC symbol is not truncated when the row has room',
      (tester) async {
    await _pumpRow(
      tester,
      Trade.fromJson(const {
        'symbol': _occ, 'side': 'sell', 'qty': 1, 'price': 1.25,
      }),
      360,
    );

    expect(tester.takeException(), isNull);
    expect(find.text('OPTION'), findsOneWidget);
    final para = tester.renderObject<RenderParagraph>(find.text(_occ));
    expect(para.didExceedMaxLines, isFalse);
    expect(tester.getSize(find.text(_occ)).width,
        greaterThanOrEqualTo(para.getMaxIntrinsicWidth(double.infinity) - 0.01));
    // And the price column is still flush right.
    final price = find.text('\$1.25');
    expect(tester.getRect(price).right,
        moreOrLessEquals(tester.getRect(_topRowOf(price)).right, epsilon: 0.01));
  });

  testWidgets('option fill at 320pt: no overflow, badge and price still shown',
      (tester) async {
    await _pumpRow(
      tester,
      Trade.fromJson(const {
        'symbol': _occ, 'side': 'sell', 'qty': 1, 'price': 1.25,
      }),
      320,
    );

    expect(tester.takeException(), isNull);
    expect(find.text('OPTION'), findsOneWidget);
    expect(find.text('\$1.25'), findsOneWidget);
    expect(find.text('CONTRACTS'), findsOneWidget);
    expect(find.text('\$125.00'), findsOneWidget);
  });
}
