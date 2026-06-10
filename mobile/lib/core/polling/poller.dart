import 'dart:async';
import 'package:flutter/widgets.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

/// A self-scheduling interval poller.
///
/// The interval is re-read every cycle, so cadence can adapt to state
/// (e.g. 3s while trading is active, 10s while idle). Pauses when the app is
/// backgrounded and resumes on foreground. The first fetch is the caller's
/// responsibility (notifiers do it in `build`); this drives subsequent ones.
class IntervalPoller {
  IntervalPoller({required this.fetch, required this.interval});

  /// Called each cycle. Errors are swallowed so the loop keeps running.
  final Future<void> Function() fetch;

  /// Re-evaluated each cycle to pick the next delay.
  final Duration Function() interval;

  Timer? _timer;
  bool _paused = false;
  bool _disposed = false;
  bool _running = false;

  void start() => _schedule();

  void _schedule() {
    _timer?.cancel();
    if (_disposed || _paused) return;
    _timer = Timer(interval(), _tick);
  }

  Future<void> _tick() async {
    if (_disposed || _paused || _running) {
      if (!_disposed && !_paused) _schedule();
      return;
    }
    _running = true;
    try {
      await fetch();
    } catch (_) {
      // Keep polling; the notifier surfaces errors via its own state.
    } finally {
      _running = false;
      _schedule();
    }
  }

  void pause() {
    _paused = true;
    _timer?.cancel();
  }

  void resume() {
    if (!_paused) return;
    _paused = false;
    _schedule();
  }

  void dispose() {
    _disposed = true;
    _timer?.cancel();
  }
}

/// Tracks the app lifecycle so pollers can pause in the background.
class AppLifecycleNotifier extends ChangeNotifier with WidgetsBindingObserver {
  AppLifecycleNotifier() {
    WidgetsBinding.instance.addObserver(this);
  }

  AppLifecycleState state = AppLifecycleState.resumed;
  DateTime? lastPausedAt;

  bool get isForeground => state == AppLifecycleState.resumed;

  @override
  void didChangeAppLifecycleState(AppLifecycleState state) {
    this.state = state;
    if (state == AppLifecycleState.paused ||
        state == AppLifecycleState.inactive) {
      lastPausedAt = DateTime.now();
    }
    notifyListeners();
  }

  @override
  void dispose() {
    WidgetsBinding.instance.removeObserver(this);
    super.dispose();
  }
}

final appLifecycleProvider = ChangeNotifierProvider<AppLifecycleNotifier>(
  (ref) => AppLifecycleNotifier(),
);

/// Base class for screens that poll an endpoint on an interval.
///
/// Subclasses implement [fetch] (returns fresh state) and [interval] (cadence,
/// may read `state`). The poller pauses automatically when the app backgrounds.
abstract class PollingNotifier<T> extends AutoDisposeAsyncNotifier<T> {
  IntervalPoller? _poller;

  Future<T> fetch();
  Duration interval();

  @override
  Future<T> build() async {
    final lifecycle = ref.watch(appLifecycleProvider);
    final data = await fetch();

    _poller = IntervalPoller(
      fetch: _refresh,
      interval: interval,
    );
    if (lifecycle.isForeground) {
      _poller!.start();
    } else {
      _poller!.pause();
    }
    // React to lifecycle changes.
    ref.listen(appLifecycleProvider, (_, next) {
      if (next.isForeground) {
        _poller?.resume();
      } else {
        _poller?.pause();
      }
    });
    ref.onDispose(() => _poller?.dispose());
    return data;
  }

  Future<void> _refresh() async {
    try {
      final data = await fetch();
      state = AsyncData(data);
    } catch (e, st) {
      state = AsyncError(e, st);
    }
  }

  /// Force a refresh now (e.g. pull-to-refresh / manual button).
  Future<void> refreshNow() => _refresh();
}
