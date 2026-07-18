import 'dart:async';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import '../data/backtest_repository.dart';
import '../data/models/backtest.dart';

/// Holds all data for the detail screen.
class BacktestDetailState {
  const BacktestDetailState({
    this.summary,
    this.graphData,
    this.llmCost,
    this.statusData,
    this.loading = true,
    this.error,
    this.llmCostLoading = false,
    this.llmCostError,
    this.logsLoading = false,
    this.logsError,
    this.logLines = const [],
    this.logSource = '',
  });

  final BacktestSummary? summary;
  final BacktestGraphData? graphData;
  final LlmCost? llmCost;
  final BacktestStatus? statusData;
  final bool loading;
  final String? error;
  final bool llmCostLoading;
  final String? llmCostError;
  final bool logsLoading;
  final String? logsError;
  final List<String> logLines;
  final String logSource;

  String get currentStatus =>
      statusData?.status ?? summary?.status ?? 'unknown';

  num? get progress => statusData?.progress;
  num? get elapsedSeconds =>
      statusData?.timeElapsedSeconds ?? summary?.timeElapsedSeconds;
  NexusLookback? get nexusLookback => statusData?.nexusLookback;

  bool get isActive {
    final s = currentStatus.toLowerCase();
    return s == 'running' || s == 'queued' || s == 'pending' ||
        s == 'paused' || s == 'paused_llm_critical';
  }

  bool get isTerminal {
    final s = currentStatus.toLowerCase();
    return s == 'completed' || s == 'finished' || s == 'stopped' ||
        s == 'failed' || s == 'error' || s == 'cancelled';
  }

  BacktestDetailState copyWith({
    BacktestSummary? summary,
    BacktestGraphData? graphData,
    LlmCost? llmCost,
    BacktestStatus? statusData,
    bool? loading,
    String? error,
    bool clearError = false,
    bool? llmCostLoading,
    String? llmCostError,
    bool clearLlmCostError = false,
    bool? logsLoading,
    String? logsError,
    bool clearLogsError = false,
    List<String>? logLines,
    String? logSource,
  }) =>
      BacktestDetailState(
        summary: summary ?? this.summary,
        graphData: graphData ?? this.graphData,
        llmCost: llmCost ?? this.llmCost,
        statusData: statusData ?? this.statusData,
        loading: loading ?? this.loading,
        error: clearError ? null : (error ?? this.error),
        llmCostLoading: llmCostLoading ?? this.llmCostLoading,
        llmCostError: clearLlmCostError
            ? null
            : (llmCostError ?? this.llmCostError),
        logsLoading: logsLoading ?? this.logsLoading,
        logsError:
            clearLogsError ? null : (logsError ?? this.logsError),
        logLines: logLines ?? this.logLines,
        logSource: logSource ?? this.logSource,
      );
}

