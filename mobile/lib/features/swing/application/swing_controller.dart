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
        'Approve ${s.symbol}? The broker rebuilds the order at the live price and checks it before sending. Decisions are final.',
      'approve_half' =>
        'Approve ${s.symbol} at half size? The broker rebuilds the order at the live price and checks it before sending. Decisions are final.',
      _ => 'Reject ${s.symbol}? Decisions are final.',
    };

String decisionSuccessMessage(SwingSignal s, String decision) =>
    switch (decision) {
      // No notification is promised: some refusals send none (FW item 4).
      'approve' =>
        'Approved ${s.symbol}. The broker rebuilds and checks the order at the live price before sending it.',
      'approve_half' =>
        'Approved ${s.symbol} at half size. The broker rebuilds and checks the order at the live price before sending it.',
      _ => 'Rejected ${s.symbol}.',
    };

/// FW-api-I1 / follow-up 1: the controller's wording for a 202 (the server
/// sends the same text); the fallback when a 202 carries no detail.
String uncertainMessage([String what = 'Approval']) =>
    '$what received, but its delivery to the broker could not be confirmed. '
    'Do NOT place this order by hand — it may still be queued. The card will '
    'show submitted or failed shortly.';

final kUncertainApproval = uncertainMessage();

// ── Pending signals (polled) ─────────────────────────────────────────────────

enum DecisionOutcome {
  /// The server recorded it; the card goes. It comes back only if the
  /// broker puts the signal back to pending (FW-api-I2).
  recorded,

  /// 202 (FW-api-I1): the approval is recorded but the broker command may or
  /// may not be queued. The card goes as for [recorded]; the message is the
  /// server's advice (check the signal status and open orders).
  uncertain,

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

/// The clock the stuck-approval rule reads; tests override it.
final swingClockProvider = Provider<DateTime Function()>((ref) => DateTime.now);

/// An approval no broker command has claimed this long is offered a re-send
/// (fix wave item 3).
const stuckAfter = Duration(minutes: 2);

/// Approved signals no broker command has claimed for more than
/// [stuckAfter], counted from the later of the decision and this device's
/// last re-send. An undated one counts as stuck: the server refuses a
/// re-send while a command for it is still queued.
List<SwingSignal> stuckApprovals(List<SwingSignal> approved, DateTime now,
    Map<String, DateTime> resentAt) {
  return approved.where((s) {
    final times = [s.decidedAt, resentAt[s.id]].whereType<DateTime>();
    if (times.isEmpty) return true;
    final since = times.reduce((a, b) => a.isAfter(b) ? a : b);
    return now.difference(since) > stuckAfter;
  }).toList();
}

String stuckLabel(SwingSignal s, DateTime now) {
  final at = s.decidedAt;
  if (at == null) return 'Approved; the broker has not picked it up yet.';
  final mins = now.difference(at).inMinutes;
  return 'Approved ${mins < 1 ? 1 : mins} min ago; the broker has not picked it up yet.';
}

String resendConfirmBody(SwingSignal s) =>
    'Re-send the approval for ${s.symbol}? The broker rebuilds the order at the '
    'live price and checks it before sending; a copy it already picked up is '
    'ignored.';

class PendingSignalsState {
  const PendingSignalsState({
    this.signals = const [],
    this.stuck = const [],
    this.deciding = const {},
    this.resending = const {},
    this.refreshError,
    this.asOf,
  });

  final List<SwingSignal> signals;

  /// Approved signals no broker command has claimed for [stuckAfter]: each
  /// gets a Re-send button.
  final List<SwingSignal> stuck;

  /// Signal ids whose decision request is in flight — their buttons disable.
  final Set<String> deciding;

  /// Signal ids whose re-send request is in flight.
  final Set<String> resending;

  /// Set when the latest poll failed; [signals] is then the last good list.
  final String? refreshError;

  /// The clock [stuck] was computed at.
  final DateTime? asOf;

  bool isDeciding(String id) => deciding.contains(id);
  bool isResending(String id) => resending.contains(id);

  PendingSignalsState copyWith({
    List<SwingSignal>? signals,
    List<SwingSignal>? stuck,
    Set<String>? deciding,
    Set<String>? resending,
    String? refreshError,
    bool clearRefreshError = false,
    DateTime? asOf,
  }) =>
      PendingSignalsState(
        signals: signals ?? this.signals,
        stuck: stuck ?? this.stuck,
        deciding: deciding ?? this.deciding,
        resending: resending ?? this.resending,
        refreshError:
            clearRefreshError ? null : (refreshError ?? this.refreshError),
        asOf: asOf ?? this.asOf,
      );
}

class PendingSignalsNotifier
    extends AutoDisposeFamilyAsyncNotifier<PendingSignalsState, String> {
  static const pollEvery = Duration(seconds: 30);

  IntervalPoller? _poller;

  /// FW-api-I2. Each fetch takes a generation as it starts. A decision's 2xx
  /// hides its card from fetches that were already in flight when it arrived
  /// (their answer can predate it): id -> the newest generation started then.
  /// The hide ends when a fetch begun after the 2xx answers, or when a fetch
  /// returns the id with a non-pending status; the server governs after
  /// that, so a signal the broker puts back to pending shows again.
  final Map<String, int> _hidden = <String, int>{};
  int _generation = 0;

  /// The newest generation whose answer is on screen: an older fetch that
  /// lands after it is dropped, not applied over it.
  int _applied = 0;

  /// id -> when this device last re-sent it, or was refused because a
  /// command was already queued (fix wave item 3).
  final Map<String, DateTime> _resentAt = <String, DateTime>{};

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

    // The notifier survives ref.invalidate (riverpod 2.6.1), so pull-to-
    // refresh lands here with the old hides: it clears them (FW-api-I2).
    _hidden.clear();
    final lifecycle = ref.read(appLifecycleProvider);
    final generation = ++_generation;
    final (pending, approved) = await _fetch();
    if (generation > _applied) _applied = generation;
    final first = _apply(const PendingSignalsState(), generation, pending, approved);
    if (disposed) return first;

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
    return first;
  }

