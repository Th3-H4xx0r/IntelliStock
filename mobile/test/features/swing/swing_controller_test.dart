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

Future<ProviderContainer> start(FakeSwingRepo repo) async {
  final container = ProviderContainer(
    overrides: [swingRepositoryProvider.overrideWithValue(repo)],
  );
  addTearDown(container.dispose);
  container.listen(pendingSignalsProvider('i1'), (_, _) {});
  await container.read(pendingSignalsProvider('i1').future);
  return container;
}

PendingSignalsState read(ProviderContainer c) =>
    c.read(pendingSignalsProvider('i1')).requireValue;

/// Holds every pendingSignals() call on [listGate] and counts the calls.
class _GatedListRepo extends FakeSwingRepo {
  _GatedListRepo(super.pending);

  final listGate = Completer<void>();
  int listCalls = 0;

  @override
  Future<List<SwingSignal>> pendingSignals(String instanceId) async {
    listCalls++;
    await listGate.future;
    return super.pendingSignals(instanceId);
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
      final repo = FakeSwingRepo([signal('a1')]);
      final c = await start(repo);
      final notifier = c.read(pendingSignalsProvider('i1').notifier);
      await notifier.decide(signal('a1'), 'approve');
      await notifier.refresh(); // the server still lists a1 (stale read)
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
    expect(
      decisionConfirmBody(s, 'approve'),
      'Approve AAPL? The broker rebuilds the order at the live price and '
      'checks it before sending. Decisions are final.',
    );
    expect(
      decisionConfirmBody(s, 'approve_half'),
      'Approve AAPL at half size? The broker rebuilds the order at the live '
      'price and checks it before sending. Decisions are final.',
    );
    expect(
      decisionSuccessMessage(s, 'approve'),
      'Approved AAPL. The broker rebuilds and checks the order at the live '
      "price; if it refuses, you'll get a notification.",
    );
    expect(
      decisionSuccessMessage(s, 'approve_half'),
      'Approved AAPL at half size. The broker rebuilds and checks the order at '
      "the live price; if it refuses, you'll get a notification.",
    );
    expect(decisionConfirmBody(s, 'reject'), 'Reject AAPL? Decisions are final.');
    expect(decisionSuccessMessage(s, 'reject'), 'Rejected AAPL.');
    final promise = RegExp('placed|goes out|within seconds|command poll');
    for (final d in ['approve', 'approve_half']) {
      expect(decisionConfirmBody(s, d), isNot(matches(promise)));
      expect(decisionSuccessMessage(s, d), isNot(matches(promise)));
    }
  });
}
