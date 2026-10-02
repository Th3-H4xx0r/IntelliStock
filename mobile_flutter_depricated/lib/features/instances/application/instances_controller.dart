import 'dart:async';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import '../../../core/network/api_error.dart';
import '../../../core/polling/poller.dart';
import '../data/instance_repository.dart';
import '../data/models/instance.dart';

// ── Filter ───────────────────────────────────────────────────────────────────

enum InstanceFilter { all, user, ai }

// ── List state ───────────────────────────────────────────────────────────────

class InstancesState {
  const InstancesState({
    this.instances = const [],
    this.filter = InstanceFilter.all,
    this.busyIds = const {},
    this.errorMessage,
  });

  final List<Instance> instances;
  final InstanceFilter filter;
  final Set<String> busyIds;
  final String? errorMessage;

  List<Instance> get filtered {
    if (filter == InstanceFilter.all) return instances;
    final target = filter == InstanceFilter.ai ? 'ai' : 'user';
    return instances.where((i) => i.createdBy == target).toList();
  }

  int get allCount => instances.length;
  int get userCount =>
      instances.where((i) => i.createdBy != 'ai').length;
  int get aiCount => instances.where((i) => i.createdBy == 'ai').length;

  InstancesState copyWith({
    List<Instance>? instances,
    InstanceFilter? filter,
    Set<String>? busyIds,
    Object? errorMessage = _sentinel,
  }) =>
      InstancesState(
        instances: instances ?? this.instances,
        filter: filter ?? this.filter,
        busyIds: busyIds ?? this.busyIds,
        errorMessage: errorMessage == _sentinel
            ? this.errorMessage
            : errorMessage as String?,
      );
}

const _sentinel = Object();

// ── List controller (PollingNotifier) ────────────────────────────────────────

class InstancesController extends PollingNotifier<InstancesState> {
  @override
  Future<InstancesState> fetch() async {
    final repo = ref.read(instanceRepositoryProvider);
    final list = await repo.listInstances();
    return state.valueOrNull?.copyWith(instances: list) ??
        InstancesState(instances: list);
  }

  @override
  Duration interval() => const Duration(seconds: 30);

  void setFilter(InstanceFilter f) {
    if (state case AsyncData(:final value)) {
      state = AsyncData(value.copyWith(filter: f));
    }
  }

  Future<void> start(String id) => _instanceAction(id, (repo) => repo.startInstance(id));
  Future<void> stop(String id) => _instanceAction(id, (repo) => repo.stopInstance(id));

  Future<void> _instanceAction(
    String id,
    Future<void> Function(InstanceRepository) action,
  ) async {
    if (state case AsyncData(:final value)) {
      state = AsyncData(
          value.copyWith(busyIds: {...value.busyIds, id}));
    }
    try {
      await action(ref.read(instanceRepositoryProvider));
      await refreshNow();
    } on ApiError catch (e) {
      if (state case AsyncData(:final value)) {
        state = AsyncData(value.copyWith(
          busyIds: {...value.busyIds}..remove(id),
          errorMessage: e.message,
        ));
      }
    } finally {
      if (state case AsyncData(:final value)) {
        if (value.busyIds.contains(id)) {
          state = AsyncData(
              value.copyWith(busyIds: {...value.busyIds}..remove(id)));
        }
      }
    }
  }

  Future<void> delete(String id, {bool force = false}) async {
    final repo = ref.read(instanceRepositoryProvider);
    await _instanceAction(id, (_) => repo.deleteInstance(id, force: force));
  }

  Future<void> removeStock(String instanceId, String symbol) async {
    final repo = ref.read(instanceRepositoryProvider);
    await repo.removeStock(instanceId, symbol);
    await refreshNow();
  }

  Future<void> addStock(String instanceId, String symbol) async {
    final repo = ref.read(instanceRepositoryProvider);
    await repo.addStock(instanceId, symbol);
    await refreshNow();
  }

  Future<void> createInstance({
    required String id,
    String? name,
    String? granularity,
    bool runCommand = false,
    String? brokerageId,
    double? maxUsage,
    String? strategyId,
  }) async {
    final repo = ref.read(instanceRepositoryProvider);
    await repo.createInstance(
      id: id,
      name: name,
      granularity: granularity,
      runCommand: runCommand,
      brokerageId: brokerageId,
      maxUsage: maxUsage,
      strategyId: strategyId,
    );
    await refreshNow();
  }

