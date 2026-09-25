import 'package:flutter_test/flutter_test.dart';
import 'package:intellistock_mobile/core/network/api_client.dart';
import 'package:intellistock_mobile/features/swing/data/swing_repository.dart';

class _FakeApiClient implements ApiClient {
  final calls = <Map<String, dynamic>>[];
  Object? getResponse;
  Object? postResponse;

  /// When set, GET answers by the `status` query instead of [getResponse].
  Map<String, Object?>? byStatus;

  @override
  Future<T> get<T>(String path, {Map<String, dynamic>? query}) async {
    calls.add({'method': 'GET', 'path': path, 'query': query});
    final table = byStatus;
    if (table != null) return table[query?['status']] as T;
    return getResponse as T;
  }

  @override
  Future<T> post<T>(String path, {Object? body, Map<String, dynamic>? query}) async {
    calls.add({'method': 'POST', 'path': path, 'body': body});
    return postResponse as T;
  }

  @override
  Future<T> put<T>(String path, {Object? body}) async => null as T;
  @override
  Future<T> patch<T>(String path, {Object? body}) async => null as T;
  @override
  Future<T> delete<T>(String path, {Map<String, dynamic>? query}) async =>
      null as T;
}

Map<String, dynamic> swingJson({String id = 'a1', String status = 'pending', String createdAt = '2026-09-24T13:15:02Z'}) => {
      'id': id,
      'instance_id': 'swing-paper',
      'lane': 'swing',
      'symbol': 'AAPL',
      'session': '2026-09-24',
      'created_at': createdAt,
      'score': 62,
      'recommendation': 'REVIEW',
      'reasoning': 'Pullback to the 50-day in an uptrend.',
      'key_risks': ['earnings in 9 days', '', '  sector rotation  '],
      'size_adjustment': 1.0,
      'proposal': {'entry': 200.0, 'stop': 188.0, 'target': 218.0, 'shares': 6},
      'status': status,
    };

Map<String, dynamic> wheelJson() => {
      'id': 'w1',
      'lane': 'wheel',
      'symbol': 'APH',
      'session': '2026-09-21',
      'created_at': '2026-09-21T14:30:00Z',
      'score': 55,
      'recommendation': 'REVIEW',
      'reasoning': 'IV rank is high.',
      'key_risks': <String>[],
      'size_adjustment': 1.0,
      'proposal': {
        'contract': 'APH261002P00130000',
        'strike': 130,
        'expiry': '2026-10-02',
        'qty': 1,
        'limit_price': 1.23,
        'premium_est': 1.3,
        'delta': -0.24,
      },
      'status': 'pending',
    };

