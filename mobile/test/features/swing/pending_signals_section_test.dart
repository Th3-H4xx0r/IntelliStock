import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:intellistock_mobile/core/network/api_error.dart';
import 'package:intellistock_mobile/core/widgets/app_button.dart';
import 'package:intellistock_mobile/features/swing/application/swing_controller.dart';
import 'package:intellistock_mobile/features/swing/data/swing_repository.dart';
import 'package:intellistock_mobile/features/swing/presentation/pending_signals_section.dart';
import 'package:intellistock_mobile/features/swing/presentation/wheel_card.dart';

import 'swing_fakes.dart';

final _now = DateTime.utc(2026, 9, 25, 13, 30);

Widget _app(FakeSwingRepo repo, Widget child) => ProviderScope(
      overrides: [
        swingRepositoryProvider.overrideWithValue(repo),
        swingClockProvider.overrideWithValue(() => _now),
      ],
      child: MaterialApp(
        home: Scaffold(body: SingleChildScrollView(child: child)),
      ),
    );

const _section = PendingSignalsSection(instanceId: 'i1');

Finder _cardButton(String label) => find.widgetWithText(AppButton, label);

Finder _dialogButton(String label) => find.descendant(
    of: find.byType(Dialog), matching: find.widgetWithText(AppButton, label));

void _phone(WidgetTester tester) {
  tester.view.physicalSize = const Size(320 * 3, 2400 * 3);
  tester.view.devicePixelRatio = 3.0;
  addTearDown(tester.view.resetPhysicalSize);
  addTearDown(tester.view.resetDevicePixelRatio);
}

