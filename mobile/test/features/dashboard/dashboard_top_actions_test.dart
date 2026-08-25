import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:intellistock_mobile/features/dashboard/presentation/dashboard_screen.dart';

void main() {
  testWidgets('keeps the dashboard hero free of a Portfolio heading',
      (tester) async {
    await tester.pumpWidget(
      const MaterialApp(home: Scaffold(body: DashboardTopActions())),
    );

    expect(find.text('Portfolio'), findsNothing);
    expect(find.byTooltip('Search symbols'), findsOneWidget);
  });
}