  Future<void> createBacktest({
    required String instanceId,
    required List<String> stocks,
    required String startDate,
    required String endDate,
    String granularity = '60',
    double initialCash = 100000,
  }) async {
    final repo = ref.read(instanceRepositoryProvider);
    await repo.createBacktest(
      instanceId: instanceId,
      stocks: stocks,
      startDate: startDate,
      endDate: endDate,
      granularity: granularity,
      initialCash: initialCash,
    );
  }

  Future<void> linkStrategy(String instanceId, String strategyId) async {
    final repo = ref.read(instanceRepositoryProvider);
    await repo.linkStrategy(instanceId, strategyId);
    await refreshNow();
  }

  Future<void> linkBrokerage(String instanceId, String brokerageId) async {
    final repo = ref.read(instanceRepositoryProvider);
    await repo.linkBrokerage(instanceId, brokerageId);
    await refreshNow();
  }
}

final instancesControllerProvider =
    AutoDisposeAsyncNotifierProvider<InstancesController, InstancesState>(
  InstancesController.new,
);

// ── Selector data ─────────────────────────────────────────────────────────────

final brokeragesProvider = FutureProvider.autoDispose<List<Map<String, dynamic>>>(
  (ref) => ref.read(instanceRepositoryProvider).listBrokerages(),
);

final strategiesProvider = FutureProvider.autoDispose<List<Map<String, dynamic>>>(
  (ref) => ref.read(instanceRepositoryProvider).listStrategies(),
);

// ── Detail state ──────────────────────────────────────────────────────────────

class InstanceDetailState {
  const InstanceDetailState({
    this.instance,
    this.backtests = const [],
    this.btPage = 1,
    this.btTotalPages = 1,
    this.btTotal = 0,
    this.btSortBy = 'completed_at',
    this.btSortOrder = 'desc',
    this.btLoading = false,
    this.btProgress = const {},
    this.liveUptimeSecs = 0,
    this.errorMessage,
  });

  final Instance? instance;
  final List<BacktestRow> backtests;
  final int btPage;
  final int btTotalPages;
  final int btTotal;
  final String btSortBy;
  final String btSortOrder;
  final bool btLoading;

  /// Maps backtest id → progress (0-100).
  final Map<String, int> btProgress;
  final int liveUptimeSecs;
  final String? errorMessage;

  InstanceDetailState copyWith({
    Instance? instance,
    List<BacktestRow>? backtests,
    int? btPage,
    int? btTotalPages,
    int? btTotal,
    String? btSortBy,
    String? btSortOrder,
    bool? btLoading,
    Map<String, int>? btProgress,
    int? liveUptimeSecs,
    Object? errorMessage = _sentinel,
  }) =>
      InstanceDetailState(
        instance: instance ?? this.instance,
        backtests: backtests ?? this.backtests,
        btPage: btPage ?? this.btPage,
        btTotalPages: btTotalPages ?? this.btTotalPages,
        btTotal: btTotal ?? this.btTotal,
        btSortBy: btSortBy ?? this.btSortBy,
        btSortOrder: btSortOrder ?? this.btSortOrder,
        btLoading: btLoading ?? this.btLoading,
        btProgress: btProgress ?? this.btProgress,
        liveUptimeSecs: liveUptimeSecs ?? this.liveUptimeSecs,
        errorMessage: errorMessage == _sentinel
            ? this.errorMessage
            : errorMessage as String?,
      );
}

// ── Detail controller ─────────────────────────────────────────────────────────