class BacktestDetailController
    extends AutoDisposeFamilyNotifier<BacktestDetailState, String> {
  Timer? _pollTimer;
  int _llmCostTick = 0;
  late final String _id;

  @override
  BacktestDetailState build(String id) {
    _id = id;
    ref.onDispose(_stopPoll);
    Future.microtask(() => _init(id));
    return const BacktestDetailState(loading: true);
  }

  BacktestRepository get _repo => ref.read(backtestRepositoryProvider);

  Future<void> _init(String id) async {
    state = state.copyWith(loading: true, clearError: true);
    try {
      final results = await Future.wait([
        _repo.summary(id),
        _repo.graphData(id).catchError((_) => const BacktestGraphData()),
        _repo.llmCost(id).catchError((_) => const LlmCost()),
      ]);
      state = state.copyWith(
        summary: results[0] as BacktestSummary,
        graphData: results[1] as BacktestGraphData,
        llmCost: results[2] as LlmCost,
        loading: false,
        clearError: true,
      );
      final s = (state.summary?.status ?? '').toLowerCase();
      if (_isActive(s)) {
        await _fetchStatus(id);
        _startPoll(id);
      }
    } catch (e) {
      state = state.copyWith(loading: false, error: e.toString());
    }
  }

  bool _isActive(String s) =>
      s == 'running' || s == 'queued' || s == 'pending' ||
      s == 'paused' || s == 'paused_llm_critical';

  bool _isTerminal(String s) =>
      s == 'completed' || s == 'finished' || s == 'stopped' ||
      s == 'failed' || s == 'error' || s == 'cancelled';

  void _startPoll(String id) {
    if (_pollTimer != null) return;
    _pollTimer = Timer.periodic(
      const Duration(seconds: 3),
      (_) => _tick(id),
    );
  }

  void _stopPoll() {
    _pollTimer?.cancel();
    _pollTimer = null;
  }

  Future<void> _tick(String id) async {
    _llmCostTick = (_llmCostTick + 1) % 10;
    if (_llmCostTick == 0) {
      _refreshLlmCost(id);
    }
    await _fetchStatus(id);
  }

  Future<void> _fetchStatus(String id) async {
    try {
      final s = await _repo.status(id);
      state = state.copyWith(statusData: s);
      final st = (s.status ?? '').toLowerCase();
      if (_isTerminal(st)) {
        _stopPoll();
        final results = await Future.wait([
          _repo.summary(id),
          _repo.graphData(id)
              .catchError((_) => const BacktestGraphData()),
          _repo.llmCost(id).catchError((_) => const LlmCost()),
        ]);
        state = state.copyWith(
          summary: results[0] as BacktestSummary,
          graphData: results[1] as BacktestGraphData,
          llmCost: results[2] as LlmCost,
        );
      } else if (st == 'paused') {
        _stopPoll();
      } else {
        // Still running — refresh the summary so the stat tiles and the crypto
        // Fees card update live (the broker rewrites pnl/trades/fees each loop).
        try {
          final sum = await _repo.summary(id);
          state = state.copyWith(summary: sum);
        } catch (_) {}
      }
    } catch (_) {}
  }

  Future<void> _refreshLlmCost(String id) async {
    try {
      final cost = await _repo.llmCost(id);
      state = state.copyWith(llmCost: cost, clearLlmCostError: true);
    } catch (_) {}
  }

  // ── Public API ────────────────────────────────────────────────────────────

  Future<void> refreshLlmCost() async {
    state = state.copyWith(llmCostLoading: true, clearLlmCostError: true);
    try {
      final cost = await _repo.llmCost(_id);
      state = state.copyWith(llmCost: cost, llmCostLoading: false);
    } catch (e) {
      state = state.copyWith(
          llmCostLoading: false, llmCostError: e.toString());
    }
  }

  Future<void> loadLogs() async {
    if (state.logsLoading) return;
    state = state.copyWith(logsLoading: true, clearLogsError: true);
    try {
      final data = await _repo.logs(_id);
      final lines = (data['logs'] as List? ?? []).cast<String>();
      state = state.copyWith(
        logsLoading: false,
        logLines: lines,
        logSource: data['source']?.toString() ?? 'db',
      );
    } catch (e) {
      state = state.copyWith(logsLoading: false, logsError: e.toString());
    }
  }

  /// Returns error string or null on success.
  Future<String?> performAction(String action) async {
    try {
      if (action == 'delete') {
        await _repo.delete(_id);
        _stopPoll();
        return null;
      }
      await _repo.action(_id, action);
      await _fetchStatus(_id);
      if (action == 'stop') {
        _stopPoll();
      } else if (action == 'resume') {
        _startPoll(_id);
      }
      return null;
    } catch (e) {
      return e.toString();
    }
  }

  Future<Map<String, dynamic>> rerun() async {
    final s = state.summary;
    if (s == null) throw Exception('No summary loaded');
    final body = <String, dynamic>{
      if (s.instanceId != null) 'instance_id': s.instanceId,
      'stocks': s.tickers,
      'start_date': s.startDate,
      'end_date': s.endDate,
      'granularity': s.granularity ?? '60',
      'initial_cash': s.initialCash ?? 100000,
      'emulate_fee_venue': s.emulateFeeVenue ?? 'default',
    };
    return _repo.create(body);
  }
}

final backtestDetailControllerProvider = AutoDisposeNotifierProvider.family<
    BacktestDetailController, BacktestDetailState, String>(
  BacktestDetailController.new,
);
