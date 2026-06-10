import 'dart:async';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import '../data/backtest_repository.dart';
import '../data/models/backtest.dart';

/// State for the backtests list screen.
class BacktestsListState {
  const BacktestsListState({
    this.rows = const [],
    this.total = 0,
    this.totalPages = 1,
    this.page = 1,
    this.perPage = 15,
    this.sortBy = 'completed_at',
    this.sortOrder = 'desc',
    this.loading = false,
    this.error,
    this.statusMap = const {},
  });

  final List<BacktestRow> rows;
  final int total;
  final int totalPages;
  final int page;
  final int perPage;
  final String sortBy;
  final String sortOrder;
  final bool loading;
  final String? error;
  // Live status overlay keyed by backtest id
  final Map<String, BacktestStatus> statusMap;

  BacktestsListState copyWith({
    List<BacktestRow>? rows,
    int? total,
    int? totalPages,
    int? page,
    int? perPage,
    String? sortBy,
    String? sortOrder,
    bool? loading,
    String? error,
    bool clearError = false,
    Map<String, BacktestStatus>? statusMap,
  }) =>
      BacktestsListState(
        rows: rows ?? this.rows,
        total: total ?? this.total,
        totalPages: totalPages ?? this.totalPages,
        page: page ?? this.page,
        perPage: perPage ?? this.perPage,
        sortBy: sortBy ?? this.sortBy,
        sortOrder: sortOrder ?? this.sortOrder,
        loading: loading ?? this.loading,
        error: clearError ? null : (error ?? this.error),
        statusMap: statusMap ?? this.statusMap,
      );
}

class BacktestsListController extends AutoDisposeNotifier<BacktestsListState> {
  Timer? _pollTimer;

  @override
  BacktestsListState build() {
    ref.onDispose(_stopPoll);
    // Initial load + start polling
    Future.microtask(_loadPage);
    return const BacktestsListState(loading: true);
  }

  BacktestRepository get _repo =>
      ref.read(backtestRepositoryProvider);

  Future<void> _loadPage({int? page}) async {
    final s = state;
    state = s.copyWith(loading: true, clearError: true);
    try {
      final resp = await _repo.list(
        page: page ?? s.page,
        perPage: s.perPage,
        sortBy: s.sortBy,
        sortOrder: s.sortOrder,
      );
      state = state.copyWith(
        rows: resp.backtests,
        total: resp.total,
        totalPages: resp.totalPages,
        page: resp.page,
        loading: false,
        clearError: true,
      );
      _managePoll();
    } catch (e) {
      state = state.copyWith(loading: false, error: e.toString());
    }
  }

  void _managePoll() {
    final hasRunning = state.rows.any((r) {
      final live = state.statusMap[r.id]?.status ?? r.status ?? '';
      return _isActive(live);
    });
    if (hasRunning) {
      _startPoll();
    } else {
      _stopPoll();
    }
  }

  bool _isActive(String s) {
    final l = s.toLowerCase();
    return l == 'running' || l == 'queued' || l == 'pending' || l == 'paused' || l == 'paused_llm_critical';
  }

  bool _isTerminal(String s) {
    final l = s.toLowerCase();
    return l == 'completed' || l == 'finished' || l == 'stopped' || l == 'failed' || l == 'error' || l == 'cancelled';
  }

  void _startPoll() {
    if (_pollTimer != null) return;
    _pollTimer = Timer.periodic(const Duration(seconds: 3), (_) => _pollRunning());
  }

  void _stopPoll() {
    _pollTimer?.cancel();
    _pollTimer = null;
  }

  Future<void> _pollRunning() async {
    final running = state.rows.where((r) {
      final live = state.statusMap[r.id]?.status ?? r.status ?? '';
      return _isActive(live);
    }).toList();
    if (running.isEmpty) {
      _stopPoll();
      return;
    }
    var updated = Map<String, BacktestStatus>.from(state.statusMap);
    bool anyTerminated = false;
    await Future.wait(running.map((bt) async {
      try {
        final s = await _repo.status(bt.id);
        updated[bt.id] = s;
        if (_isTerminal(s.status ?? '')) anyTerminated = true;
      } catch (_) {}
    }));
    state = state.copyWith(statusMap: updated);
    if (anyTerminated) {
      await _loadPage(page: state.page);
    }
  }

  // ── Public actions ────────────────────────────────────────────────────────

  Future<void> refresh() => _loadPage();

  Future<void> goToPage(int page) => _loadPage(page: page);

  void setPerPage(int perPage) {
    state = state.copyWith(perPage: perPage);
    _loadPage(page: 1);
  }

  void toggleSort(String field) {
    if (state.sortBy == field) {
      state = state.copyWith(
          sortOrder: state.sortOrder == 'asc' ? 'desc' : 'asc');
    } else {
      state = state.copyWith(sortBy: field, sortOrder: 'desc');
    }
    _loadPage(page: 1);
  }

  Future<String?> performAction(String id, String action) async {
    try {
      await _repo.action(id, action);
      // Immediately refresh status for that item
      try {
        final s = await _repo.status(id);
        final updated = Map<String, BacktestStatus>.from(state.statusMap);
        updated[id] = s;
        state = state.copyWith(statusMap: updated);
      } catch (_) {}
      _loadPage(page: state.page);
      if (action == 'resume') _startPoll();
      return null; // success
    } catch (e) {
      return e.toString();
    }
  }
}

final backtestsListControllerProvider =
    AutoDisposeNotifierProvider<BacktestsListController, BacktestsListState>(
  BacktestsListController.new,
);
