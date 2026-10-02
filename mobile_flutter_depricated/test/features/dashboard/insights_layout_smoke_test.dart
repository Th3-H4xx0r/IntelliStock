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

  // The fixed pattern: default Row cross-axis (no `stretch`, so no intrinsic
  // -height query that BackdropFilter can't answer). frosted cards (with the
  // BackdropFilter blur) render fine here — the holdings card proves it on the
  // real dashboard; the blank was the stretch, not the blur.
  testWidgets('two Expanded frosted GlassCards in a Row render', (tester) async {
    await tester.pumpWidget(host(
      Row(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: const [
          Expanded(child: GlassCard(frosted: true, child: Text('TODAY'))),
          SizedBox(width: 12),
          Expanded(
              child: GlassCard(frosted: true, child: Text('DIVERSIFICATION'))),
        ],
      ),
    ));
    expect(tester.takeException(), isNull);
    expect(find.text('TODAY'), findsOneWidget);
    expect(find.text('DIVERSIFICATION'), findsOneWidget);
  });

  testWidgets('frosted GlassCard variant renders inside a sliver', (tester) async {
    await tester.pumpWidget(host(
      const GlassCard(frosted: true, child: Text('INDICES')),
    ));
    expect(tester.takeException(), isNull);
    expect(find.text('INDICES'), findsOneWidget);
  });
}
