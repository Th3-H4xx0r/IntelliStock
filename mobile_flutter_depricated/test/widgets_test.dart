import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:intellistock_mobile/core/theme/app_colors.dart';
import 'package:intellistock_mobile/core/widgets/status_pill.dart';
import 'package:intellistock_mobile/core/widgets/typed_confirm_field.dart';

Widget _wrap(Widget child) =>
    MaterialApp(home: Scaffold(body: Center(child: child)));

void main() {
  testWidgets('StatusPill renders its label', (tester) async {
    await tester.pumpWidget(
        _wrap(const StatusPill(label: 'Running', color: AppColors.success)));
    expect(find.text('Running'), findsOneWidget);
  });

  testWidgets('TypedConfirmField fires match only on exact phrase',
      (tester) async {
    final matches = <bool>[];
    await tester.pumpWidget(_wrap(
      TypedConfirmField(phrase: 'HALT', onMatchChanged: matches.add),
    ));

    await tester.enterText(find.byType(TextField), 'HAL');
    await tester.pump();
    expect(matches, isEmpty); // never matched yet

    await tester.enterText(find.byType(TextField), 'HALT');
    await tester.pump();
    expect(matches.last, isTrue);

    await tester.enterText(find.byType(TextField), 'HALTx');
    await tester.pump();
    expect(matches.last, isFalse);
  });

  test('StatusPill.colorForStatus maps known statuses', () {
    expect(StatusPill.colorForStatus('running'), AppColors.success);
    expect(StatusPill.colorForStatus('failed'), AppColors.danger);
    expect(StatusPill.colorForStatus('queued'), AppColors.warning);
    expect(StatusPill.colorForStatus('whatever'), AppColors.textDim);
  });
}
