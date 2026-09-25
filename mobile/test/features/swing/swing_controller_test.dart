import 'dart:async';

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

  test('copy names the symbol and the decision', () {
    final s = signal('a1');
    expect(decisionLabel('approve_half'), 'Approve ½');
    expect(decisionConfirmBody(s, 'reject'), contains('final'));
    expect(decisionSuccessMessage(s, 'approve'), startsWith('Approved AAPL'));
  });
}
