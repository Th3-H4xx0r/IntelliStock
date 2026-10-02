import 'package:flutter_riverpod/flutter_riverpod.dart';
import '../../../core/polling/poller.dart';
import '../data/dashboard_repository.dart';

// ── Services polling notifier ─────────────────────────────────────────────────

/// Polls all service-status endpoints every 10 s; surfaces a [ServicesSnapshot].
class DashboardServicesNotifier
    extends PollingNotifier<ServicesSnapshot> {
  @override
  Future<ServicesSnapshot> fetch() =>
      ref.read(dashboardRepositoryProvider).services();

  @override
  Duration interval() => const Duration(seconds: 10);
}

final dashboardServicesProvider =
    AutoDisposeAsyncNotifierProvider<DashboardServicesNotifier, ServicesSnapshot>(
  DashboardServicesNotifier.new,
);

// ── Busy / in-flight tracking per engine ─────────────────────────────────────

/// Tracks which engine ids currently have an action in-flight.
/// Keys: 'price_engine', 'discover_engine', 'ai_backtest_engine',
///       'daily_digest_engine', 'nexus_graph_engine'.
class EngineBusyNotifier extends AutoDisposeNotifier<Set<String>> {
  @override
  Set<String> build() => const {};

  bool isBusy(String id) => state.contains(id);

  /// Runs [action], marks [id] busy for the duration, then refreshes services.
  Future<void> run(String id, Future<void> Function() action) async {
    if (state.contains(id)) return;
    state = {...state, id};
    try {
      await action();
      // Refresh so the new status appears immediately.
      await ref.read(dashboardServicesProvider.notifier).refreshNow();
    } catch (_) {
      // Surface errors via AsyncValue in services provider; keep going.
    } finally {
      state = state.difference({id});
    }
  }
}

final engineBusyProvider =
    AutoDisposeNotifierProvider<EngineBusyNotifier, Set<String>>(
  EngineBusyNotifier.new,
);

// ── Brokerages (one-shot, re-fetched on demand) ───────────────────────────────

/// Loads the brokerage account list once; exposed as an AsyncValue.
/// Pull-to-refresh or retry calls [ref.invalidate(brokeragesProvider)].
class BrokeragesNotifier
    extends AutoDisposeAsyncNotifier<List<BrokerageAccount>> {
  @override
  Future<List<BrokerageAccount>> build() =>
      ref.read(dashboardRepositoryProvider).brokerages();
}

final brokeragesProvider = AutoDisposeAsyncNotifierProvider<BrokeragesNotifier,
    List<BrokerageAccount>>(
  BrokeragesNotifier.new,
);

// ── Portfolio freshness ───────────────────────────────────────────────────────

/// Wall-clock time of the last *successful* portfolio-history fetch on the
/// dashboard. Stamped by the history notifier; read by the freshness label.
final portfolioUpdatedAtProvider = StateProvider<DateTime?>((ref) => null);
