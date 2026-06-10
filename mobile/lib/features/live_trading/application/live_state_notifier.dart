import 'dart:async';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import '../../../core/models/portfolio_history.dart';
import '../../../core/polling/poller.dart';
import '../data/live_repository.dart';
import '../data/models/live_state.dart';

// ── Command toast model ───────────────────────────────────────────────────────

class CommandToast {
  const CommandToast({
    required this.commandId,
    required this.type,
    required this.status,
    this.error,
    this.result,
  });

  final String? commandId;
  final String type;
  final String status;
  final String? error;
  final Map<String, dynamic>? result;

  bool get isTerminal => status == 'completed' || status == 'failed';
  bool get isPending => status == 'pending' || status == 'running';

  CommandToast copyWith({
    String? commandId,
    String? type,
    String? status,
    String? error,
    Map<String, dynamic>? result,
  }) {
    return CommandToast(
      commandId: commandId ?? this.commandId,
      type: type ?? this.type,
      status: status ?? this.status,
      error: error,
      result: result ?? this.result,
    );
  }
}

// ── Full live trading state ───────────────────────────────────────────────────

class LiveTradingState {
  const LiveTradingState({
    this.liveState,
    this.notRunning = false,
    this.equityHistory,
    this.positionHistoricals = const {},
    this.currentRange = '1D',
    this.commandToast,
    this.fetchError,
  });

  final LiveState? liveState;
  final bool notRunning;
  final PortfolioHistory? equityHistory;
  final Map<String, List<HistPoint>> positionHistoricals;
  final String currentRange;
  final CommandToast? commandToast;
  final String? fetchError;

  LiveTradingState copyWith({
    LiveState? liveState,
    bool? notRunning,
    PortfolioHistory? equityHistory,
    Map<String, List<HistPoint>>? positionHistoricals,
    String? currentRange,
    Object? commandToast = _sentinel,
    Object? fetchError = _sentinel,
  }) {
    return LiveTradingState(
      liveState: liveState ?? this.liveState,
      notRunning: notRunning ?? this.notRunning,
      equityHistory: equityHistory ?? this.equityHistory,
      positionHistoricals: positionHistoricals ?? this.positionHistoricals,
      currentRange: currentRange ?? this.currentRange,
      commandToast: commandToast == _sentinel
          ? this.commandToast
          : commandToast as CommandToast?,
      fetchError:
          fetchError == _sentinel ? this.fetchError : fetchError as String?,
    );
  }
}

const _sentinel = Object();

// ── Notifier ─────────────────────────────────────────────────────────────────

/// Per-instance adaptive polling notifier.
/// 3s when tradingActive, 10s otherwise.
/// Create via [liveStateProvider].
class LiveStateNotifier extends AutoDisposeAsyncNotifier<LiveTradingState> {
  LiveStateNotifier(this.instanceId);

  final String instanceId;

  IntervalPoller? _poller;
  Timer? _commandPollTimer;
  Timer? _toastDismissTimer;

  @override
  Future<LiveTradingState> build() async {
    final lifecycle = ref.watch(appLifecycleProvider);
    final initial = await _fetchState();

    _poller = IntervalPoller(
      fetch: _pollCycle,
      interval: () {
        final ls = state.valueOrNull?.liveState;
        return (ls?.tradingActive == true)
            ? const Duration(seconds: 3)
            : const Duration(seconds: 10);
      },
    );

    if (lifecycle.isForeground) {
      _poller!.start();
    } else {
      _poller!.pause();
    }

    ref.listen(appLifecycleProvider, (_, next) {
      if (next.isForeground) {
        _poller?.resume();
      } else {
        _poller?.pause();
      }
    });

    ref.onDispose(() {
      _poller?.dispose();
      _commandPollTimer?.cancel();
      _toastDismissTimer?.cancel();
    });

    return initial;
  }

  // ── Internal fetch/poll ────────────────────────────────────────────────────

  Future<LiveTradingState> _fetchState() async {
    final repo = ref.read(liveRepositoryProvider);
    try {
      final ls = await repo.liveState(instanceId);
      final prev = state.valueOrNull;
      if (ls == null) {
        return prev?.copyWith(
              notRunning: true,
              liveState: null,
              fetchError: null,
            ) ??
            const LiveTradingState(notRunning: true);
      }
      return prev?.copyWith(
            liveState: ls,
            notRunning: false,
            fetchError: null,
          ) ??
          LiveTradingState(liveState: ls);
    } catch (e) {
      final prev = state.valueOrNull;
      return prev?.copyWith(fetchError: e.toString()) ??
          LiveTradingState(fetchError: e.toString());
    }
  }