void main() {
  group('SwingSignal.fromJson', () {
    test('swing proposal fields and risks', () {
      final s = SwingSignal.fromJson(swingJson());
      expect(s.isWheel, isFalse);
      expect(s.allowsHalf, isTrue);
      expect(s.entry, 200.0);
      expect(s.stop, 188.0);
      expect(s.target, 218.0);
      expect(s.shares, 6);
      expect(s.keyRisksText, 'earnings in 9 days · sector rotation');
    });

    test('wheel proposal fields, credit and collateral', () {
      final s = SwingSignal.fromJson(wheelJson());
      expect(s.isWheel, isTrue);
      expect(s.allowsHalf, isFalse);
      expect(s.contract, 'APH261002P00130000');
      expect(s.strike, 130.0);
      expect(s.qty, 1);
      expect(s.limitPrice, 1.23);
      expect(s.creditEst, closeTo(130.0, 1e-9));
      expect(s.collateral, 13000.0);
    });

    test('missing fields do not throw', () {
      final s = SwingSignal.fromJson(const {'id': 'x'});
      expect(s.lane, 'swing');
      expect(s.status, 'pending');
      expect(s.score, isNull);
      expect(s.entry, isNull);
      expect(s.creditEst, isNull);
      expect(s.keyRisksText, '');
    });
  });

  group('SwingRepository', () {
    test('pendingSignals GETs with status=pending, keeps pending only, newest first', () async {
      final api = _FakeApiClient()
        ..getResponse = {
          'signals': [
            swingJson(id: 'old', createdAt: '2026-09-23T13:15:00Z'),
            swingJson(id: 'done', status: 'approved'),
            swingJson(id: 'new', createdAt: '2026-09-24T13:15:00Z'),
            'junk',
          ],
        };
      final rows = await SwingRepository(api).pendingSignals('swing-paper');
      expect(rows.map((s) => s.id), ['new', 'old']);
      expect(api.calls.single['path'], '/instances/swing-paper/swing/signals');
      expect(api.calls.single['query'], {'status': 'pending'});
    });

    test('pendingSignals also accepts a bare list', () async {
      final api = _FakeApiClient()..getResponse = [wheelJson()];
      final rows = await SwingRepository(api).pendingSignals('i1');
      expect(rows.single.id, 'w1');
    });

    test('decide POSTs the decision, and the reason only when given', () async {
      final api = _FakeApiClient();
      final repo = SwingRepository(api);
      await repo.decide('i1', 'a1', 'approve_half');
      await repo.decide('i1', 'a1', 'reject', reason: '  too close to earnings ');
      await repo.decide('i1', 'a1', 'reject', reason: '   ');
      expect(api.calls[0]['path'], '/instances/i1/swing/signals/a1/decision');
      expect(api.calls[0]['body'], {'decision': 'approve_half'});
      expect(api.calls[1]['body'],
          {'decision': 'reject', 'reason': 'too close to earnings'});
      expect(api.calls[2]['body'], {'decision': 'reject'});
    });

    test('decide reads FW-api-I1\'s uncertain 202 body; any other body is recorded',
        () async {
      final api = _FakeApiClient();
      final repo = SwingRepository(api);
      expect((await repo.decide('i1', 'a1', 'approve')).uncertain, isFalse);
      api.postResponse = {'signal': {}, 'command_id': 'c1'};
      expect((await repo.decide('i1', 'a1', 'approve')).uncertain, isFalse);
      api.postResponse = {
        'signal': {},
        'command_id': null,
        'uncertain': true,
        'detail': '  approval received — the order may be in flight  ',
      };
      final r = await repo.decide('i1', 'a1', 'approve');
      expect(r.uncertain, isTrue);
      expect(r.detail, 'approval received — the order may be in flight');
    });

    test('approvedSignals reads approved and approved_half, approved rows only', () async {
      final api = _FakeApiClient()
        ..byStatus = {
          'approved': {
            'signals': [
              swingJson(id: 'a1', status: 'approved')
                ..['decided_at'] = '2026-09-25T13:20:00+00:00',
              swingJson(id: 'p1'),
            ],
          },
          'approved_half': [
            swingJson(id: 'h1', status: 'approved_half')
              ..['decided_at'] = '2026-09-25T13:25:00+00:00',
          ],
        };
      final rows = await SwingRepository(api).approvedSignals('swing-paper');
      expect(rows.map((s) => s.id), ['h1', 'a1']);
      expect(rows.first.decidedAt, DateTime.utc(2026, 9, 25, 13, 25));
      expect(api.calls.map((c) => c['query']), [
        {'status': 'approved'},
        {'status': 'approved_half'},
      ]);
      expect(SwingSignal.fromJson(const {'id': 'x'}).decidedAt, isNull);
    });

    test('resend POSTs to .../resend with no body and reads the receipt', () async {
      final api = _FakeApiClient()
        ..postResponse = {'signal': {}, 'command_id': 'c1'};
      final repo = SwingRepository(api);
      expect((await repo.resend('i1', 'a1')).uncertain, isFalse);
      expect(api.calls.single['path'], '/instances/i1/swing/signals/a1/resend');
      expect(api.calls.single['body'], isNull);
      api.postResponse = {'uncertain': true, 'detail': 're-send received — x'};
      expect((await repo.resend('i1', 'a1')).detail, 're-send received — x');
    });

    test('wheel parses the addendum shape and tolerates nulls', () async {
      final api = _FakeApiClient()
        ..getResponse = {
          'open_puts': [
            {
              'contract': 'APH261002P00130000',
              'underlying': 'APH',
              'strike': 130,
              'expiry': '2026-10-02',
              'qty': 1,
              'avg_entry_price': 1.23,
              'current_price': null,
              'underlying_price': 127.4,
              'itm_pct': 2.0,
              'dte': 8,
              'collateral': 13000,
              'unrealized_pl': null,
            },
          ],
          'collateral_total': 13000,
          'cash': 25000,
          'recent_scans': [
            {'id': 's1', 'session': '2026-09-21', 'symbol': 'APH', 'strike': 130,
             'expiry': '2026-10-02', 'score': 55, 'status': 'pending', 'skip_reason': null},
          ],
        };
      final before = DateTime.now();
      final w = await SwingRepository(api).wheel('i1');
      expect(api.calls.single['path'], '/instances/i1/wheel');
      // FW item 4 (M-3): the snapshot carries when it was fetched.
      expect(w.fetchedAt, isNotNull);
      expect(w.fetchedAt!.isBefore(before), isFalse);
      expect(w.openPuts.single.currentPrice, isNull);
      expect(w.openPuts.single.monitorWillBuyBack, isFalse);
      expect(w.collateralTotal, 13000.0);
      expect(w.recentScans.single.skipReason, '');
    });
  });

  test('WheelPut.monitorWillBuyBack mirrors the 15:45 monitor rules', () {
    WheelPut put(double? itm, int? dte) => WheelPut(
        contract: 'c', underlying: 'u', expiry: '', itmPct: itm, dte: dte);
    expect(put(10, 20).monitorWillBuyBack, isTrue);
    expect(put(5, 2).monitorWillBuyBack, isTrue);
    expect(put(0.4, 0).monitorWillBuyBack, isTrue);
    expect(put(5, 3).monitorWillBuyBack, isFalse);
    expect(put(-3, 0).monitorWillBuyBack, isFalse);
    expect(put(null, 0).monitorWillBuyBack, isFalse);
  });
}
