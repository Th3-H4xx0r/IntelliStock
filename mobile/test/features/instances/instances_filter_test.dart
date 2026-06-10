import 'package:flutter_test/flutter_test.dart';
import 'package:intellistock_mobile/features/instances/application/instances_controller.dart';
import 'package:intellistock_mobile/features/instances/data/models/instance.dart';

// ── Helpers ──────────────────────────────────────────────────────────────────

Instance _inst({
  required String id,
  String createdBy = 'user',
  bool runCommand = false,
}) =>
    Instance(
      id: id,
      name: id,
      createdBy: createdBy,
      runCommand: runCommand,
    );

InstancesState _stateWith(List<Instance> instances,
        {InstanceFilter filter = InstanceFilter.all}) =>
    InstancesState(instances: instances, filter: filter);

// ── Tests ─────────────────────────────────────────────────────────────────────

void main() {
  group('InstancesState.filtered', () {
    final userInst = _inst(id: 'u1', createdBy: 'user');
    final aiInst = _inst(id: 'a1', createdBy: 'ai');
    final userInst2 = _inst(id: 'u2', createdBy: 'user', runCommand: true);

    final all = [userInst, aiInst, userInst2];

    test('filter=all returns all instances', () {
      final state = _stateWith(all, filter: InstanceFilter.all);
      expect(state.filtered, equals(all));
    });

    test('filter=user returns only user-created', () {
      final state = _stateWith(all, filter: InstanceFilter.user);
      expect(state.filtered, containsAll([userInst, userInst2]));
      expect(state.filtered, isNot(contains(aiInst)));
    });

    test('filter=ai returns only ai-created', () {
      final state = _stateWith(all, filter: InstanceFilter.ai);
      expect(state.filtered, equals([aiInst]));
    });

    test('filter=user on empty list returns empty', () {
      final state = _stateWith([], filter: InstanceFilter.user);
      expect(state.filtered, isEmpty);
    });
  });

  group('InstancesState counts', () {
    final instances = [
      _inst(id: 'u1', createdBy: 'user'),
      _inst(id: 'u2', createdBy: 'user'),
      _inst(id: 'a1', createdBy: 'ai'),
    ];

    test('allCount is total instances length', () {
      final state = _stateWith(instances);
      expect(state.allCount, 3);
    });

    test('userCount counts non-ai', () {
      final state = _stateWith(instances);
      expect(state.userCount, 2);
    });

    test('aiCount counts ai-created', () {
      final state = _stateWith(instances);
      expect(state.aiCount, 1);
    });

    test('counts sum to allCount', () {
      final state = _stateWith(instances);
      expect(state.userCount + state.aiCount, equals(state.allCount));
    });
  });

  group('Instance.fromJson', () {
    test('parses minimal json', () {
      final inst = Instance.fromJson({'id': 'test-1', 'run_command': false});
      expect(inst.id, 'test-1');
      expect(inst.createdBy, 'user');
      expect(inst.runCommand, false);
      expect(inst.stocks, isEmpty);
    });

    test('parses run_command true', () {
      final inst = Instance.fromJson({'id': 'x', 'run_command': true});
      expect(inst.runCommand, true);
    });

    test('parses stocks as list of strings', () {
      final inst = Instance.fromJson({
        'id': 'x',
        'stocks': ['AAPL', 'TSLA'],
      });
      expect(inst.stocks, ['AAPL', 'TSLA']);
    });

    test('parses stocks as list of maps with symbol key', () {
      final inst = Instance.fromJson({
        'id': 'x',
        'stocks': [
          {'symbol': 'MSFT'},
          {'symbol': 'NVDA'},
        ],
      });
      expect(inst.stocks, ['MSFT', 'NVDA']);
    });

    test('tolerates null/missing fields', () {
      final inst = Instance.fromJson({});
      expect(inst.id, '');
      expect(inst.name, '');
      expect(inst.strategyId, isNull);
      expect(inst.brokerageId, isNull);
      expect(inst.granularityTimeIncrement, isNull);
      expect(inst.maxUsage, isNull);
      expect(inst.uptimeSeconds, isNull);
    });

    test('parses granularity_time_increment', () {
      final inst = Instance.fromJson({
        'id': 'x',
        'granularity_time_increment': 300,
      });
      expect(inst.granularityTimeIncrement, 300);
    });

    test('falls back to granularity string field', () {
      final inst =
          Instance.fromJson({'id': 'x', 'granularity': '3600'});
      expect(inst.granularityTimeIncrement, 3600);
    });
  });

  group('BacktestRow.fromJson', () {
    test('parses full row', () {
      final bt = BacktestRow.fromJson({
        'id': 'bt-1',
        'stocks': ['AAPL'],
        'start_date': '2025-01-01',
        'end_date': '2025-06-01',
        'status': 'completed',
        'pnl': 1234.56,
        'pnl_percent': 12.34,
        'time_elapsed_seconds': 300.0,
      });
      expect(bt.id, 'bt-1');
      expect(bt.stocks, ['AAPL']);
      expect(bt.status, 'completed');
      expect(bt.pnl, closeTo(1234.56, 0.01));
      expect(bt.pnlPercent, closeTo(12.34, 0.01));
    });

    test('tolerates null pnl', () {
      final bt = BacktestRow.fromJson({'id': 'bt-2', 'status': 'queued'});
      expect(bt.pnl, isNull);
      expect(bt.pnlPercent, isNull);
    });
  });

  group('InstancesState.copyWith', () {
    test('copies with updated filter', () {
      final s = const InstancesState(filter: InstanceFilter.all);
      final s2 = s.copyWith(filter: InstanceFilter.ai);
      expect(s2.filter, InstanceFilter.ai);
      expect(s.filter, InstanceFilter.all); // immutable
    });

    test('copies with cleared errorMessage', () {
      final s = const InstancesState(errorMessage: 'oops');
      final s2 = s.copyWith(errorMessage: null);
      expect(s2.errorMessage, isNull);
    });

    test('busyIds toggle preserves other ids', () {
      final s = InstancesState(busyIds: {'a', 'b'});
      final s2 = s.copyWith(busyIds: {...s.busyIds, 'c'});
      expect(s2.busyIds, contains('a'));
      expect(s2.busyIds, contains('c'));
    });
  });
}
