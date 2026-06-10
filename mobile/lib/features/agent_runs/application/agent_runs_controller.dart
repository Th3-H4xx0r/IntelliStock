import 'dart:async';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import '../../../core/polling/poller.dart';
import '../data/agent_repository.dart';

// ── State ─────────────────────────────────────────────────────────────────────

class AgentRunsState {
  const AgentRunsState({
    this.runs = const [],
    this.total = 0,
    this.totalPages = 1,
    this.page = 1,
    this.perPage = 20,
    this.control = const AgentControl(),
    this.busy = false,
    this.errorMessage,
    // Countdown timer state
    this.scheduledResumeAt,
    this.scheduledTotalMs = 0,
  });

  final List<AgentRun> runs;
  final int total;
  final int totalPages;
  final int page;
  final int perPage;
  final AgentControl control;
  final bool busy;
  final String? errorMessage;

  /// When non-null, the user has set a scheduled resume countdown.
  final DateTime? scheduledResumeAt;
  final int scheduledTotalMs;

  /// Fractional progress (0.0 – 1.0) of the elapsed countdown time.
  double get countdownFraction {
    if (scheduledResumeAt == null || scheduledTotalMs == 0) return 0;
    final remaining =
        scheduledResumeAt!.difference(DateTime.now()).inMilliseconds;
    final elapsed = scheduledTotalMs - remaining.clamp(0, scheduledTotalMs);
    return (elapsed / scheduledTotalMs).clamp(0.0, 1.0);
  }

  /// Seconds remaining in the countdown.
  int get countdownSecsRemaining {
    if (scheduledResumeAt == null) return 0;
    return scheduledResumeAt!
        .difference(DateTime.now())
        .inSeconds
        .clamp(0, scheduledTotalMs ~/ 1000);
  }

  AgentRunsState copyWith({
    List<AgentRun>? runs,
    int? total,
    int? totalPages,
    int? page,
    int? perPage,
    AgentControl? control,
    bool? busy,
    Object? errorMessage = _sentinel,
    Object? scheduledResumeAt = _sentinel,
    int? scheduledTotalMs,
  }) =>
      AgentRunsState(
        runs: runs ?? this.runs,
        total: total ?? this.total,
        totalPages: totalPages ?? this.totalPages,
        page: page ?? this.page,
        perPage: perPage ?? this.perPage,
        control: control ?? this.control,
        busy: busy ?? this.busy,
        errorMessage: errorMessage == _sentinel
            ? this.errorMessage
            : errorMessage as String?,
        scheduledResumeAt: scheduledResumeAt == _sentinel
            ? this.scheduledResumeAt
            : scheduledResumeAt as DateTime?,
        scheduledTotalMs: scheduledTotalMs ?? this.scheduledTotalMs,
      );
}

const _sentinel = Object();

// ── Controller ────────────────────────────────────────────────────────────────

class AgentRunsController extends PollingNotifier<AgentRunsState> {
  Timer? _countdownTimer;

  @override
  Future<AgentRunsState> build() async {
    ref.onDispose(() => _countdownTimer?.cancel());
    return super.build();
  }

  @override
  Future<AgentRunsState> fetch() async {
    final prev = state.valueOrNull ?? const AgentRunsState();
    final repo = ref.read(agentRepositoryProvider);
    final results = await Future.wait([
      repo.runs(page: prev.page, perPage: prev.perPage),
      repo.control(),
    ]);
    final page = results[0] as AgentRunsPage;
    final ctrl = results[1] as AgentControl;

    // If agent resumed externally while our timer was running, cancel it.
    if (!ctrl.isPaused && prev.scheduledResumeAt != null) {
      _cancelCountdown();
    }

    return prev.copyWith(
      runs: page.runs,
      total: page.total,
      totalPages: page.totalPages,
      page: page.page,
      control: ctrl,
      errorMessage: null,
    );
  }

  @override
  Duration interval() => const Duration(seconds: 5);

  // ── Control actions ──────────────────────────────────────────────────────────

  Future<void> startAgent({String? specialRequest}) async {
    await _controlAction(() async {
      await ref.read(agentRepositoryProvider).setControl(
            running: true,
            specialRequest: specialRequest,
          );
    });
  }

  Future<void> pauseAgent() async {
    await _controlAction(() async {
      await ref.read(agentRepositoryProvider).setControl(paused: true);
    });
  }

  Future<void> resumeAgentNow() async {
    _cancelCountdown();
    await _controlAction(() async {
      await ref.read(agentRepositoryProvider).setControl(paused: false);
    });
  }

  Future<void> stopAgent() async {
    _cancelCountdown();
    await _controlAction(() async {
      await ref.read(agentRepositoryProvider).setControl(running: false);
    });
  }

  /// Schedule an automatic resume after [minutes] minutes. 0 = resume now.
  Future<void> scheduleResume(int minutes) async {
    if (minutes == 0) {
      await resumeAgentNow();
      return;
    }
    final durationMs = minutes * 60 * 1000;
    final resumeAt = DateTime.now().add(Duration(milliseconds: durationMs));

    if (state case AsyncData(:final value)) {
      state = AsyncData(value.copyWith(
        scheduledResumeAt: resumeAt,
        scheduledTotalMs: durationMs,
      ));
    }

    _startCountdown(resumeAt, durationMs);
  }

  void _startCountdown(DateTime resumeAt, int totalMs) {
    _countdownTimer?.cancel();
    _countdownTimer = Timer.periodic(const Duration(seconds: 1), (_) async {
      if (state case AsyncData(:final value)) {
        if (value.scheduledResumeAt == null) {
          _countdownTimer?.cancel();
          return;
        }
        // Tick — state rebuild causes UI to re-read countdownFraction etc.
        state = AsyncData(value.copyWith());

        final remaining = resumeAt.difference(DateTime.now()).inMilliseconds;
        if (remaining <= 0) {
          _cancelCountdown();
          await resumeAgentNow();
        }
      }
    });
  }

  void cancelCountdown() => _cancelCountdown();

  void _cancelCountdown() {
    _countdownTimer?.cancel();
    _countdownTimer = null;
    if (state case AsyncData(:final value)) {
      state = AsyncData(value.copyWith(
        scheduledResumeAt: null,
        scheduledTotalMs: 0,
      ));
    }
  }

  Future<void> forceStop(String logId) async {
    await _controlAction(
      () async => ref.read(agentRepositoryProvider).forceStop(logId),
    );
  }

  Future<void> goToPage(int page) async {
    if (state case AsyncData(:final value)) {
      state = AsyncData(value.copyWith(page: page));
      await refreshNow();
    }
  }

  Future<void> setPerPage(int perPage) async {
    if (state case AsyncData(:final value)) {
      state = AsyncData(value.copyWith(perPage: perPage, page: 1));
      await refreshNow();
    }
  }

  Future<void> _controlAction(Future<void> Function() action) async {
    if (state case AsyncData(:final value)) {
      state = AsyncData(value.copyWith(busy: true, errorMessage: null));
      try {
        await action();
      } catch (e) {
        if (state case AsyncData(:final value)) {
          state = AsyncData(
              value.copyWith(busy: false, errorMessage: e.toString()));
        }
        return;
      }
      await refreshNow();
    }
  }
}

final agentRunsControllerProvider =
    AutoDisposeAsyncNotifierProvider<AgentRunsController, AgentRunsState>(
  AgentRunsController.new,
);
