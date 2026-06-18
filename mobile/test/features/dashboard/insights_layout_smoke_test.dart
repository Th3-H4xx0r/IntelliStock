import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:intellistock_mobile/core/widgets/glass_card.dart';

// Reproduces the dashboard Insights layout primitives inside a CustomScrollView
// to surface any layout/render exception (the section renders blank on device).
void main() {
  Widget host(Widget child) => MaterialApp(
        home: Scaffold(
          body: CustomScrollView(
            slivers: [
              SliverList(
                delegate: SliverChildListDelegate([
                  Padding(padding: const EdgeInsets.all(16), child: child),
                ]),
              ),
            ],
          ),
        ),
      );

  testWidgets('default GlassCard renders inside a sliver', (tester) async {
    await tester.pumpWidget(host(
      const GlassCard(child: Text('TODAY')),
    ));
    expect(tester.takeException(), isNull);
    expect(find.text('TODAY'), findsOneWidget);
  });

  // The fixed pattern: liquid cards (no BackdropFilter) + default Row cross-axis
  // (no `stretch`, so no intrinsic-height query that BackdropFilter can't answer).
  testWidgets('two Expanded liquid GlassCards in a Row render', (tester) async {
    await tester.pumpWidget(host(
      Row(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: const [
          Expanded(child: GlassCard(liquid: true, child: Text('TODAY'))),
          SizedBox(width: 12),
          Expanded(child: GlassCard(liquid: true, child: Text('DIVERSIFICATION'))),
        ],
      ),
    ));
    expect(tester.takeException(), isNull);
    expect(find.text('TODAY'), findsOneWidget);
    expect(find.text('DIVERSIFICATION'), findsOneWidget);
  });

  testWidgets('liquid GlassCard variant renders inside a sliver', (tester) async {
    await tester.pumpWidget(host(
      const GlassCard(liquid: true, child: Text('INDICES')),
    ));
    expect(tester.takeException(), isNull);
    expect(find.text('INDICES'), findsOneWidget);
  });
}
