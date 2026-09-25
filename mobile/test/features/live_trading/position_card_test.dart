import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:intellistock_mobile/features/live_trading/data/models/live_state.dart';
import 'package:intellistock_mobile/features/live_trading/presentation/equity_chart.dart';
import 'package:intellistock_mobile/features/live_trading/presentation/position_card.dart';

Widget _card(Position p, {VoidCallback? onClose}) => MaterialApp(
      home: Scaffold(
        body: SingleChildScrollView(
          child: PositionCard(
            position: p,
            chartStyle: ChartStyle.area,
            range: '1D',
            historicals: const [],
            onClose: onClose ?? () {},
          ),
        ),
      ),
    );

void main() {
  testWidgets('short put with no quote: contracts, badges, dashes, no Close',
      (tester) async {
    tester.view.physicalSize = const Size(320 * 3, 1200 * 3);
    tester.view.devicePixelRatio = 3.0;
    addTearDown(tester.view.resetPhysicalSize);
    addTearDown(tester.view.resetDevicePixelRatio);

    await tester.pumpWidget(_card(Position.fromJson(const {
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
    })));
    await tester.pumpAndSettle();

    expect(tester.takeException(), isNull);
    expect(find.text('OPTION'), findsOneWidget);
    expect(find.text('SHORT'), findsOneWidget);
    expect(find.text('APH \$130 Put · 2026-10-02'), findsOneWidget);
    expect(find.text('CONTRACTS'), findsOneWidget);
    expect(find.text('SHARES'), findsNothing);
    expect(find.text('1'), findsOneWidget);
    expect(find.text('No price chart for options'), findsOneWidget);
    expect(find.text('\$0.00'), findsNothing);
    // MARKET VALUE, LAST and P&L $ have no value to show.
    expect(find.text('—'), findsNWidgets(3));
    expect(find.text('Close'), findsNothing);
    expect(
        find.text('Managed by the wheel lane, which buys puts back '
            'automatically. Close it at the broker if needed.'),
        findsOneWidget);
    expect(find.textContaining('Halt'), findsNothing);
  });

  testWidgets('stock position keeps Shares and Close', (tester) async {
    var closed = false;
    await tester.pumpWidget(_card(
      Position.fromJson(const {
        'symbol': 'AAPL',
        'qty': 12,
        'avg_entry_price': 190.0,
        'last_price': 200.0,
        'market_value': 2400.0,
        'unrealized_pnl': 120.0,
        'unrealized_pnl_pct': 5.26,
      }),
      onClose: () => closed = true,
    ));
    await tester.pumpAndSettle();

    expect(find.text('SHARES'), findsOneWidget);
    expect(find.text('12.0000'), findsOneWidget);
    expect(find.text('OPTION'), findsNothing);
    expect(find.text('\$2,400.00'), findsOneWidget);
    await tester.tap(find.text('Close'));
    expect(closed, isTrue);
  });
}
