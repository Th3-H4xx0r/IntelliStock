import 'dart:async';

import 'package:flutter_riverpod/flutter_riverpod.dart';
import '../../../core/network/api_client.dart';
import '../data/dashboard_repository.dart';
import '../data/insight_models.dart';
import 'account_positions_controller.dart';
import 'portfolio_analytics.dart';

/// Session-level symbol → sector cache. Sectors are effectively static, so we
/// only ever hit /symbols/{s}/info once per symbol per app run, no matter how
/// often the allocation card rebuilds or which account is selected.
final Map<String, String?> _sectorCache = {};

/// Discover-engine opportunities for an account. Empty/never-throws so the card
/// simply hides if the backend isn't deployed or there's nothing flagged.
final discoveredProvider =
    FutureProvider.autoDispose.family<List<DiscoveredStock>, String>(
  (ref, brokerageId) async {
    try {
      final data = await ref
          .read(apiClientProvider)
          .get<Map<String, dynamic>>('/brokerages/$brokerageId/discovered');
      final list = (data['stocks'] as List? ?? const []);
      return list
          .whereType<Map<String, dynamic>>()
          .map(DiscoveredStock.fromJson)
          .toList();
    } catch (_) {
      return const [];
    }
  },
);

/// Detected market trends for an account. Empty/never-throws.
final trendsProvider =
    FutureProvider.autoDispose.family<List<MarketTrend>, String>(
  (ref, brokerageId) async {
    try {
      final data = await ref
          .read(apiClientProvider)
          .get<Map<String, dynamic>>('/brokerages/$brokerageId/trends');
      final list = (data['trends'] as List? ?? const []);
      return list
          .whereType<Map<String, dynamic>>()
          .map(MarketTrend.fromJson)
          .toList();
    } catch (_) {
      return const [];
    }
  },
);

/// Today's account change ($ and %), relative to local midnight — independent
/// of the chart's selected range. Null when unavailable.
typedef DayChange = ({double abs, double? pct});

final dayChangeProvider =
    FutureProvider.autoDispose.family<DayChange?, String>(
  (ref, brokerageId) async {
    try {
      final h = (await ref
              .read(dashboardRepositoryProvider)
              .portfolioHistory(brokerageId, '1D'))
          .sinceLocalMidnight();
      if (h.isEmpty) return null;
      return (abs: h.changeAbs ?? 0.0, pct: h.changePct);
    } catch (_) {
      return null;
    }
  },
);

/// Portfolio concentration / diversification for an account's holdings.
final concentrationProvider =
    FutureProvider.autoDispose.family<ConcentrationStats, String>(
  (ref, brokerageId) async {
    try {
      final holdings =
          await ref.read(accountHoldingsProvider(brokerageId).future);
      return concentration(
          [for (final p in holdings.positions) p.marketValue]);
    } catch (_) {
      return const ConcentrationStats(topWeight: 0, count: 0, hhi: 0, score: 0);
    }
  },
);

/// Sector allocation: fetch each holding's sector from /symbols/{s}/info (in
/// parallel) and aggregate by value. Never-throws; unknown sectors fold into
/// "Other". (A cached backend /allocation endpoint is the Phase-2 optimization.)
final sectorAllocationProvider =
    FutureProvider.autoDispose.family<List<SectorSlice>, String>(
  (ref, brokerageId) async {
    // Keep the computed allocation alive across scroll/dispose so we don't
    // re-run the per-symbol fan-out every time the card leaves and re-enters
    // the viewport.
    ref.keepAlive();
    try {
      final holdings =
          await ref.read(accountHoldingsProvider(brokerageId).future);
      final positions = holdings.positions
          .where((p) => p.symbol.isNotEmpty && p.marketValue > 0)
          .toList();
      if (positions.isEmpty) return const [];
      final client = ref.read(apiClientProvider);
      // Only fetch sectors we haven't already resolved this session; each call
      // is time-bounded so one slow symbol can't hang the whole card.
      final misses = [
        for (final p in positions)
          if (!_sectorCache.containsKey(p.symbol)) p.symbol
      ];
      await Future.wait(misses.map((sym) async {
        try {
          final info = await client
              .get<Map<String, dynamic>>('/symbols/$sym/info')
              .timeout(const Duration(seconds: 8));
          _sectorCache[sym] = info['sector'] as String?;
        } catch (_) {
          _sectorCache[sym] = null;
        }
      }));
      final valueBySymbol = {for (final p in positions) p.symbol: p.marketValue};
      final sectors = {for (final p in positions) p.symbol: _sectorCache[p.symbol]};
      return aggregateBySector(valueBySymbol, sectors);
    } catch (_) {
      return const [];
    }
  },
);

/// Holdings ranked by today's % move, derived from the 1D sparklines already
/// fetched for the holdings list. Never-throws.
final todaysMoversProvider =
    FutureProvider.autoDispose.family<List<Mover>, String>(
  (ref, brokerageId) async {
    try {
      final sparks = await ref.read(holdingsSparklinesProvider(
        (brokerageId: brokerageId, range: '1D'),
      ).future);
      final pct = <String, double>{};
      sparks.forEach((sym, vals) {
        if (vals.length >= 2 && vals.first != 0) {
          pct[sym] = (vals.last / vals.first - 1) * 100;
        }
      });
      return todaysMovers(pct);
    } catch (_) {
      return const [];
    }
  },
);
