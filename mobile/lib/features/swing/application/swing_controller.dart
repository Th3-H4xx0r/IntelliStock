import 'package:flutter_riverpod/flutter_riverpod.dart';
import '../../../core/network/api_error.dart';
import '../../../core/polling/poller.dart';
import '../data/swing_repository.dart';

// ── Which lanes does this instance run? ──────────────────────────────────────

class SwingLanes {
  const SwingLanes({required this.swing, required this.wheel});

  static const none = SwingLanes(swing: false, wheel: false);

  final bool swing;
  final bool wheel;

  bool get any => swing || wheel;
}

String _canonicalStrategyId(Object? raw) => (raw ?? '')
    .toString()
    .trim()
    .replaceAllMapped(RegExp(r'([a-z0-9])([A-Z])'), (m) => '${m[1]}_${m[2]}')
    .toLowerCase();

/// Reads the instance's nested strategy document (`Instance.strategy`).
/// Accepts the lowercase id ("strategy_swing") and the class name
/// ("StrategySwing").
SwingLanes swingLanesOf(Map<String, dynamic>? strategyDoc) {
  final subs = strategyDoc?['strategies'];
  if (subs is! List) return SwingLanes.none;
  var swing = false;
  var wheel = false;
  for (final sub in subs) {
    if (sub is! Map) continue;
    final id = _canonicalStrategyId(sub['strategy']);
    if (id == 'strategy_swing') swing = true;
    if (id == 'strategy_wheel') wheel = true;
  }
  return SwingLanes(swing: swing, wheel: wheel);
}

// ── Copy shared by the confirm dialog and the snackbar ───────────────────────

String decisionLabel(String decision) => switch (decision) {
      'approve' => 'Approve',
      'approve_half' => 'Approve ½',
      _ => 'Reject',
    };

String decisionConfirmBody(SwingSignal s, String decision) => switch (decision) {
      'approve' =>
        'Approve ${s.symbol}? The order is rebuilt at the live price and placed within seconds.',
      'approve_half' =>
        'Approve ${s.symbol} at half size? The order is rebuilt at the live price and placed within seconds.',
      _ => 'Reject ${s.symbol}? Decisions are final.',
    };

String decisionSuccessMessage(SwingSignal s, String decision) =>
    switch (decision) {
      'approve' =>
        "Approved ${s.symbol}. The order goes out on the broker's next command poll.",
      'approve_half' =>
        "Approved ${s.symbol} at half size. The order goes out on the broker's next command poll.",
      _ => 'Rejected ${s.symbol}.',
    };

// ── Pending signals (polled) ─────────────────────────────────────────────────

enum DecisionOutcome {
  /// The server recorded it; the card is gone for good.
  recorded,

  /// 400/404/409: already decided elsewhere (or gone). The card is removed;
  /// the next poll brings it back only if it is in fact still pending.
  noLongerPending,

  /// Anything else (401, 403, 5xx, network). The card stays and can be retried.
  failed,

  /// A second tap while the first request was in flight. Nothing was sent.
  ignored,
}

class DecisionResult {
  const DecisionResult(this.outcome, this.message);
  final DecisionOutcome outcome;
  final String message;
}

class PendingSignalsState {
  const PendingSignalsState({
    this.signals = const [],
    this.deciding = const {},
    this.refreshError,
  });

  final List<SwingSignal> signals;

  /// Signal ids whose decision request is in flight — their buttons disable.
  final Set<String> deciding;

  /// Set when the latest poll failed; [signals] is then the last good list.
  final String? refreshError;

  bool isDeciding(String id) => deciding.contains(id);

  PendingSignalsState copyWith({
    List<SwingSignal>? signals,
    Set<String>? deciding,
    String? refreshError,
    bool clearRefreshError = false,
  }) =>
      PendingSignalsState(
        signals: signals ?? this.signals,
        deciding: deciding ?? this.deciding,
        refreshError:
            clearRefreshError ? null : (refreshError ?? this.refreshError),
      );
}

