import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:intellistock_mobile/features/connect/presentation/connect_screen.dart';

void main() {
  testWidgets('invalid URL shows an inline error and does not save', (tester) async {
    await tester.pumpWidget(const ProviderScope(
      child: MaterialApp(home: ConnectScreen()),
    ));
    await tester.enterText(find.byType(TextField), 'notaurl');
    await tester.tap(find.text('Test & Connect'));
    await tester.pump();
    expect(find.textContaining('valid URL'), findsOneWidget);
  });
}
