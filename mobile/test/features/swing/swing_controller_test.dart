import 'dart:async';

import 'package:fake_async/fake_async.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:intellistock_mobile/core/network/api_error.dart';
import 'package:intellistock_mobile/features/swing/application/swing_controller.dart';
import 'package:intellistock_mobile/features/swing/data/swing_repository.dart';

import 'swing_fakes.dart';

SwingSignal signal(String id) =>
    swingSignal(id, createdAt: '2026-09-24T13:15:0${id.length}Z');

/// The notifier's clock; tests move it.
var clock = DateTime.utc(2026, 9, 25, 13, 30);

Future<ProviderContainer> start(FakeSwingRepo repo) async {
  final container = ProviderContainer(
    overrides: [
      swingRepositoryProvider.overrideWithValue(repo),
      swingClockProvider.overrideWithValue(() => clock),
    ],
  );
  addTearDown(container.dispose);
  container.listen(pendingSignalsProvider('i1'), (_, _) {});
  await container.read(pendingSignalsProvider('i1').future);
  return container;
}

PendingSignalsState read(ProviderContainer c) =>
    c.read(pendingSignalsProvider('i1')).requireValue;

/// Holds every pendingSignals() call on [listGate] and counts the calls.
/// [nextListGate], when set, holds only the next poll: its pending list
/// answers with the rows as they were when it began, and its approved list
/// with the rows as they are when the gate opens (the two are separate
/// requests, served at different moments).
class _GatedListRepo extends FakeSwingRepo {
  _GatedListRepo(super.pending);

  final listGate = Completer<void>();
  Completer<void>? nextListGate;
  Completer<void>? _approvedHold;
  int listCalls = 0;

  @override
  Future<List<SwingSignal>> pendingSignals(String instanceId) async {
    listCalls++;
    final snapshot = await super.pendingSignals(instanceId);
    final hold = nextListGate;
    nextListGate = null;
    _approvedHold = hold;
    await listGate.future;
    if (hold != null) await hold.future;
    return snapshot;
  }

  @override
  Future<List<SwingSignal>> approvedSignals(String instanceId) async {
    await Future<void>.delayed(Duration.zero); // after pendingSignals began
    final hold = _approvedHold;
    _approvedHold = null;
    if (hold != null) await hold.future;
    return super.approvedSignals(instanceId);
  }
}

/// Fails the next pending read only (follow-up 4).
class _PendingDown {
  _PendingDown(this.repo);
  final FakeSwingRepo repo;

  Future<void> refreshWith(ProviderContainer c) async {
    final keep = repo.pending;
    repo.pendingError = ApiError('pending down');
    await c.read(pendingSignalsProvider('i1').notifier).refresh();
    repo
      ..pendingError = null
      ..pending = keep;
  }
}