class PendingSignalsNotifier
    extends AutoDisposeFamilyAsyncNotifier<PendingSignalsState, String> {
  static const pollEvery = Duration(seconds: 30);

  IntervalPoller? _poller;

  /// Ids this device decided. A poll that raced the decision must not bring
  /// the card back.
  final Set<String> _decided = <String>{};

  @override
  Future<PendingSignalsState> build(String arg) async {
    // Registered before the first await: if the screen is left while the
    // first GET is in flight, the element is gone by the time it returns,
    // onDispose would throw, and a poller started then would never stop.
    var disposed = false;
    ref.onDispose(() {
      disposed = true;
      _poller?.dispose();
    });

    final lifecycle = ref.read(appLifecycleProvider);
    final rows = await ref.read(swingRepositoryProvider).pendingSignals(arg);
    if (disposed) return PendingSignalsState(signals: _visible(rows));

    _poller?.dispose();
    _poller = IntervalPoller(fetch: refresh, interval: () => pollEvery);
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
    return PendingSignalsState(signals: _visible(rows));
  }

  List<SwingSignal> _visible(List<SwingSignal> rows) =>
      rows.where((s) => !_decided.contains(s.id)).toList();

  /// One poll cycle. A failure keeps the last good list and says so.
  Future<void> refresh() async {
    try {
      final rows = await ref.read(swingRepositoryProvider).pendingSignals(arg);
      final current = state.valueOrNull ?? const PendingSignalsState();
      state = AsyncData(
          current.copyWith(signals: _visible(rows), clearRefreshError: true));
    } catch (err) {
      final current = state.valueOrNull;
      if (current == null) return;
      state = AsyncData(current.copyWith(refreshError: err.toString()));
    }
  }

  Future<DecisionResult> decide(
    SwingSignal signal,
    String decision, {
    String? reason,
  }) async {
    final current = state.valueOrNull;
    if (current == null ||
        current.deciding.contains(signal.id) ||
        _decided.contains(signal.id)) {
      return const DecisionResult(DecisionOutcome.ignored, '');
    }
    state = AsyncData(
        current.copyWith(deciding: {...current.deciding, signal.id}));
    try {
      await ref
          .read(swingRepositoryProvider)
          .decide(arg, signal.id, decision, reason: reason);
      _decided.add(signal.id);
      _drop(signal.id);
      return DecisionResult(
          DecisionOutcome.recorded, decisionSuccessMessage(signal, decision));
    } on ApiError catch (err) {
      final code = err.statusCode;
      final detail = err.message.trim();
      if (code == 400 || code == 404 || code == 409) {
        _drop(signal.id);
        return DecisionResult(
          DecisionOutcome.noLongerPending,
          detail.isEmpty
              ? 'This signal is no longer pending — it was decided elsewhere.'
              : detail,
        );
      }
      _release(signal.id);
      if (code == 401) {
        return const DecisionResult(
            DecisionOutcome.failed, 'Session expired — please sign in again.');
      }
      if (code == 403) {
        return DecisionResult(
          DecisionOutcome.failed,
          detail.isEmpty ? 'You are not allowed to decide this signal.' : detail,
        );
      }
      return DecisionResult(DecisionOutcome.failed,
          detail.isEmpty ? 'Could not record that decision.' : detail);
    } catch (err) {
      _release(signal.id);
      return DecisionResult(
          DecisionOutcome.failed, 'Could not record that decision: $err');
    }
  }

  void _drop(String id) {
    final current = state.valueOrNull ?? const PendingSignalsState();
    state = AsyncData(current.copyWith(
      signals: current.signals.where((s) => s.id != id).toList(),
      deciding: {...current.deciding}..remove(id),
    ));
  }

  void _release(String id) {
    final current = state.valueOrNull ?? const PendingSignalsState();
    state =
        AsyncData(current.copyWith(deciding: {...current.deciding}..remove(id)));
  }
}

final pendingSignalsProvider = AsyncNotifierProvider.autoDispose
    .family<PendingSignalsNotifier, PendingSignalsState, String>(
  PendingSignalsNotifier.new,
);

// ── Wheel snapshot (pull-to-refresh) ─────────────────────────────────────────

final wheelSnapshotProvider =
    FutureProvider.autoDispose.family<WheelSnapshot, String>(
  (ref, instanceId) => ref.watch(swingRepositoryProvider).wheel(instanceId),
);