  Future<void> _pollCycle() async {
    final next = await _fetchState();
    state = AsyncData(next);
    unawaited(_refreshEquityHistory());
    unawaited(_refreshPositionHistoricals());
  }

  Future<void> _refreshEquityHistory() async {
    final prev = state.valueOrNull;
    if (prev == null) return;
    try {
      final history = await ref
          .read(liveRepositoryProvider)
          .equityHistory(instanceId, prev.currentRange);
      if (state case AsyncData(:final value)) {
        state = AsyncData(value.copyWith(equityHistory: history));
      }
    } catch (_) {}
  }

  Future<void> _refreshPositionHistoricals() async {
    final prev = state.valueOrNull;
    if (prev == null) return;
    final symbols =
        prev.liveState?.positions.map((p) => p.symbol).toList() ?? [];
    if (symbols.isEmpty) return;
    try {
      final hist = await ref
          .read(liveRepositoryProvider)
          .symbolHistoricals(symbols, prev.currentRange);
      if (state case AsyncData(:final value)) {
        state = AsyncData(value.copyWith(positionHistoricals: hist));
      }
    } catch (_) {}
  }

  // ── Public API ────────────────────────────────────────────────────────────

  Future<void> setRange(String range) async {
    if (state case AsyncData(:final value)) {
      state = AsyncData(value.copyWith(currentRange: range));
    }
    await _refreshEquityHistory();
    await _refreshPositionHistoricals();
  }

  Future<void> refreshNow() async {
    final next = await _fetchState();
    state = AsyncData(next);
    unawaited(_refreshEquityHistory());
    unawaited(_refreshPositionHistoricals());
  }

  Future<void> runCommand(String type, Map<String, dynamic> payload) async {
    _commandPollTimer?.cancel();
    _toastDismissTimer?.cancel();
    _setToast(CommandToast(commandId: null, type: type, status: 'pending'));

    try {
      final result = await ref
          .read(liveRepositoryProvider)
          .sendCommand(instanceId, type, payload);
      _setToast(CommandToast(
        commandId: result.commandId,
        type: type,
        status: result.status,
        error: result.error,
        result: result.result,
      ));
      if (!result.isTerminal) {
        _pollCommandStatus(result.commandId);
      } else {
        _scheduleDismiss();
        unawaited(refreshNow());
      }
    } catch (e) {
      _setToast(CommandToast(
        commandId: null,
        type: type,
        status: 'failed',
        error: e.toString(),
      ));
      _scheduleDismiss(const Duration(seconds: 6));
    }
  }

  void _pollCommandStatus(String commandId) {
    _commandPollTimer?.cancel();
    _commandPollTimer = Timer(const Duration(seconds: 1), () async {
      try {
        final result =
            await ref.read(liveRepositoryProvider).commandStatus(commandId);
        final toastNow = state.valueOrNull?.commandToast;
        if (toastNow?.commandId != commandId) return;
        _setToast(CommandToast(
          commandId: commandId,
          type: toastNow!.type,
          status: result.status,
          error: result.error,
          result: result.result,
        ));
        if (result.isTerminal) {
          _scheduleDismiss();
          unawaited(refreshNow());
        } else {
          _pollCommandStatus(commandId);
        }
      } catch (_) {
        _pollCommandStatus(commandId);
      }
    });
  }

  void _setToast(CommandToast toast) {
    if (state case AsyncData(:final value)) {
      state = AsyncData(value.copyWith(commandToast: toast));
    }
  }

  void dismissToast() {
    _toastDismissTimer?.cancel();
    if (state case AsyncData(:final value)) {
      state = AsyncData(value.copyWith(commandToast: null));
    }
  }

  void _scheduleDismiss([Duration delay = const Duration(seconds: 5)]) {
    _toastDismissTimer?.cancel();
    _toastDismissTimer = Timer(delay, dismissToast);
  }
}

// ── Provider factory ──────────────────────────────────────────────────────────

/// Returns a fresh auto-dispose provider scoped to [instanceId].
/// The provider is re-created if instanceId changes (router navigation).
/// Usage:
///   final p = liveStateProvider(instanceId);
///   ref.watch(p)         -> AsyncValue of LiveTradingState
///   ref.read(p.notifier) → LiveStateNotifier
AutoDisposeAsyncNotifierProvider<LiveStateNotifier, LiveTradingState>
    liveStateProvider(String instanceId) {
  return AutoDisposeAsyncNotifierProvider<LiveStateNotifier, LiveTradingState>(
    () => LiveStateNotifier(instanceId),
  );
}
