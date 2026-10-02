import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:intellistock_mobile/core/widgets/relative_time_text.dart';

void main() {
  testWidgets('renders relative time and ticks as the clock advances',
      (tester) async {
    var fakeNow = DateTime(2026, 6, 16, 12, 0, 0);
    final ts = DateTime(2026, 6, 16, 11, 58, 0); // 2 minutes earlier

    await tester.pumpWidget(MaterialApp(
      home: Scaffold(
        body: RelativeTimeText(
          timestamp: ts,
          tick: const Duration(seconds: 1),
          clock: () => fakeNow,
        ),
      ),
    ));

    expect(find.text('2m ago'), findsOneWidget);

    // Advance the injected clock by 3 minutes, let one tick fire.
    fakeNow = DateTime(2026, 6, 16, 12, 3, 0); // now 5 minutes after ts
    await tester.pump(const Duration(seconds: 1));

    expect(find.text('5m ago'), findsOneWidget);
  });

  testWidgets('shows "Just now" for a fresh timestamp', (tester) async {
    final now = DateTime(2026, 6, 16, 12, 0, 0);
    await tester.pumpWidget(MaterialApp(
      home: Scaffold(
        body: RelativeTimeText(
          timestamp: now.subtract(const Duration(seconds: 5)),
          clock: () => now,
        ),
      ),
    ));
    expect(find.text('Just now'), findsOneWidget);
  });
}
