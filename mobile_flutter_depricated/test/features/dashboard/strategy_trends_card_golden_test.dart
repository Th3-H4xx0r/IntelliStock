import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:intellistock_mobile/features/dashboard/application/nexus_strategy_controller.dart';
import 'package:intellistock_mobile/features/dashboard/data/nexus_models.dart';
import 'package:intellistock_mobile/features/dashboard/presentation/strategy_section.dart';

final _active = <MarketTrend>[
  MarketTrend.fromJson({
    'name': 'AI Semiconductor Rally',
    'status': 'active',
    'direction': 'bullish',
    'strength': 0.78,
    'affected_tickers': ['NVDA', 'AMD', 'TSM', 'AVGO', 'MU', 'INTC'],
  }),
  MarketTrend.fromJson({
    'name': 'Regional Bank Stress',
    'status': 'active',
    'direction': 'bearish',
    'strength': 0.52,
    'affected_tickers': ['KRE', 'PACW'],
  }),
];

Widget _wrap(NexusTrendsView view) => ProviderScope(
      overrides: [
        nexusTrendsProvider('acct1').overrideWith((ref) async => view),
      ],
      child: const MaterialApp(
        home: Scaffold(
          backgroundColor: Color(0xFF04040C),
          body: SingleChildScrollView(
            padding: EdgeInsets.all(16),
            child: MarketTrendsCardForTest(brokerageId: 'acct1'),
          ),
        ),
      ),
    );

void main() {
  testWidgets('Market Trends card — active trends layout golden', (tester) async {
    await tester.pumpWidget(_wrap(NexusTrendsView(active: _active, recentlyEnded: const [])));
    await tester.pumpAndSettle();
    await expectLater(
      find.byType(MarketTrendsCardForTest),
      matchesGoldenFile('goldens/strategy_trends_card.png'),
    );
  });

  testWidgets('Market Trends card renders the recently-ended section', (tester) async {
    final view = NexusTrendsView(active: _active, recentlyEnded: [
      MarketTrend.fromJson({
        'name': 'Energy Squeeze',
        'status': 'ended',
        'direction': 'bullish',
        'ended_at': '2026-06-16T00:00:00',
      }),
    ]);
    await tester.pumpWidget(_wrap(view));
    await tester.pumpAndSettle();

    expect(find.text('MARKET TRENDS'), findsOneWidget);
    expect(find.text('AI Semiconductor Rally'), findsOneWidget);
    expect(find.text('RECENTLY ENDED'), findsOneWidget);
    expect(find.text('Energy Squeeze'), findsOneWidget);
  });
}
