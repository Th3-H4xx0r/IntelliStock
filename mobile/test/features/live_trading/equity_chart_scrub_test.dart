import 'dart:math';

import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:intellistock_mobile/core/models/portfolio_history.dart';
import 'package:intellistock_mobile/features/live_trading/presentation/equity_chart.dart';

void main() {
  // Regression guard for the scrub-misalignment bug: dragging the left edge of
  // the chart must report the FIRST data point and the right edge the LAST.
  // The old scrubber mapped the finger across the full widget while the curve
  // lived inside the y-axis label gutter, so the reported index was shifted.
  testWidgets(
    'scrub maps left edge -> first point and right edge -> last point',
    (tester) async {
      final history = PortfolioHistory(
        timestamps: List.generate(
          11,
          (i) => DateTime(2026, 1, 1).add(Duration(minutes: i)),
        ),
        values: List.generate(11, (i) => i.toDouble()),
      );

      final reported = <int?>[];

      await tester.pumpWidget(
        MaterialApp(
          home: Scaffold(
            body: Center(
              child: SizedBox(
                width: 220,
                child: EquityChart(
                  history: history,
                  style: ChartStyle.line,
                  range: '1D',
                  height: 240,
                  onScrub: reported.add,
                ),
              ),
            ),
          ),
        ),
      );
      // Let the series animation finish without relying on pumpAndSettle.
      await tester.pump(const Duration(seconds: 2));

      final rect = tester.getRect(find.byType(EquityChart));
      final y = rect.top + 60;

      final gesture = await tester.startGesture(Offset(rect.left + 3, y));
      await gesture.moveTo(Offset(rect.left + 30, y)); // exceed slop -> drag starts
      await tester.pump();
      await gesture.moveTo(Offset(rect.left + 2, y)); // settle on the far left
      await tester.pump();
      await gesture.moveTo(Offset(rect.right - 2, y)); // sweep to the far right
      await tester.pump();
      await gesture.up();
      await tester.pump();

      final indices = reported.whereType<int>().toList();
      expect(indices, isNotEmpty, reason: 'reported=$reported rect=$rect');
      // Far left must report ~first point, far right ~last point.
      expect(indices.reduce(min), lessThanOrEqualTo(1),
          reason: 'reported=$reported rect=$rect');
      expect(indices.reduce(max), greaterThanOrEqualTo(9),
          reason: 'reported=$reported rect=$rect');
    },
  );
}
