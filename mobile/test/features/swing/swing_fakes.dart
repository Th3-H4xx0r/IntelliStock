import 'dart:async';

import 'package:intellistock_mobile/features/swing/data/swing_repository.dart';

/// Test double for [SwingRepository]. Not a *_test.dart file, so the runner
/// does not execute it on its own.
class FakeSwingRepo implements SwingRepository {
  FakeSwingRepo(this.pending, {this.wheelSnapshot = WheelSnapshot.empty});

  List<SwingSignal> pending;
  WheelSnapshot wheelSnapshot;
  final decideCalls = <String>[];
  Object? decideError;
  Object? listError;
  Object? wheelError;

  /// When set, decide() waits on it: lets a test hold a request in flight.
  Completer<void>? gate;

  @override
  Future<List<SwingSignal>> pendingSignals(String instanceId) async {
    if (listError != null) throw listError!;
    return List.of(pending);
  }

  @override
  Future<void> decide(String instanceId, String signalId, String decision,
      {String? reason}) async {
    decideCalls.add('$signalId:$decision');
    if (gate != null) await gate!.future;
    if (decideError != null) throw decideError!;
  }

  @override
  Future<WheelSnapshot> wheel(String instanceId) async {
    if (wheelError != null) throw wheelError!;
    return wheelSnapshot;
  }
}

SwingSignal swingSignal(
  String id, {
  String symbol = 'AAPL',
  String createdAt = '2026-09-24T13:15:00Z',
  String reasoning = 'Pullback to the 50-day in an uptrend.',
}) =>
    SwingSignal.fromJson({
      'id': id,
      'lane': 'swing',
      'symbol': symbol,
      'session': '2026-09-24',
      'created_at': createdAt,
      'score': 62,
      'recommendation': 'REVIEW',
      'reasoning': reasoning,
      'key_risks': ['earnings in 9 days'],
      'proposal': {'entry': 200.0, 'stop': 188.0, 'target': 218.0, 'shares': 6},
      'status': 'pending',
    });

SwingSignal wheelSignal(String id) => SwingSignal.fromJson({
      'id': id,
      'lane': 'wheel',
      'symbol': 'APH',
      'session': '2026-09-21',
      'created_at': '2026-09-21T14:30:00Z',
      'score': 55,
      'recommendation': 'REVIEW',
      'reasoning': 'IV rank is high.',
      'key_risks': <String>[],
      'proposal': {
        'contract': 'APH261002P00130000',
        'strike': 130,
        'expiry': '2026-10-02',
        'qty': 1,
        'limit_price': 1.23,
        'premium_est': 1.3,
      },
      'status': 'pending',
    });
