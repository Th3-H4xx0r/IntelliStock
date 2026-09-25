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

// Follow-up 5: an approval is not final. A transient refusal (no quote before
// the open, say) puts the signal back to pending.
const _approveAfter = "Approving sends the order. If the broker can't place it "
    'yet (e.g. before the open) the signal returns here to approve again.';

String decisionConfirmBody(SwingSignal s, String decision) => switch (decision) {
      'approve' =>
        'Approve ${s.symbol}? The broker rebuilds the order at the live price and checks it before sending. $_approveAfter',
      'approve_half' =>
        'Approve ${s.symbol} at half size? The broker rebuilds the order at the live price and checks it before sending. $_approveAfter',
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

/// The New York calendar date ("YYYY-MM-DD") at [instant]. US Eastern
/// time, exactly: EDT (UTC-4) from 02:00 local on the
/// second Sunday of March to 02:00 local on the first Sunday of November,
/// EST (UTC-5) otherwise.
String nyDate(DateTime instant) {
  final u = instant.toUtc();
  DateTime nthSunday(int month, int n) {
    final first = DateTime.utc(u.year, month, 1);
    final offset = (DateTime.sunday - first.weekday) % 7;
    return first.add(Duration(days: offset + 7 * (n - 1)));
  }

  final dstStart = nthSunday(3, 2).add(const Duration(hours: 7)); // 02:00 EST
  final dstEnd = nthSunday(11, 1).add(const Duration(hours: 6)); // 02:00 EDT
  final edt = !u.isBefore(dstStart) && u.isBefore(dstEnd);
  final local = u.subtract(Duration(hours: edt ? 4 : 5));
  String two(int v) => v.toString().padLeft(2, '0');
  return '${local.year}-${two(local.month)}-${two(local.day)}';
}

/// Round 3 minor 1: the server re-sends only an approval made today in New
/// York (its decidedAt, not its session: a wheel signal's session is its
/// weekly scan day), and it alone decides the 409. The button follows the
/// same rule. null when the card may offer Re-send, else the reason shown in
/// its place. [today] is nyDate(now).
String? resendBlockedReason(SwingSignal s, String today) {
  final at = s.decidedAt;
  final madeOn = at == null ? null : nyDate(at);
  if (madeOn != null && madeOn == today) return null;
  return 'This approval was made on ${madeOn ?? 'an unknown date'}; '
      'approve a fresh signal instead.';
}

String resendConfirmBody(SwingSignal s) =>
    'Re-send the approval for ${s.symbol}? The broker rebuilds the order at the '
    'live price and checks it before sending; a copy it already picked up is '
    'ignored.';

/// Follow-up 2: the badge on a card whose approval or re-send answered 202.
const uncertainBadge = 'uncertain — waiting for the broker';

/// Round 3 FU-1: what a waiting card says.
const waitingCopy = 'Delivery to the broker could not be confirmed. Do NOT '
    'place this order by hand. This should show submitted or failed within a '
    'minute; if it is still waiting after 2 minutes you can re-send it here.';

/// A 202'd approval or re-send, kept on its own card until a poll begun
/// after the 202 settles it (follow-up 2). Still approved [stuckAfter] after
/// the 202, it joins the stuck list instead (round 3 FU-1).
class UncertainCard {
  const UncertainCard({
    required this.signal,
    required this.since,
    required this.sinceAt,
    this.resolved,
  });

  final SwingSignal signal;

  /// The newest fetch generation started when the 202 arrived: only a fetch
  /// begun after it may settle the card.
  final int since;

  /// When the 202 arrived (device clock).
  final DateTime sinceAt;

  /// null while waiting, then "submitted" or "failed".
  final String? resolved;

  String get badge => resolved ?? uncertainBadge;

  /// Round 3 FU-1: never without an action. Dismiss once settled, or once
  /// [stuckAfter] has passed since the 202.
  bool canDismiss(DateTime now) =>
      resolved != null || now.difference(sinceAt) > stuckAfter;

  UncertainCard settled(String status) => UncertainCard(
      signal: signal, since: since, sinceAt: sinceAt, resolved: status);
}

class PendingSignalsState {
  const PendingSignalsState({
    this.signals = const [],
    this.stuck = const [],
    this.uncertain = const [],
    this.deciding = const {},
    this.resending = const {},
    this.refreshError,
    this.asOf,
  });

  final List<SwingSignal> signals;

  /// 202'd approvals and re-sends, waiting for the broker (follow-up 2).
  final List<UncertainCard> uncertain;

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
    List<UncertainCard>? uncertain,
    Set<String>? deciding,
    Set<String>? resending,
    String? refreshError,
    bool clearRefreshError = false,
    DateTime? asOf,
  }) =>
      PendingSignalsState(
        signals: signals ?? this.signals,
        stuck: stuck ?? this.stuck,
        uncertain: uncertain ?? this.uncertain,
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

  /// id -> the waiting card after a 202 (follow-up 2). It outlives
  /// pull-to-refresh: only a poll that settles it, or Dismiss, removes it.
  final Map<String, UncertainCard> _uncertain = <String, UncertainCard>{};

  /// Stuck ids the operator dismissed on this device (round 3 FU-1). A new
  /// decision or 202 on the id forgets it (round 4, R3-2).
  final Set<String> _dismissedStuck = <String>{};

  /// Ids that left the waiting state for the stuck list. While they still
  /// read approved they skip the server-clock age check, so a device clock
  /// behind the server cannot hide them between polls (round 4, R3-1).
  final Set<String> _joinedStuck = <String>{};

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
    final load = await _fetch();
    // No pending list at all yet: the section shows the error with Retry. A
    // failed approved read alone does not stop it (follow-up 4).
    if (load.pending == null) throw load.error!;
    if (generation > _applied) _applied = generation;
    final first = _apply(const PendingSignalsState(), generation, load);
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

  /// The pending list and the approved lists, requested together and
  /// settled independently (follow-up 4): a failed read leaves its list null
  /// and never stops the other. The approved ones feed
  /// [PendingSignalsState.stuck] and end a hide.
  Future<_Load> _fetch() async {
    final repo = ref.read(swingRepositoryProvider);
    Future<(List<SwingSignal>?, Object?)> settle(
        Future<List<SwingSignal>> read) async {
      try {
        return (await read, null);
      } catch (err) {
        return (null, err);
      }
    }

    final pendingRead = settle(repo.pendingSignals(arg));
    final approvedRead = settle(repo.approvedSignals(arg));
    // While a card waits (follow-up 2), the submitted and failed lists too.
    final waiting = _uncertain.values.any((c) => c.resolved == null);
    final submittedRead =
        waiting ? settle(repo.signalsWithStatus(arg, 'submitted')) : null;
    final failedRead = waiting ? settle(repo.signalsWithStatus(arg, 'failed')) : null;
    final (pending, pendingError) = await pendingRead;
    final (approved, approvedError) = await approvedRead;
    final (submitted, submittedError) = await (submittedRead ?? Future.value((null, null)));
    final (failed, failedError) = await (failedRead ?? Future.value((null, null)));
    return _Load(pending, approved,
        pendingError ?? approvedError ?? submittedError ?? failedError,
        submitted: submitted, failed: failed);
  }

  /// Follow-up 2: settle the waiting cards a fetch begun after their 202 can
  /// speak for. Pending: the card goes and the pending card is back.
  /// Submitted or failed: the badge says so until Dismiss. Round 3 FU-1:
  /// still approved more than [stuckAfter] after the 202, the card leaves
  /// the waiting state and joins the stuck list (the returned ids), where
  /// Re-send and Dismiss are. Anything else, or a failed read, leaves it
  /// waiting.
  Set<String> _foldUncertain(int generation, _Load load, DateTime now) {
    Set<String>? ids(List<SwingSignal>? rows) => rows?.map((s) => s.id).toSet();
    final pending = ids(load.pending);
    final submitted = ids(load.submitted);
    final failed = ids(load.failed);
    final approved = ids(load.approved) ?? const <String>{};
    final joinedStuck = <String>{};
    for (final id in _uncertain.keys.toList()) {
      final card = _uncertain[id]!;
      if (card.resolved != null || generation <= card.since) continue;
      if (pending?.contains(id) ?? false) {
        _uncertain.remove(id);
      } else if (submitted?.contains(id) ?? false) {
        _uncertain[id] = card.settled('submitted');
      } else if (failed?.contains(id) ?? false) {
        _uncertain[id] = card.settled('failed');
      } else if (approved.contains(id) &&
          now.difference(card.sinceAt) > stuckAfter) {
        _uncertain.remove(id);
        joinedStuck.add(id);
      }
    }
    return joinedStuck;
  }

  /// A failed read keeps its part of [current]; the first error is shown.
  PendingSignalsState _apply(
      PendingSignalsState current, int generation, _Load load) {
    final now = ref.read(swingClockProvider)();
    final pending = load.pending;
    final approved = load.approved;
    _joinedStuck.addAll(_foldUncertain(generation, load, now));
    if (approved != null) {
      final ids = approved.map((s) => s.id).toSet();
      _joinedStuck.retainWhere(ids.contains);
    }
    // Only this device's re-send snooze (device clock) still applies to a
    // joined card.
    bool snoozed(String id) {
      final at = _resentAt[id];
      return at != null && now.difference(at) <= stuckAfter;
    }

    final base =
        approved == null ? current.stuck : stuckApprovals(approved, now, _resentAt);
    final listed = base.map((s) => s.id).toSet();
    final next = current.copyWith(
      signals: pending == null
          ? current.signals
          : _visible(generation, pending,
              nonPending: (approved ?? const []).map((s) => s.id)),
      // A card that just left waiting is stuck now, whatever its server-clock
      // age. An uncertain card is never also a stuck one, and a dismissed one
      // stays off.
      stuck: [
        ...base,
        ...(approved ?? const <SwingSignal>[]).where((s) =>
            _joinedStuck.contains(s.id) &&
            !listed.contains(s.id) &&
            !snoozed(s.id)),
      ]
          .where((s) =>
              !_uncertain.containsKey(s.id) && !_dismissedStuck.contains(s.id))
          .toList(),
      uncertain: _uncertain.values.toList(),
      asOf: now,
      clearRefreshError: true,
    );
    final error = load.error;
    return error == null ? next : next.copyWith(refreshError: error.toString());
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
    final load = await _fetch();
    if (generation < _applied) return;
    final current = state.valueOrNull;
    if (current == null) return;
    if (load.pending == null && load.approved == null) {
      state = AsyncData(current.copyWith(refreshError: load.error.toString()));
      return;
    }
    _applied = generation;
    state = AsyncData(_apply(current, generation, load));
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
      _forgetStuck(signal.id); // R3-2: a new decision forgets an old dismissal
      _drop(signal.id);
      if (receipt.uncertain) {
        final message =
            receipt.detail.isEmpty ? uncertainMessage() : receipt.detail;
        _wait(signal);
        return DecisionResult(DecisionOutcome.uncertain, message);
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
        final message = receipt.detail.isEmpty
            ? uncertainMessage('Re-send')
            : receipt.detail;
        _wait(signal);
        return DecisionResult(DecisionOutcome.uncertain, message);
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

  /// Round 4 (R3-2): a new decision or 202 on [id] starts it afresh.
  void _forgetStuck(String id) {
    _dismissedStuck.remove(id);
    _joinedStuck.remove(id);
  }

  /// Follow-up 2: a 202 puts the signal on a waiting card.
  void _wait(SwingSignal signal) {
    _forgetStuck(signal.id);
    _uncertain[signal.id] = UncertainCard(
        signal: signal,
        since: _generation,
        sinceAt: ref.read(swingClockProvider)());
    final current = state.valueOrNull ?? const PendingSignalsState();
    state = AsyncData(current.copyWith(
      uncertain: _uncertain.values.toList(),
      stuck: current.stuck.where((s) => s.id != signal.id).toList(),
    ));
  }

  /// Round 3 FU-1: takes a stuck card off for good on this device.
  void dismissStuck(String id) {
    _dismissedStuck.add(id);
    final current = state.valueOrNull ?? const PendingSignalsState();
    state = AsyncData(
        current.copyWith(stuck: current.stuck.where((s) => s.id != id).toList()));
  }

  /// Removes a waiting card (its Dismiss button).
  void dismissUncertain(String id) {
    _uncertain.remove(id);
    final current = state.valueOrNull ?? const PendingSignalsState();
    state = AsyncData(current.copyWith(uncertain: _uncertain.values.toList()));
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

/// One poll's reads, each settled on its own (follow-up 4): null when that
/// read failed; [error] is the first failure.
class _Load {
  const _Load(this.pending, this.approved, this.error,
      {this.submitted, this.failed});
  final List<SwingSignal>? pending;
  final List<SwingSignal>? approved;
  final Object? error;

  /// Read only while a card is uncertain (follow-up 2).
  final List<SwingSignal>? submitted;
  final List<SwingSignal>? failed;
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
