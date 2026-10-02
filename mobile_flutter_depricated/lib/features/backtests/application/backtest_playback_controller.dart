import 'dart:async';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import '../data/backtest_repository.dart';
import '../data/models/backtest.dart';

const List<double> kPlaybackSpeeds = [0.5, 1, 2, 5, 10];

class BacktestPlaybackState {
  const BacktestPlaybackState({
    this.events = const [],
    this.metadata = const PlaybackMetadata(),
    this.frameIndex = -1,
    this.isPlaying = false,
    this.speedIndex = 1, // default 1x
    this.loading = true,
    this.error,
  });

  final List<PlaybackEvent> events;
  final PlaybackMetadata metadata;
  final int frameIndex;
  final bool isPlaying;
  final int speedIndex;
  final bool loading;
  final String? error;

  double get speed => kPlaybackSpeeds[speedIndex];
  bool get isFinished => frameIndex >= events.length - 1;
  bool get isEmpty => !loading && events.isEmpty;

  /// All events up to and including the current frame.
  List<PlaybackEvent> get visibleEvents =>
      frameIndex >= 0 ? events.sublist(0, frameIndex + 1) : const [];

  /// Last portfolio event at or before frameIndex.
  PlaybackEvent? get currentPortfolioEvent {
    for (int i = frameIndex; i >= 0; i--) {
      if (events[i].type == 'portfolio') return events[i];
    }
    return null;
  }

  num get currentPortfolioValue =>
      currentPortfolioEvent?.portfolioValue ??
      metadata.initialCash ??
      100000;

  List<PlaybackHolding> get currentHoldings =>
      currentPortfolioEvent?.holdings ?? const [];

  /// Current date label from the most recent 'date' event.
  String get currentDateLabel {
    for (int i = frameIndex; i >= 0; i--) {
      if (events[i].type == 'date') {
        final ev = events[i];
        return '${ev.label ?? ''} — ${ev.time ?? ''}';
      }
    }
    return '—';
  }

  /// All portfolio value data points from the start up to frameIndex.
  List<({DateTime time, double value})> get portfolioHistory {
    final pts = <({DateTime time, double value})>[];
    for (int i = 0; i <= frameIndex && i < events.length; i++) {
      final ev = events[i];
      if (ev.type == 'portfolio' && ev.date != null) {
        final dt = DateTime.tryParse(ev.date!);
        if (dt != null && ev.portfolioValue != null) {
          pts.add((
            time: dt,
            value: ev.portfolioValue!.toDouble(),
          ));
        }
      }
    }
    return pts;
  }

  /// Full x-axis range (for fixed-range chart).
  ({DateTime min, DateTime max}) get xRange {
    DateTime? first;
    DateTime? last;
    for (final ev in events) {
      if (ev.type == 'portfolio' && ev.date != null) {
        final dt = DateTime.tryParse(ev.date!);
        if (dt != null) {
          first ??= dt;
          last = dt;
        }
      }
    }
    final now = DateTime.now();
    return (
      min: first?.subtract(const Duration(days: 1)) ??
          now.subtract(const Duration(days: 7)),
      max: last ?? now,
    );
  }

  BacktestPlaybackState copyWith({
    List<PlaybackEvent>? events,
    PlaybackMetadata? metadata,
    int? frameIndex,
    bool? isPlaying,
    int? speedIndex,
    bool? loading,
    String? error,
    bool clearError = false,
  }) =>
      BacktestPlaybackState(
        events: events ?? this.events,
        metadata: metadata ?? this.metadata,
        frameIndex: frameIndex ?? this.frameIndex,
        isPlaying: isPlaying ?? this.isPlaying,
        speedIndex: speedIndex ?? this.speedIndex,
        loading: loading ?? this.loading,
        error: clearError ? null : (error ?? this.error),
      );
}

class BacktestPlaybackController
    extends AutoDisposeFamilyNotifier<BacktestPlaybackState, String> {
  Timer? _frameTimer;

  @override
  BacktestPlaybackState build(String id) {
    ref.onDispose(_cancelTimer);
    Future.microtask(() => _load(id));
    return const BacktestPlaybackState(loading: true);
  }

  BacktestRepository get _repo => ref.read(backtestRepositoryProvider);

  Future<void> _load(String id) async {
    try {
      final data = await _repo.playbackData(id);
      state = state.copyWith(
        events: data.events,
        metadata: data.metadata,
        frameIndex: -1,
        loading: false,
        clearError: true,
      );
    } catch (e) {
      state = state.copyWith(loading: false, error: e.toString());
    }
  }

  void _cancelTimer() {
    _frameTimer?.cancel();
    _frameTimer = null;
  }

  void _scheduleFrame() {
    _cancelTimer();
    final delay =
        Duration(milliseconds: (1000 / state.speed).round());
    _frameTimer = Timer(delay, _advance);
  }

  void _advance() {
    if (!state.isPlaying) return;
    if (state.isFinished) {
      state = state.copyWith(isPlaying: false);
      return;
    }
    state = state.copyWith(frameIndex: state.frameIndex + 1);
    _scheduleFrame();
  }

  // ── Public controls ───────────────────────────────────────────────────────

  void togglePlay() {
    if (state.isPlaying) {
      _cancelTimer();
      state = state.copyWith(isPlaying: false);
    } else {
      if (state.isFinished) {
        state = state.copyWith(
            frameIndex: state.events.isEmpty ? -1 : 0,
            isPlaying: true);
      } else {
        state = state.copyWith(isPlaying: true);
      }
      _scheduleFrame();
    }
  }

  void reset() {
    _cancelTimer();
    state = state.copyWith(
      frameIndex: state.events.isEmpty ? -1 : 0,
      isPlaying: false,
    );
  }

  void cycleSpeed() {
    final wasPlaying = state.isPlaying;
    if (wasPlaying) _cancelTimer();
    final next = (state.speedIndex + 1) % kPlaybackSpeeds.length;
    state = state.copyWith(speedIndex: next);
    if (wasPlaying) _scheduleFrame();
  }
}

final backtestPlaybackControllerProvider =
    AutoDisposeNotifierProvider.family<BacktestPlaybackController,
        BacktestPlaybackState, String>(
  BacktestPlaybackController.new,
);