void main() {
  TestWidgetsFlutterBinding.ensureInitialized();

  group('swingLanesOf', () {
    test('finds lanes by id or class name, ignores everything else', () {
      expect(swingLanesOf({'strategies': [{'strategy': 'strategy_swing'}]}).swing, isTrue);
      final l = swingLanesOf({
        'strategies': [
          {'strategy': 'StrategyWheel'},
          {'strategy': 'strategy_eb'},
          'junk',
        ],
      });
      expect([l.swing, l.wheel, l.any], [false, true, true]);
      expect(swingLanesOf({'strategies': [{'strategy': 'strategy_eb'}]}).any, isFalse);
      expect(swingLanesOf(null).any, isFalse);
      expect(swingLanesOf({'strategies': 'oops'}).any, isFalse);
    });
  });

  group('PendingSignalsNotifier.decide', () {
    test('a double tap sends one request', () async {
      final repo = FakeSwingRepo([signal('a1')])..gate = Completer<void>();
      final c = await start(repo);
      final notifier = c.read(pendingSignalsProvider('i1').notifier);

      final first = notifier.decide(signal('a1'), 'approve');
      expect(read(c).isDeciding('a1'), isTrue);
      final second = await notifier.decide(signal('a1'), 'approve');
      expect(second.outcome, DecisionOutcome.ignored);

      repo.gate!.complete();
      expect((await first).outcome, DecisionOutcome.recorded);
      expect(repo.decideCalls, ['a1:approve']);
      expect(read(c).signals, isEmpty);
      expect(read(c).deciding, isEmpty);
    });

    test('decided elsewhere (400): card removed, server reason shown', () async {
      final repo = FakeSwingRepo([signal('a1'), signal('b22')])
        ..decideError = ApiError('signal a1 is approved, not pending', statusCode: 400);
      final c = await start(repo);
      final result = await c
          .read(pendingSignalsProvider('i1').notifier)
          .decide(signal('a1'), 'approve');
      expect(result.outcome, DecisionOutcome.noLongerPending);
      expect(result.message, 'signal a1 is approved, not pending');
      expect(read(c).signals.map((s) => s.id), ['b22']);
      expect(read(c).deciding, isEmpty);
    });

    test('forbidden (403): card stays, buttons re-enable, reason shown', () async {
      final repo = FakeSwingRepo([signal('a1')])
        ..decideError = ApiError('not your instance', statusCode: 403);
      final c = await start(repo);
      final result = await c
          .read(pendingSignalsProvider('i1').notifier)
          .decide(signal('a1'), 'reject');
      expect(result.outcome, DecisionOutcome.failed);
      expect(result.message, 'not your instance');
      expect(read(c).signals.map((s) => s.id), ['a1']);
      expect(read(c).isDeciding('a1'), isFalse);
    });

    test('broker not running (503): card stays, buttons re-enable, retry allowed', () async {
      final repo = FakeSwingRepo([signal('a1')])
        ..decideError = ApiError('instance i1 is not running', statusCode: 503);
      final c = await start(repo);
      final notifier = c.read(pendingSignalsProvider('i1').notifier);
      final result = await notifier.decide(signal('a1'), 'approve');
      expect(result.outcome, DecisionOutcome.failed);
      expect(result.message, 'instance i1 is not running');
      expect(read(c).signals.map((s) => s.id), ['a1']);
      expect(read(c).isDeciding('a1'), isFalse);

      repo.decideError = null;
      final retry = await notifier.decide(signal('a1'), 'approve');
      expect(retry.outcome, DecisionOutcome.recorded);
      expect(repo.decideCalls, ['a1:approve', 'a1:approve']);
      expect(read(c).signals, isEmpty);
    });

    test('FW-api-I1: a 202 (uncertain) drops the card and says the order may be in flight',
        () async {
      final detail = kUncertainApproval;
      final repo = FakeSwingRepo([signal('a1'), signal('b22')])
        ..decideReceipt = DecisionReceipt(uncertain: true, detail: detail);
      final c = await start(repo);
      final notifier = c.read(pendingSignalsProvider('i1').notifier);
      final result = await notifier.decide(signal('a1'), 'approve');
      expect(result.outcome, DecisionOutcome.uncertain);
      expect(result.message, detail);
      expect(read(c).signals.map((s) => s.id), ['b22']);
      expect(read(c).deciding, isEmpty);

      repo.decideReceipt = const DecisionReceipt(uncertain: true);
      final bare = await notifier.decide(signal('b22'), 'approve');
      expect(bare.outcome, DecisionOutcome.uncertain);
      // Follow-up 1: no detail falls back to the same ruled text.
      expect(bare.message,
          'Approval received, but its delivery to the broker could not be '
          'confirmed. Do NOT place this order by hand — it may still be '
          'queued. The card will show submitted or failed shortly.');
      for (final m in [result.message, bare.message]) {
        expect(m, isNot(contains('place the order manually')));
        expect(m, isNot(contains('no broker command was queued')));
      }
    });

    test('FW-api-I1: a 503 without detail says not queued, try again', () async {
      final repo = FakeSwingRepo([signal('a1')])
        ..decideError = ApiError('', statusCode: 503);
      final c = await start(repo);
      final result = await c
          .read(pendingSignalsProvider('i1').notifier)
          .decide(signal('a1'), 'approve');
      expect(result.outcome, DecisionOutcome.failed);
      expect(result.message, 'Not queued — the signal is still pending; try again.');
      expect(read(c).signals.map((s) => s.id), ['a1']);
    });

    test('expired session (401): card stays, message says so', () async {
      final repo = FakeSwingRepo([signal('a1')])
        ..decideError = ApiError('Not authenticated', statusCode: 401);
      final c = await start(repo);
      final result = await c
          .read(pendingSignalsProvider('i1').notifier)
          .decide(signal('a1'), 'approve');
      expect(result.outcome, DecisionOutcome.failed);
      expect(result.message, 'Session expired — please sign in again.');
      expect(read(c).signals, hasLength(1));
    });

    test('a poll that raced a recorded decision does not resurrect the card', () async {
      final repo = _GatedListRepo([signal('a1')])..listGate.complete();
      final c = await start(repo);
      final notifier = c.read(pendingSignalsProvider('i1').notifier);
      // The poll starts, and its answer predates the decision...
      final hold = Completer<void>();
      repo.nextListGate = hold;
      final racing = notifier.refresh();
      await notifier.decide(signal('a1'), 'approve'); // ...the 2xx arrives...
      hold.complete(); // ...and the poll lands still listing a1 as pending.
      await racing;
      expect(read(c).signals, isEmpty);
    });

    test('FW-api-I2: a poll begun after the 2xx governs, so a broker reset shows again',
        () async {
      final repo = FakeSwingRepo([signal('a1')]);
      final c = await start(repo);
      final notifier = c.read(pendingSignalsProvider('i1').notifier);
      await notifier.decide(signal('a1'), 'approve');
      expect(read(c).signals, isEmpty);
      // No quote before the open: the broker put a1 back to pending and
      // pushed "approve again after the open".
      await notifier.refresh();
      expect(read(c).signals.map((s) => s.id), ['a1']);
      // And it can be approved again from this screen.
      final again = await notifier.decide(signal('a1'), 'approve');
      expect(again.outcome, DecisionOutcome.recorded);
      expect(repo.decideCalls, ['a1:approve', 'a1:approve']);
    });

    test('FW-api-I2: pull-to-refresh (invalidate) clears the local hide', () async {
      final repo = _GatedListRepo([signal('a1')])..listGate.complete();
      final c = await start(repo);
      final notifier = c.read(pendingSignalsProvider('i1').notifier);
      final hold = Completer<void>();
      repo.nextListGate = hold;
      final racing = notifier.refresh();
      await notifier.decide(signal('a1'), 'approve');
      hold.complete();
      await racing;
      expect(read(c).signals, isEmpty); // hidden from the racing poll

      c.invalidate(pendingSignalsProvider('i1'));
      await c.read(pendingSignalsProvider('i1').future);
      expect(read(c).signals.map((s) => s.id), ['a1']);
    });

    test('FW-api-I2: an older poll that lands after a newer one is ignored', () async {
      final repo = _GatedListRepo([signal('a1')])..listGate.complete();
      final c = await start(repo);
      final notifier = c.read(pendingSignalsProvider('i1').notifier);
      final hold = Completer<void>();
      repo.nextListGate = hold;
      final older = notifier.refresh(); // answers a1 pending, but late
      repo.pending = [];
      await notifier.refresh(); // newer: a1 was decided elsewhere
      expect(read(c).signals, isEmpty);
      repo.pending = [signal('a1')];
      hold.complete();
      await older;
      expect(read(c).signals, isEmpty);
    });

    test('a failed poll keeps the last good list and reports the error', () async {
      final repo = FakeSwingRepo([signal('a1')]);
      final c = await start(repo);
      repo.listError = ApiError('Cannot reach the server.');
      await c.read(pendingSignalsProvider('i1').notifier).refresh();
      expect(read(c).signals.map((s) => s.id), ['a1']);
      expect(read(c).refreshError, 'Cannot reach the server.');
    });
  });

  group('follow-up 4: the list reads settle independently', () {
    setUp(() => clock = DateTime.utc(2026, 9, 25, 13, 30));

    test('a failed approved read never stops the pending list refreshing', () async {
      final repo = FakeSwingRepo([signal('a1')],
          approved: [approvedSignal('old', '2026-09-25T13:20:00Z')]);
      final c = await start(repo);
      expect(read(c).stuck.map((s) => s.id), ['old']);
      repo
        ..pending = [signal('a1'), signal('b22')]
        ..approvedError = ApiError('Could not load signals (502)');
      await c.read(pendingSignalsProvider('i1').notifier).refresh();
      expect(read(c).signals.map((s) => s.id), ['a1', 'b22']); // refreshed
      expect(read(c).stuck.map((s) => s.id), ['old']); // last good
      expect(read(c).refreshError, 'Could not load signals (502)');
    });

    test('an approved read that fails on the first load still shows the pending list',
        () async {
      final repo = FakeSwingRepo([signal('a1')])
        ..approvedError = ApiError('Could not load signals (502)');
      final c = await start(repo);
      expect(read(c).signals.map((s) => s.id), ['a1']);
      expect(read(c).refreshError, 'Could not load signals (502)');
    });

    test('a failed pending read keeps the last good list; the approved lists refresh',
        () async {
      final repo = FakeSwingRepo([signal('a1')]);
      final c = await start(repo);
      repo.approved = [approvedSignal('old', '2026-09-25T13:20:00Z')];
      final gated = _PendingDown(repo);
      await gated.refreshWith(c);
      expect(read(c).signals.map((s) => s.id), ['a1']);
      expect(read(c).stuck.map((s) => s.id), ['old']);
      expect(read(c).refreshError, 'pending down');
    });
  });

  group('follow-up 3: re-send is for today\'s New York session only', () {
    test('nyDate is the New York calendar date, across the DST switches', () {
      expect(nyDate(DateTime.utc(2026, 9, 25, 1, 30)), '2026-09-24'); // 21:30 EDT
      expect(nyDate(DateTime.utc(2026, 9, 25, 4, 0)), '2026-09-25'); // 00:00 EDT
      expect(nyDate(DateTime.utc(2026, 3, 8, 4, 59)), '2026-03-07'); // 23:59 EST
      expect(nyDate(DateTime.utc(2026, 3, 8, 7, 0)), '2026-03-08'); // 03:00 EDT
      expect(nyDate(DateTime.utc(2026, 11, 1, 4, 30)), '2026-11-01'); // 00:30 EDT
      expect(nyDate(DateTime.utc(2026, 11, 2, 4, 30)), '2026-11-01'); // 23:30 EST
      expect(nyDate(DateTime.utc(2026, 1, 1, 4, 59)), '2025-12-31'); // 23:59 EST
    });

    test('resendBlockedReason names the stale session', () {
      expect(resendBlockedReason(approvedSignal('a', '2026-09-25T13:00:00Z'), '2026-09-25'),
          isNull);
      expect(
          resendBlockedReason(
              approvedSignal('a', '2026-09-24T13:00:00Z', session: '2026-09-24'),
              '2026-09-25'),
          'This approval is from 2026-09-24; approve a fresh signal instead.');
    });
  });

  group('FW item 3: stuck approvals', () {
    setUp(() => clock = DateTime.utc(2026, 9, 25, 13, 30));

    test('an approval unclaimed for more than 2 minutes is offered a re-send', () async {
      final repo = FakeSwingRepo([], approved: [
        approvedSignal('old', '2026-09-25T13:27:59Z'),
        approvedSignal('edge', '2026-09-25T13:28:00Z'),
        approvedSignal('new', '2026-09-25T13:29:30Z', status: 'approved_half'),
      ]);
      final c = await start(repo);
      expect(read(c).stuck.map((s) => s.id), ['old']);
      clock = clock.add(const Duration(seconds: 31));
      await c.read(pendingSignalsProvider('i1').notifier).refresh();
      expect(read(c).stuck.map((s) => s.id), ['old', 'edge']);
      expect(read(c).signals, isEmpty);
    });

    test('a re-send queues once, then waits another 2 minutes before offering again',
        () async {
      final repo = FakeSwingRepo([],
          approved: [approvedSignal('a1', '2026-09-25T13:20:00Z')]);
      final c = await start(repo);
      final notifier = c.read(pendingSignalsProvider('i1').notifier);
      final result = await notifier.resend(read(c).stuck.single);
      expect(result.outcome, DecisionOutcome.recorded);
      expect(result.message, 'Re-sent AAPL to the broker.');
      expect(repo.resendCalls, ['a1']);
      expect(read(c).stuck, isEmpty);

      await notifier.refresh(); // still approved: the broker has not run it yet
      expect(read(c).stuck, isEmpty);
      clock = clock.add(const Duration(minutes: 2, seconds: 1));
      await notifier.refresh();
      expect(read(c).stuck.map((s) => s.id), ['a1']);
    });

    test('a double tap on Re-send sends one request', () async {
      final repo = FakeSwingRepo([],
          approved: [approvedSignal('a1', '2026-09-25T13:20:00Z')])
        ..gate = Completer<void>();
      final c = await start(repo);
      final notifier = c.read(pendingSignalsProvider('i1').notifier);
      final s = read(c).stuck.single;
      final first = notifier.resend(s);
      expect(read(c).isResending('a1'), isTrue);
      expect((await notifier.resend(s)).outcome, DecisionOutcome.ignored);
      repo.gate!.complete();
      await first;
      expect(repo.resendCalls, ['a1']);
    });

    test('409 (a command is still queued) drops the card and snoozes it', () async {
      final repo = FakeSwingRepo([],
          approved: [approvedSignal('a1', '2026-09-25T13:20:00Z')])
        ..resendError = ApiError('command c1 for signal a1 is still pending; ...',
            statusCode: 409);
      final c = await start(repo);
      final notifier = c.read(pendingSignalsProvider('i1').notifier);
      final result = await notifier.resend(read(c).stuck.single);
      expect(result.outcome, DecisionOutcome.noLongerPending);
      expect(result.message, contains('still pending'));
      await notifier.refresh();
      expect(read(c).stuck, isEmpty);
    });

    test('503 keeps the card and says not queued', () async {
      final repo = FakeSwingRepo([],
          approved: [approvedSignal('a1', '2026-09-25T13:20:00Z')])
        ..resendError = ApiError('', statusCode: 503);
      final c = await start(repo);
      final result = await c
          .read(pendingSignalsProvider('i1').notifier)
          .resend(read(c).stuck.single);
      expect(result.outcome, DecisionOutcome.failed);
      expect(result.message, 'Not queued — try again.');
      expect(read(c).stuck.map((s) => s.id), ['a1']);
      expect(read(c).isResending('a1'), isFalse);
    });

    test('a 202 re-send is uncertain and carries the server advice', () async {
      final repo = FakeSwingRepo([],
          approved: [approvedSignal('a1', '2026-09-25T13:20:00Z')])
        ..resendReceipt = const DecisionReceipt(uncertain: true);
      final c = await start(repo);
      final result = await c
          .read(pendingSignalsProvider('i1').notifier)
          .resend(read(c).stuck.single);
      expect(result.outcome, DecisionOutcome.uncertain);
      expect(result.message,
          'Re-send received, but its delivery to the broker could not be '
          'confirmed. Do NOT place this order by hand — it may still be '
          'queued. The card will show submitted or failed shortly.');
      expect(read(c).stuck, isEmpty);
    });

    test('FW-api-I2: a racing poll that also sees the id approved never shows it pending',
        () async {
      final repo = _GatedListRepo([signal('a1')])..listGate.complete();
      final c = await start(repo);
      final notifier = c.read(pendingSignalsProvider('i1').notifier);
      final hold = Completer<void>();
      repo.nextListGate = hold;
      final racing = notifier.refresh(); // its pending list predates the decision
      await notifier.decide(signal('a1'), 'approve');
      repo.pending = [];
      repo.approved = [approvedSignal('a1', '2026-09-25T13:29:59Z')];
      hold.complete(); // its approved list is served after it
      await racing;
      expect(read(c).signals, isEmpty);
      expect(read(c).stuck, isEmpty); // approved one second ago: not stuck
      // The broker then put it back to pending: the next poll shows it.
      repo.pending = [signal('a1')];
      repo.approved = [];
      await notifier.refresh();
      expect(read(c).signals.map((s) => s.id), ['a1']);
    });
  });

  test('disposed during the first fetch: no poller outlives the provider', () {
    fakeAsync((async) {
      final repo = _GatedListRepo([signal('a1')]);
      final c = ProviderContainer(
        overrides: [swingRepositoryProvider.overrideWithValue(repo)],
      );
      final sub = c.listen(pendingSignalsProvider('i1'), (_, _) {});
      async.flushMicrotasks();
      expect(repo.listCalls, 1);

      // The screen is left while the first GET is still in flight.
      sub.close();
      async.elapse(Duration.zero); // Riverpod's scheduled autoDispose runs
      expect(c.exists(pendingSignalsProvider('i1')), isFalse);

      repo.listGate.complete();
      async.flushMicrotasks();
      async.elapse(PendingSignalsNotifier.pollEvery * 3);

      expect(repo.listCalls, 1);
      expect(async.pendingTimers, isEmpty);
      c.dispose();
    });
  });

  test('copy names the symbol and the decision', () {
    final s = signal('a1');
    expect(decisionLabel('approve_half'), 'Approve ½');
    expect(decisionConfirmBody(s, 'reject'), contains('final'));
    expect(decisionSuccessMessage(s, 'approve'), startsWith('Approved AAPL'));
  });

  test('approval copy says the broker rebuilds and checks, never that it placed',
      () {
    final s = signal('a1');
    // Follow-up 5: an approval is not final; a transient refusal returns it here.
    expect(
      decisionConfirmBody(s, 'approve'),
      'Approve AAPL? The broker rebuilds the order at the live price and '
      'checks it before sending. Approving sends the order. If the broker '
      "can't place it yet (e.g. before the open) the signal returns here to "
      'approve again.',
    );
    expect(
      decisionConfirmBody(s, 'approve_half'),
      'Approve AAPL at half size? The broker rebuilds the order at the live '
      'price and checks it before sending. Approving sends the order. If the '
      "broker can't place it yet (e.g. before the open) the signal returns "
      'here to approve again.',
    );
    expect(decisionConfirmBody(s, 'approve'), isNot(contains('final')));
    // FW item 4 (M-1): some refusals send no notification, so none is promised.
    expect(
      decisionSuccessMessage(s, 'approve'),
      'Approved AAPL. The broker rebuilds and checks the order at the live '
      'price before sending it.',
    );
    expect(
      decisionSuccessMessage(s, 'approve_half'),
      'Approved AAPL at half size. The broker rebuilds and checks the order at '
      'the live price before sending it.',
    );
    expect(decisionConfirmBody(s, 'reject'), 'Reject AAPL? Decisions are final.');
    expect(decisionSuccessMessage(s, 'reject'), 'Rejected AAPL.');
    final promise = RegExp('placed|goes out|within seconds|command poll|notif');
    for (final d in ['approve', 'approve_half']) {
      expect(decisionConfirmBody(s, d), isNot(matches(promise)));
      expect(decisionSuccessMessage(s, d), isNot(matches(promise)));
    }
  });
}