class InstanceDetailController
    extends AutoDisposeFamilyAsyncNotifier<InstanceDetailState, String> {

  Timer? _uptimeTicker;
  Timer? _btPollTimer;
  late String _instanceId;
  bool _disposed = false;

  @override
  Future<InstanceDetailState> build(String arg) async {
    _instanceId = arg;

    ref.onDispose(() {
      _disposed = true;
      _uptimeTicker?.cancel();
      _btPollTimer?.cancel();
    });

    final repo = ref.read(instanceRepositoryProvider);
    final inst = await repo.getInstance(_instanceId);
    final btData = await repo.listBacktests(_instanceId);
    final rows = _parseBacktests(btData);

    final st = InstanceDetailState(
      instance: inst,
      backtests: rows,
      btTotal: (btData['total'] as num?)?.toInt() ?? rows.length,
      btTotalPages: (btData['total_pages'] as num?)?.toInt() ?? 1,
      btPage: 1,
      liveUptimeSecs: inst.uptimeSeconds ?? 0,
    );

    _startUptimeTicker(inst);
    _startBtPolling(rows);

    return st;
  }

  List<BacktestRow> _parseBacktests(Map<String, dynamic> data) {
    final list = data['backtests'] as List? ?? const [];
    return list
        .whereType<Map<String, dynamic>>()
        .map(BacktestRow.fromJson)
        .toList();
  }

  void _startUptimeTicker(Instance inst) {
    _uptimeTicker?.cancel();
    if (inst.runCommand) {
      _uptimeTicker = Timer.periodic(const Duration(seconds: 1), (_) {
        if (state case AsyncData(:final value)) {
          state = AsyncData(
              value.copyWith(liveUptimeSecs: value.liveUptimeSecs + 1));
        }
      });
    }
  }

  void _startBtPolling(List<BacktestRow> rows) {
    _btPollTimer?.cancel();
    final hasRunning = rows.any((b) {
      final s = b.status.toLowerCase();
      return s == 'running' || s == 'queued' || s == 'pending';
    });
    if (hasRunning) {
      _btPollTimer = Timer.periodic(const Duration(seconds: 3), (_) {
        _pollBtProgress();
      });
    }
  }

  Future<void> _pollBtProgress() async {
    if (state case AsyncData(:final value)) {
      final running = value.backtests.where((b) {
        final s = b.status.toLowerCase();
        return s == 'running' || s == 'queued' || s == 'pending';
      }).toList();

      if (running.isEmpty) {
        _btPollTimer?.cancel();
        return;
      }

      final repo = ref.read(instanceRepositoryProvider);
      final updatedProgress = Map<String, int>.from(value.btProgress);
      bool needRefresh = false;

      final results = await Future.wait(
        running.map((bt) async {
          try {
            return MapEntry(bt.id, await repo.getBacktestStatus(bt.id));
          } catch (_) {
            return null;
          }
        }),
      );

      if (_disposed) return;

      for (final entry in results) {
        if (entry == null) continue;
        final statusData = entry.value;
        final p = (statusData['progress'] as num?)?.toInt();
        if (p != null) updatedProgress[entry.key] = p;
        final s = (statusData['status'] ?? '').toString().toLowerCase();
        if (['completed', 'finished', 'stopped', 'failed', 'error',
            'cancelled'].contains(s)) {
          needRefresh = true;
        }
      }

      if (state case AsyncData(:final value)) {
        state = AsyncData(value.copyWith(btProgress: updatedProgress));
      }

      if (needRefresh) await _refreshBacktests();
    }
  }

  Future<void> refreshInstance() async {
    final repo = ref.read(instanceRepositoryProvider);
    try {
      final inst = await repo.getInstance(_instanceId);
      if (state case AsyncData(:final value)) {
        _uptimeTicker?.cancel();
        _startUptimeTicker(inst);
        state = AsyncData(value.copyWith(
          instance: inst,
          liveUptimeSecs: inst.uptimeSeconds ?? 0,
          errorMessage: null,
        ));
      }
    } on ApiError catch (e) {
      if (state case AsyncData(:final value)) {
        state = AsyncData(value.copyWith(errorMessage: e.message));
      }
    }
  }

  Future<void> toggleRun() async {
    if (state case AsyncData(:final value)) {
      final inst = value.instance;
      if (inst == null) return;
      final repo = ref.read(instanceRepositoryProvider);
      try {
        if (inst.runCommand) {
          await repo.stopInstance(inst.id);
        } else {
          await repo.startInstance(inst.id);
        }
        await refreshInstance();
      } on ApiError catch (e) {
        if (state case AsyncData(:final value)) {
          state = AsyncData(value.copyWith(errorMessage: e.message));
        }
      }
    }
  }

  Future<void> _refreshBacktests() async {
    if (state case AsyncData(:final value)) {
      state = AsyncData(value.copyWith(btLoading: true));
      try {
        final repo = ref.read(instanceRepositoryProvider);
        final data = await repo.listBacktests(
          _instanceId,
          page: value.btPage,
          sortBy: value.btSortBy,
          sortOrder: value.btSortOrder,
        );
        final rows = _parseBacktests(data);
        state = AsyncData(value.copyWith(
          backtests: rows,
          btTotal: (data['total'] as num?)?.toInt() ?? rows.length,
          btTotalPages: (data['total_pages'] as num?)?.toInt() ?? 1,
          btLoading: false,
        ));
        _startBtPolling(rows);
      } on ApiError catch (e) {
        state = AsyncData(value.copyWith(
          btLoading: false,
          errorMessage: e.message,
        ));
      }
    }
  }

  Future<void> goToBacktestPage(int page) async {
    if (state case AsyncData(:final value)) {
      state = AsyncData(value.copyWith(btPage: page));
    }
    await _refreshBacktests();
  }

  Future<void> sortBacktests(String field) async {
    if (state case AsyncData(:final value)) {
      final order = value.btSortBy == field
          ? (value.btSortOrder == 'asc' ? 'desc' : 'asc')
          : 'desc';
      state = AsyncData(value.copyWith(btSortBy: field, btSortOrder: order, btPage: 1));
    }
    await _refreshBacktests();
  }

  Future<void> removeStock(String symbol) async {
    if (state case AsyncData(:final value)) {
      final id = value.instance?.id;
      if (id == null) return;
      await ref.read(instanceRepositoryProvider).removeStock(id, symbol);
      await refreshInstance();
    }
  }

  Future<void> addStock(String symbol) async {
    if (state case AsyncData(:final value)) {
      final id = value.instance?.id;
      if (id == null) return;
      await ref.read(instanceRepositoryProvider).addStock(id, symbol);
      await refreshInstance();
    }
  }

  Future<void> linkStrategy(String strategyId) async {
    if (state case AsyncData(:final value)) {
      final id = value.instance?.id;
      if (id == null) return;
      await ref.read(instanceRepositoryProvider).linkStrategy(id, strategyId);
      await refreshInstance();
    }
  }

  Future<void> unlinkStrategy() async {
    if (state case AsyncData(:final value)) {
      final id = value.instance?.id;
      if (id == null) return;
      await ref.read(instanceRepositoryProvider).unlinkStrategy(id);
      await refreshInstance();
    }
  }

  Future<void> linkBrokerage(String brokerageId) async {
    if (state case AsyncData(:final value)) {
      final id = value.instance?.id;
      if (id == null) return;
      await ref
          .read(instanceRepositoryProvider)
          .linkBrokerage(id, brokerageId);
      await refreshInstance();
    }
  }

  Future<void> unlinkBrokerage() async {
    if (state case AsyncData(:final value)) {
      final id = value.instance?.id;
      if (id == null) return;
      await ref.read(instanceRepositoryProvider).unlinkBrokerage(id);
      await refreshInstance();
    }
  }

  Future<Map<String, dynamic>> previewClearState(String scope) async {
    if (state case AsyncData(:final value)) {
      final id = value.instance?.id;
      if (id == null) return {};
      return ref
          .read(instanceRepositoryProvider)
          .previewClearState(id, scope);
    }
    return {};
  }

  Future<Map<String, dynamic>> applyClearState(String scope) async {
    if (state case AsyncData(:final value)) {
      final id = value.instance?.id;
      if (id == null) return {};
      return ref
          .read(instanceRepositoryProvider)
          .applyClearState(id, scope);
    }
    return {};
  }

  Future<void> createBacktest({
    required List<String> stocks,
    required String startDate,
    required String endDate,
    String granularity = '60',
    double initialCash = 100000,
  }) async {
    if (state case AsyncData(:final value)) {
      final id = value.instance?.id;
      if (id == null) return;
      await ref.read(instanceRepositoryProvider).createBacktest(
            instanceId: id,
            stocks: stocks,
            startDate: startDate,
            endDate: endDate,
            granularity: granularity,
            initialCash: initialCash,
          );
      await _refreshBacktests();
    }
  }
}

final instanceDetailControllerProvider = AutoDisposeAsyncNotifierProvider
    .family<InstanceDetailController, InstanceDetailState, String>(
  InstanceDetailController.new,
);