void main() {
  group('PendingSignalsSection', () {
    testWidgets('renders swing and wheel cards; Approve ½ on swing only',
        (tester) async {
      final repo = FakeSwingRepo([swingSignal('a1'), wheelSignal('w1')]);
      await tester.pumpWidget(_app(repo, _section));
      await tester.pumpAndSettle();

      expect(find.text('Pending AI signals (2)'), findsOneWidget);
      expect(find.text('AAPL'), findsOneWidget);
      expect(find.text('APH'), findsOneWidget);
      expect(_cardButton('Approve ½'), findsOneWidget);
      expect(_cardButton('Approve'), findsNWidgets(2));
      expect(find.text('APH261002P00130000'), findsOneWidget);
      expect(find.text('\$1.30 (\$130.00)'), findsOneWidget);
      expect(find.text('\$13,000.00'), findsOneWidget);
      expect(find.text('Risks: earnings in 9 days'), findsOneWidget);
    });

    testWidgets('empty list says so', (tester) async {
      await tester.pumpWidget(_app(FakeSwingRepo([]), _section));
      await tester.pumpAndSettle();
      expect(find.text('Nothing waiting for review.'), findsOneWidget);
    });

    testWidgets('approve goes through the confirm dialog, then the card leaves',
        (tester) async {
      final repo = FakeSwingRepo([swingSignal('a1')]);
      await tester.pumpWidget(_app(repo, _section));
      await tester.pumpAndSettle();

      await tester.tap(_cardButton('Approve'));
      await tester.pumpAndSettle();
      expect(find.byType(Dialog), findsOneWidget);
      expect(repo.decideCalls, isEmpty);

      await tester.tap(_dialogButton('Approve'));
      await tester.pumpAndSettle();
      expect(repo.decideCalls, ['a1:approve']);
      expect(find.text('AAPL'), findsNothing);
      expect(find.textContaining('Approved AAPL'), findsOneWidget);
    });

    testWidgets('FW-api-I1: a 202 shows the server advice, never the success copy',
        (tester) async {
      _phone(tester); // 320pt wide: the long badge and the advice must fit
      final detail = kUncertainApproval;
      final repo = FakeSwingRepo([swingSignal('a1')])
        ..decideReceipt = DecisionReceipt(uncertain: true, detail: detail);
      await tester.pumpWidget(_app(repo, _section));
      await tester.pumpAndSettle();

      await tester.tap(_cardButton('Approve'));
      await tester.pumpAndSettle();
      await tester.tap(_dialogButton('Approve'));
      await tester.pumpAndSettle();
      // Follow-up 2: the advice stays on a waiting card, not a snackbar.
      expect(find.text('Waiting for the broker (1)'), findsOneWidget);
      // AppBadge upper-cases its label.
      expect(find.text('UNCERTAIN — WAITING FOR THE BROKER'), findsOneWidget);
      expect(find.text(waitingCopy), findsOneWidget); // round 3 FU-1 copy
      expect(find.text(detail), findsNothing);
      expect(find.byType(SnackBar), findsNothing);
      expect(_cardButton('Dismiss'), findsNothing); // not before 2 minutes
      expect(tester.takeException(), isNull);
      expect(_cardButton('Approve'), findsNothing); // no longer a pending card
      expect(find.textContaining('Approved AAPL'), findsNothing);

      // It persists across polls until one settles it (seams I-2: a
      // submitted row that carries the order key the broker sent).
      repo
        ..pending = []
        ..submitted = [
          withStatus(swingSignal('a1'), 'submitted', orderClientId: 'instance-1-abc-0')
        ];
      final container = ProviderScope.containerOf(tester.element(find.byType(PendingSignalsSection)));
      await container.read(pendingSignalsProvider('i1').notifier).refresh();
      await tester.pumpAndSettle();
      expect(find.text('SUBMITTED'), findsOneWidget);
      expect(find.text('UNCERTAIN — WAITING FOR THE BROKER'), findsNothing);
      expect(find.text(waitingCopy), findsNothing);
      await tester.tap(_cardButton('Dismiss'));
      await tester.pumpAndSettle();
      expect(find.textContaining('Waiting for the broker'), findsNothing);
    });

    testWidgets('cancel in the dialog sends nothing', (tester) async {
      final repo = FakeSwingRepo([swingSignal('a1')]);
      await tester.pumpWidget(_app(repo, _section));
      await tester.pumpAndSettle();

      await tester.tap(_cardButton('Reject'));
      await tester.pumpAndSettle();
      await tester.tap(_dialogButton('Cancel'));
      await tester.pumpAndSettle();
      expect(repo.decideCalls, isEmpty);
      expect(find.text('AAPL'), findsOneWidget);
    });

    testWidgets('decided on another device (400): card removed, reason shown',
        (tester) async {
      final repo = FakeSwingRepo([swingSignal('a1')])
        ..decideError =
            ApiError('signal a1 is approved, not pending', statusCode: 400);
      await tester.pumpWidget(_app(repo, _section));
      await tester.pumpAndSettle();

      await tester.tap(_cardButton('Approve'));
      await tester.pumpAndSettle();
      await tester.tap(_dialogButton('Approve'));
      await tester.pumpAndSettle();
      expect(find.text('AAPL'), findsNothing);
      expect(find.text('signal a1 is approved, not pending'), findsOneWidget);
    });

    testWidgets('forbidden (403): card stays and can be retried',
        (tester) async {
      final repo = FakeSwingRepo([swingSignal('a1')])
        ..decideError = ApiError('not your instance', statusCode: 403);
      await tester.pumpWidget(_app(repo, _section));
      await tester.pumpAndSettle();

      await tester.tap(_cardButton('Reject'));
      await tester.pumpAndSettle();
      await tester.tap(_dialogButton('Reject'));
      await tester.pumpAndSettle();
      expect(find.text('AAPL'), findsOneWidget);
      expect(find.text('not your instance'), findsOneWidget);
      expect(tester.widget<AppButton>(_cardButton('Reject')).onPressed,
          isNotNull);
    });

    testWidgets('FW item 3: a stuck approval offers Re-send behind a confirm',
        (tester) async {
      _phone(tester); // 320pt wide: the badges and the button must fit
      final repo = FakeSwingRepo([swingSignal('p1', symbol: 'MSFT')], approved: [
        approvedSignal('a1', '2026-09-25T13:25:00Z'),
        approvedSignal('fresh', '2026-09-25T13:29:30Z', symbol: 'NVDA'),
      ]);
      await tester.pumpWidget(_app(repo, _section));
      await tester.pumpAndSettle();

      expect(find.text('Pending AI signals (1)'), findsOneWidget);
      expect(find.text('Approved, not yet sent (1)'), findsOneWidget);
      expect(find.text('Approved 5 min ago; the broker has not picked it up yet.'),
          findsOneWidget);
      expect(find.text('session 2026-09-25'), findsOneWidget); // follow-up 3
      expect(find.text('NVDA'), findsNothing); // approved 30 s ago: in flight
      expect(tester.takeException(), isNull);

      await tester.tap(_cardButton('Re-send'));
      await tester.pumpAndSettle();
      expect(find.byType(Dialog), findsOneWidget);
      expect(repo.resendCalls, isEmpty);
      await tester.tap(_dialogButton('Re-send'));
      await tester.pumpAndSettle();
      expect(repo.resendCalls, ['a1']);
      expect(find.text('Re-sent AAPL to the broker.'), findsOneWidget);
      expect(find.text('Approved, not yet sent (1)'), findsNothing);
    });

    testWidgets('follow-up 3: an approval from an older session shows its session, no Re-send',
        (tester) async {
      final repo = FakeSwingRepo([], approved: [
        approvedSignal('old', '2026-09-24T13:25:00Z', session: '2026-09-24'),
      ]);
      await tester.pumpWidget(_app(repo, _section));
      await tester.pumpAndSettle();
      expect(find.text('Approved, not yet sent (1)'), findsOneWidget);
      expect(find.text('session 2026-09-24'), findsOneWidget);
      expect(find.text('This approval was made on 2026-09-24; approve a fresh signal instead.'),
          findsOneWidget);
      expect(_cardButton('Re-send'), findsNothing);
    });

    testWidgets('round 3 FU-1: a stuck card offers Dismiss beside Re-send',
        (tester) async {
      final repo = FakeSwingRepo([],
          approved: [approvedSignal('a1', '2026-09-25T13:25:00Z')]);
      await tester.pumpWidget(_app(repo, _section));
      await tester.pumpAndSettle();
      expect(_cardButton('Re-send'), findsOneWidget);
      await tester.tap(_cardButton('Dismiss'));
      await tester.pumpAndSettle();
      expect(find.textContaining('not yet sent'), findsNothing);
      expect(repo.resendCalls, isEmpty);
    });

    testWidgets('an account with no stuck approvals renders no re-send section',
        (tester) async {
      await tester.pumpWidget(_app(FakeSwingRepo([swingSignal('a1')]), _section));
      await tester.pumpAndSettle();
      expect(find.textContaining('not yet sent'), findsNothing);
      expect(_cardButton('Re-send'), findsNothing);
    });

    testWidgets('long reasoning on a 320pt phone: no overflow, collapsible',
        (tester) async {
      _phone(tester);
      final long = List.filled(120, 'momentum').join(' ') + 'x' * 600;
      final repo = FakeSwingRepo([swingSignal('a1', reasoning: long)]);
      await tester.pumpWidget(_app(repo, _section));
      await tester.pumpAndSettle();

      expect(tester.takeException(), isNull);
      expect(find.text('Show more'), findsOneWidget);
      await tester.tap(find.text('Show more'));
      await tester.pumpAndSettle();
      expect(tester.takeException(), isNull);
      expect(find.text('Show less'), findsOneWidget);
    });
  });

  group('WheelCard', () {
    testWidgets('lists open puts; a missing mark shows a dash', (tester) async {
      final repo = FakeSwingRepo([],
          wheelSnapshot: WheelSnapshot.fromJson({
            'open_puts': [
              {
                'contract': 'APH261002P00130000',
                'underlying': 'APH',
                'strike': 130,
                'expiry': '2026-10-02',
                'qty': 1,
                'avg_entry_price': 1.23,
                'current_price': null,
                'itm_pct': 2.0,
                'dte': 8,
                'collateral': 13000,
                'unrealized_pl': null,
              },
            ],
            'collateral_total': 13000,
            'cash': 25000,
            'recent_scans': [],
          }));
      await tester.pumpWidget(_app(repo, const WheelCard(instanceId: 'i1')));
      await tester.pumpAndSettle();

      expect(find.text('APH \$130.00 P · 2026-10-02'), findsOneWidget);
      expect(find.text('2.0% ITM'), findsOneWidget);
      expect(find.text('\$13,000.00'), findsOneWidget);
      expect(find.text('No scans recorded yet.'), findsOneWidget);
      // MARK and P&L both have no value; neither may render as $0.00.
      expect(find.text('\$0.00'), findsNothing);
      expect(find.text('—'), findsNWidgets(2));
    });

    testWidgets('an API build without the route (404) says so', (tester) async {
      final repo = FakeSwingRepo([])
        ..wheelError = ApiError('Not Found', statusCode: 404);
      await tester.pumpWidget(_app(repo, const WheelCard(instanceId: 'i1')));
      await tester.pumpAndSettle();
      expect(find.text('This API build has no wheel endpoint yet.'),
          findsOneWidget);
    });

    testWidgets('FW item 4: any other 404 shows its detail, and Retry still works',
        (tester) async {
      final repo = FakeSwingRepo([])
        ..wheelError = ApiError('Instance not found: i1', statusCode: 404);
      await tester.pumpWidget(_app(repo, const WheelCard(instanceId: 'i1')));
      await tester.pumpAndSettle();
      expect(find.text('Instance not found: i1'), findsOneWidget);
      expect(find.text('This API build has no wheel endpoint yet.'), findsNothing);
      repo.wheelError = null;
      await tester.tap(find.text('Retry'));
      await tester.pumpAndSettle();
      expect(find.text('No open puts.'), findsOneWidget);
    });

    testWidgets('FW item 4: the card stamps when the book was fetched',
        (tester) async {
      final repo = FakeSwingRepo([],
          wheelSnapshot: WheelSnapshot.fromJson(const {'open_puts': []},
              fetchedAt: DateTime(2026, 9, 25, 14, 2, 41)));
      await tester.pumpWidget(_app(repo, const WheelCard(instanceId: 'i1')));
      await tester.pumpAndSettle();
      expect(find.text('as of 14:02'), findsOneWidget);
    });

    testWidgets('an outage (503) is an error, never an empty book; Retry works',
        (tester) async {
      final repo = FakeSwingRepo([])
        ..wheelError = ApiError('broker unavailable', statusCode: 503);
      await tester.pumpWidget(_app(repo, const WheelCard(instanceId: 'i1')));
      await tester.pumpAndSettle();
      expect(find.text('broker unavailable'), findsOneWidget);
      expect(find.text('OPEN PUTS'), findsNothing);
      expect(find.text('No open puts.'), findsNothing);

      repo.wheelError = null;
      await tester.tap(find.text('Retry'));
      await tester.pumpAndSettle();
      expect(find.text('broker unavailable'), findsNothing);
      expect(find.text('No open puts.'), findsOneWidget);
    });
  });

  test('fmtItm', () {
    expect(fmtItm(2.0), '2.0% ITM');
    expect(fmtItm(-3.0), '3.0% OTM');
    expect(fmtItm(null), '—');
  });
}
