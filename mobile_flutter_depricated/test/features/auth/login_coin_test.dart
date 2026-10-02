import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:intellistock_mobile/features/auth/presentation/login_coin.dart';

void main() {
  testWidgets('starts the GLB animation when the viewer load callback is missed',
      (tester) async {
    await tester.pumpWidget(
      const MaterialApp(
        home: Scaffold(body: LoginCoin(phase: CoinPhase.idle)),
      ),
    );
    // The production viewer is a platform WebView. Widget tests do not
    // register its platform implementation, so discard that expected harness
    // exception and assert the Flutter-side entrance behaviour.
    tester.takeException();

    await tester.pump(const Duration(milliseconds: 1100));
    tester.takeException();

    final opacities = tester.widgetList<AnimatedOpacity>(
      find.byType(AnimatedOpacity),
    );
    expect(opacities.last.opacity, 1);
  });
}
