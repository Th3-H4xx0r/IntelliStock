import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:intellistock_mobile/features/dashboard/application/portfolio_analytics.dart';
import 'package:intellistock_mobile/features/dashboard/presentation/sector_3d_chart.dart';

void main() {
  const slices = <SectorSlice>[
    SectorSlice(sector: 'Technology', value: 4000, pct: 40),
    SectorSlice(sector: 'Healthcare', value: 2500, pct: 25),
    SectorSlice(sector: 'Financials', value: 1500, pct: 15),
    SectorSlice(sector: 'Energy', value: 1200, pct: 12),
    SectorSlice(sector: 'Consumer', value: 800, pct: 8),
  ];

  Widget harness(double drill) => MaterialApp(
        home: Scaffold(
          backgroundColor: const Color(0xFF0B0B10),
          body: Center(
            child: SizedBox(
              width: 360,
              child: Sector3DChart(slices: slices, debugDrill: drill),
            ),
          ),
        ),
      );

  testWidgets('flat sector chart', (tester) async {
    await tester.pumpWidget(harness(0));
    await tester.pumpAndSettle();
    await expectLater(
      find.byType(Sector3DChart),
      matchesGoldenFile('goldens/sector_3d_flat.png'),
    );
  });

  testWidgets('drilled sector chart', (tester) async {
    await tester.pumpWidget(harness(1));
    await tester.pumpAndSettle();
    await expectLater(
      find.byType(Sector3DChart),
      matchesGoldenFile('goldens/sector_3d_drilled.png'),
    );
  });
}
