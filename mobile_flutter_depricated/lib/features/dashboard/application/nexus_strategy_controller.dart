import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../data/dashboard_repository.dart';
import '../data/nexus_models.dart';

// These telemetry providers are intentionally keep-alive (NOT autoDispose):
// the dashboard rebuilds and tab navigation would otherwise dispose + re-fetch
// each endpoint repeatedly (observed 2–4× per load). Keep-alive fetches each
// once per app session and reuses it; the data changes on the bot's run cadence
// (minutes), so session-caching is fine. Restart the app to force a refresh.

/// Active + recently-ended trends, fetched together (one provider feeds the
/// Market Trends card and the Reversal Watch card).
final nexusTrendsProvider =
    FutureProvider.family<NexusTrendsView, String>((ref, id) async {
  final repo = ref.read(dashboardRepositoryProvider);
  try {
    final results = await Future.wait([
      repo.nexusTrends(id, status: 'active', limit: 30),
      repo.nexusTrends(id, status: 'ended', limit: 6),
    ]);
    return NexusTrendsView(active: results[0], recentlyEnded: results[1]);
  } catch (_) {
    return const NexusTrendsView(active: [], recentlyEnded: []);
  }
});

final backfillQueueProvider =
    FutureProvider.family<List<BackfillItem>, String>((ref, id) async {
  try {
    return await ref.read(dashboardRepositoryProvider).backfillQueue(id);
  } catch (_) {
    return const [];
  }
});

final discoveredStocksProvider =
    FutureProvider.family<List<DiscoveredStock>, String>((ref, id) async {
  try {
    return await ref.read(dashboardRepositoryProvider).discoveredStocks(id);
  } catch (_) {
    return const [];
  }
});

final tradeContextsProvider =
    FutureProvider.family<List<TradeRationale>, String>((ref, id) async {
  try {
    return await ref.read(dashboardRepositoryProvider).tradeContexts(id);
  } catch (_) {
    return const [];
  }
});

final nexusOutcomesProvider =
    FutureProvider.family<OutcomeStats, String>((ref, id) async {
  try {
    return await ref.read(dashboardRepositoryProvider).nexusOutcomes(id);
  } catch (_) {
    return const OutcomeStats(hitRate: 0, n: 0, nCorrect: 0, avgReturn: 0, recent: []);
  }
});

final momentumWatchlistProvider =
    FutureProvider.family<WatchlistSummary, String>((ref, id) async {
  try {
    return await ref.read(dashboardRepositoryProvider).momentumWatchlist(id);
  } catch (_) {
    return const WatchlistSummary(count: 0, newest: []);
  }
});
