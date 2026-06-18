import 'dart:async';

import 'package:flutter_riverpod/flutter_riverpod.dart';
import '../../../core/formatters/formatters.dart';
import '../../../core/network/api_client.dart';
import '../../live_trading/data/live_repository.dart';
import '../data/dashboard_repository.dart';
import 'account_positions_controller.dart';
import 'portfolio_analytics.dart';

// ── Market data: news, movers, nexus momentum ───────────────────────────────

/// A market news headline from GET /market/news (Google News).
class NewsArticle {
  const NewsArticle(
      {required this.title, this.source = '', this.url = '', this.publishedAt});
  final String title;
  final String source;
  final String url;
  final DateTime? publishedAt;

  factory NewsArticle.fromJson(Map<String, dynamic> j) => NewsArticle(
        title: (j['title'] ?? '').toString(),
        source: (j['source'] ?? '').toString(),
        url: (j['url'] ?? '').toString(),
        publishedAt: parseDateTime(j['published_at']),
      );
}

/// Market/business headlines. Empty/never-throws.
final marketNewsProvider = FutureProvider.autoDispose<List<NewsArticle>>(
  (ref) async {
    try {
      final data = await ref
          .read(apiClientProvider)
          .get<Map<String, dynamic>>('/market/news', query: {'limit': 15});
      return ((data['articles'] as List?) ?? const [])
          .whereType<Map<String, dynamic>>()
          .map(NewsArticle.fromJson)
          .where((a) => a.title.isNotEmpty)
          .toList();
    } catch (_) {
      return const [];
    }
  },
);

typedef MarketMover = ({String symbol, double? pct, double? price});
typedef MoversData = ({List<MarketMover> gainers, List<MarketMover> losers});

/// Market gainers/losers for the account's Alpaca screener. Empty/never-throws.
final marketMoversProvider =
    FutureProvider.autoDispose.family<MoversData, String>(
  (ref, brokerageId) async {
    List<MarketMover> parse(dynamic l) =>
        ((l as List?) ?? const [])
            .whereType<Map<String, dynamic>>()
            .map((m) => (
                  symbol: (m['symbol'] ?? '').toString(),
                  pct: (m['pct'] as num?)?.toDouble(),
                  price: (m['price'] as num?)?.toDouble(),
                ))
            .where((x) => x.symbol.isNotEmpty)
            .toList();
    try {
      final data = await ref.read(apiClientProvider).get<Map<String, dynamic>>(
        '/brokerages/$brokerageId/movers',
        query: {'top': 6},
      );
      return (gainers: parse(data['gainers']), losers: parse(data['losers']));
    } catch (_) {
      return (gainers: const <MarketMover>[], losers: const <MarketMover>[]);
    }
  },
);

typedef MomentumPick = ({String symbol, double score});

/// The nexus strategy's top ranked momentum names for the account's instance.
/// Empty unless that instance runs graph_nexus_analysis with momentum enabled.
final nexusMomentumProvider =
    FutureProvider.autoDispose.family<List<MomentumPick>, String>(
  (ref, brokerageId) async {
    try {
      final data = await ref
          .read(apiClientProvider)
          .get<Map<String, dynamic>>('/brokerages/$brokerageId/nexus-momentum');
      return ((data['momentum'] as List?) ?? const [])
          .whereType<Map<String, dynamic>>()
          .map((m) => (
                symbol: (m['symbol'] ?? '').toString(),
                score: (m['score'] as num?)?.toDouble() ?? 0.0,
              ))
          .where((x) => x.symbol.isNotEmpty)
          .toList();
    } catch (_) {
      return const [];
    }
  },
);

/// Session-level symbol → sector cache. Sectors are effectively static, so we
/// only ever hit /symbols/{s}/info once per symbol per app run, no matter how
/// often the allocation card rebuilds or which account is selected.
final Map<String, String?> _sectorCache = {};

/// A market instrument's today snapshot (index or sector ETF): a display label,
/// today's % move, and the intraday series for a mini sparkline.
class MarketQuote {
  const MarketQuote({
    required this.symbol,
    required this.label,
    required this.pct,
    this.values = const [],
  });
  final String symbol;
  final String label;
  final double pct;
  final List<double> values;
}

/// Major index proxies (liquid ETFs that the historicals endpoint serves like
/// any stock symbol). Order = display order.
const _indexSymbols = <String, String>{
  'SPY': 'S&P 500',
  'QQQ': 'Nasdaq',
  'DIA': 'Dow',
  'IWM': 'Russell 2000',
};

/// The 11 SPDR sector ETFs → sector display names.
const _sectorEtfs = <String, String>{
  'XLK': 'Technology',
  'XLF': 'Financials',
  'XLV': 'Health Care',
  'XLY': 'Consumer Disc.',
  'XLC': 'Communication',
  'XLI': 'Industrials',
  'XLP': 'Consumer Staples',
  'XLE': 'Energy',
  'XLU': 'Utilities',
  'XLRE': 'Real Estate',
  'XLB': 'Materials',
};

Future<List<MarketQuote>> _quotesFor(
  Ref ref,
  Map<String, String> universe, {
  required bool sortByPct,
}) async {
  final hist = await ref
      .read(liveRepositoryProvider)
      .symbolHistoricals(universe.keys.toList(), '1D');
  final out = <MarketQuote>[];
  universe.forEach((sym, label) {
    final pts = hist[sym];
    if (pts == null) return;
    final vals = [for (final p in pts) p.value];
    final pct = pctChangeOf(vals);
    if (pct == null) return;
    out.add(MarketQuote(symbol: sym, label: label, pct: pct, values: vals));
  });
  if (sortByPct) out.sort((a, b) => b.pct.compareTo(a.pct));
  return out;
}

/// Today's move for the major indices (in display order). Never-throws.
final marketIndicesProvider =
    FutureProvider.autoDispose<List<MarketQuote>>((ref) async {
  try {
    return await _quotesFor(ref, _indexSymbols, sortByPct: false);
  } catch (_) {
    return const [];
  }
});

/// Today's sector performance (ranked best → worst). Never-throws.
final sectorPerformanceProvider =
    FutureProvider.autoDispose<List<MarketQuote>>((ref) async {
  try {
    return await _quotesFor(ref, _sectorEtfs, sortByPct: true);
  } catch (_) {
    return const [];
  }
});

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

/// Risk metrics (volatility, max drawdown, Sharpe) from the account's 1Y equity
/// curve. keepAlive — the curve barely moves intraday. Never-throws.
final riskMetricsProvider =
    FutureProvider.autoDispose.family<RiskMetrics, String>(
  (ref, brokerageId) async {
    ref.keepAlive();
    try {
      final h = await ref
          .read(dashboardRepositoryProvider)
          .portfolioHistory(brokerageId, '1Y');
      return riskMetrics(h.values);
    } catch (_) {
      return const RiskMetrics(
          volatility: 0, maxDrawdown: 0, sharpe: null, points: 0);
    }
  },
);