  /// The pending list and the approved lists, requested together. The
  /// approved ones feed [PendingSignalsState.stuck] and end a hide.
  Future<(List<SwingSignal>, List<SwingSignal>)> _fetch() async {
    final repo = ref.read(swingRepositoryProvider);
    // Future.wait: the first error is thrown and the other one is handled.
    final lists = await Future.wait(
        [repo.pendingSignals(arg), repo.approvedSignals(arg)]);
    return (lists[0], lists[1]);
  }

  PendingSignalsState _apply(PendingSignalsState current, int generation,
      List<SwingSignal> pending, List<SwingSignal> approved) {
    final now = ref.read(swingClockProvider)();
    return current.copyWith(
      signals: _visible(generation, pending,
          nonPending: approved.map((s) => s.id)),
      stuck: stuckApprovals(approved, now, _resentAt),
      asOf: now,
      clearRefreshError: true,
    );
  }

  /// [rows] are the pending rows of the fetch that took [generation];
  /// [nonPending] the ids it saw with another status. The lists are separate
  /// requests, so a row the same fetch also saw non-pending is not shown
  /// pending (its pending read may predate the decision).
  List<SwingSignal> _visible(int generation, List<SwingSignal> rows,
      {Iterable<String> nonPending = const []}) {
    final seen = nonPending.toSet();
    _hidden.removeWhere((id, at) => generation > at || seen.contains(id));
    return rows
        .where((s) => !_hidden.containsKey(s.id) && !seen.contains(s.id))
        .toList();
  }

  /// One poll cycle. A failure keeps the last good list and says so.
  Future<void> refresh() async {
    final generation = ++_generation;
    try {
      final (pending, approved) = await _fetch();
      if (generation < _applied) return;
      _applied = generation;
      final current = state.valueOrNull ?? const PendingSignalsState();
      state = AsyncData(_apply(current, generation, pending, approved));
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
        _hidden.containsKey(signal.id)) {
      return const DecisionResult(DecisionOutcome.ignored, '');
    }
    state = AsyncData(
        current.copyWith(deciding: {...current.deciding, signal.id}));
    try {
      final receipt = await ref
          .read(swingRepositoryProvider)
          .decide(arg, signal.id, decision, reason: reason);
      _hidden[signal.id] = _generation;
      _drop(signal.id);
      if (receipt.uncertain) {
        return DecisionResult(
          DecisionOutcome.uncertain,
          receipt.detail.isEmpty ? uncertainMessage() : receipt.detail,
        );
      }
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
      if (code == 503) {
        // Provably not queued: the signal is pending, so a retry is safe.
        return DecisionResult(
          DecisionOutcome.failed,
          detail.isEmpty
              ? 'Not queued — the signal is still pending; try again.'
              : detail,
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

  /// Re-send a stuck approval (fix wave item 3). 200: queued. 202: the
  /// queue write may or may not have landed. 404/409: nothing to re-send now
  /// (no longer approved, or a command is still queued); the card goes and
  /// waits another [stuckAfter]. Anything else keeps the card.
  Future<DecisionResult> resend(SwingSignal signal) async {
    final current = state.valueOrNull;
    if (current == null || current.resending.contains(signal.id)) {
      return const DecisionResult(DecisionOutcome.ignored, '');
    }
    state = AsyncData(
        current.copyWith(resending: {...current.resending, signal.id}));
    try {
      final receipt =
          await ref.read(swingRepositoryProvider).resend(arg, signal.id);
      _snooze(signal.id);
      if (receipt.uncertain) {
        return DecisionResult(
          DecisionOutcome.uncertain,
          receipt.detail.isEmpty ? uncertainMessage('Re-send') : receipt.detail,
        );
      }
      return DecisionResult(
          DecisionOutcome.recorded, 'Re-sent ${signal.symbol} to the broker.');
    } on ApiError catch (err) {
      final code = err.statusCode;
      final detail = err.message.trim();
      if (code == 404 || code == 409) {
        _snooze(signal.id);
        return DecisionResult(DecisionOutcome.noLongerPending,
            detail.isEmpty ? 'Nothing to re-send for this signal.' : detail);
      }
      _releaseResend(signal.id);
      if (code == 401) {
        return const DecisionResult(
            DecisionOutcome.failed, 'Session expired — please sign in again.');
      }
      if (code == 503) {
        return DecisionResult(DecisionOutcome.failed,
            detail.isEmpty ? 'Not queued — try again.' : detail);
      }
      return DecisionResult(DecisionOutcome.failed,
          detail.isEmpty ? 'Could not re-send that approval.' : detail);
    } catch (err) {
      _releaseResend(signal.id);
      return DecisionResult(
          DecisionOutcome.failed, 'Could not re-send that approval: $err');
    }
  }

  /// Takes the stuck card off and holds it back for another [stuckAfter].
  void _snooze(String id) {
    _resentAt[id] = ref.read(swingClockProvider)();
    final current = state.valueOrNull ?? const PendingSignalsState();
    state = AsyncData(current.copyWith(
      stuck: current.stuck.where((s) => s.id != id).toList(),
      resending: {...current.resending}..remove(id),
    ));
  }

  void _releaseResend(String id) {
    final current = state.valueOrNull ?? const PendingSignalsState();
    state = AsyncData(
        current.copyWith(resending: {...current.resending}..remove(id)));
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
