import 'package:flutter_riverpod/flutter_riverpod.dart';
import '../../../core/formatters/formatters.dart';
import '../../../core/network/api_client.dart';
import '../../../core/polling/poller.dart';
import '../../live_trading/data/live_repository.dart';
import '../../live_trading/data/models/live_state.dart';

typedef StockSeriesArgs = ({String symbol, String range});
typedef StockSeries = ({List<DateTime> ts, List<double> vals});

/// A symbol's price series for a range, kept LIVE: polls every 10 s on 1D
/// (30 s on longer ranges), lifecycle-aware, keeping the last good data on a
/// transient failure. For 1D the points are trimmed to local midnight.
class StockHistoryNotifier
    extends AutoDisposeFamilyAsyncNotifier<StockSeries, StockSeriesArgs> {
  IntervalPoller? _poller;

  @override
  Future<StockSeries> build(StockSeriesArgs arg) async {
    final lifecycle = ref.read(appLifecycleProvider);
    final data = await _fetch(arg);
    _poller = IntervalPoller(
      fetch: () => _refresh(arg),
      interval: () => arg.range == '1D'
          ? const Duration(seconds: 10)
          : const Duration(seconds: 30),
    );
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
    ref.onDispose(() => _poller?.dispose());
    return data;
  }

  Future<StockSeries> _fetch(StockSeriesArgs arg) async {
    final map = await ref
        .read(liveRepositoryProvider)
        .symbolHistoricals([arg.symbol], arg.range);
    final pts = map[arg.symbol] ?? const [];
    DateTime? midnight;
    if (arg.range == '1D') {
      final n = DateTime.now();
      midnight = DateTime(n.year, n.month, n.day);
    }
    // Build the full series and (for 1D) the since-midnight slice in one pass.
    final allTs = <DateTime>[];
    final allVals = <double>[];
    final dayTs = <DateTime>[];
    final dayVals = <double>[];
    for (final p in pts) {
      final t = parseDateTime(p.ts);
      if (t == null) continue;
      allTs.add(t);
      allVals.add(p.value);
      if (midnight != null && !t.isBefore(midnight)) {
        dayTs.add(t);
        dayVals.add(p.value);
      }
    }
    // 1D = since midnight, but right after 12 AM / pre-market there may be no
    // post-midnight bars yet → fall back to the full series so price + chart
    // still render instead of getting stuck on the skeleton.
    if (midnight != null && dayVals.length >= 2) {
      return (ts: dayTs, vals: dayVals);
    }
    return (ts: allTs, vals: allVals);
  }

  Future<void> _refresh(StockSeriesArgs arg) async {
    try {
      state = AsyncData(await _fetch(arg));
    } catch (_) {
      // keep the last good series on a transient poll failure
    }
  }
}

final stockHistoryProvider = AutoDisposeAsyncNotifierProviderFamily<
    StockHistoryNotifier, StockSeries, StockSeriesArgs>(
  StockHistoryNotifier.new,
);

/// Display info/stats for a symbol (name, sector, market cap, P/E, 52-week
/// range, recommendation, summary…) from the backend's yfinance endpoint.
/// Returns an empty map — never throws — so the screen renders without it.
final stockInfoProvider =
    FutureProvider.autoDispose.family<Map<String, dynamic>, String>(
  (ref, symbol) async {
    try {
      return await ref
          .read(apiClientProvider)
          .get<Map<String, dynamic>>('/symbols/$symbol/info');
    } catch (_) {
      return const {};
    }
  },
);

/// One contributing strategy behind a bot decision (who else agreed).
class BotContributor {
  const BotContributor({this.strategy, this.weight, this.reason});
  final String? strategy;
  final double? weight;
  final String? reason;

  factory BotContributor.fromJson(Map<String, dynamic> j) => BotContributor(
        strategy: j['strategy']?.toString(),
        weight: (j['weight'] as num?)?.toDouble(),
        reason: j['reason']?.toString(),
      );
}

/// One real buy/sell the bot made for a symbol, with the reasoning that drove
/// it — from the backend's `BotTradeDecisions` log (per instance/brokerage).
class BotTradeEvent {
  const BotTradeEvent({
    required this.symbol,
    required this.side,
    this.ts,
    this.price,
    this.strategy,
    this.actionIntent,
    this.reason = '',
    this.score,
    this.overrideApplied = false,
    this.contributors = const [],
  });

  final String symbol;
  final String side; // "buy" | "sell"
  final DateTime? ts;
  final double? price;
  final String? strategy;
  final String? actionIntent;
  final String reason;
  final double? score;
  final bool overrideApplied;
  final List<BotContributor> contributors;

  bool get isBuy => side.toLowerCase() == 'buy';

  factory BotTradeEvent.fromJson(Map<String, dynamic> j) => BotTradeEvent(
        symbol: (j['symbol'] ?? '').toString(),
        side: (j['side'] ?? 'buy').toString(),
        // Fall back to created_at when ts is missing/null/blank/unparseable.
        ts: parseDateTime(j['ts']) ?? parseDateTime(j['created_at']),
        price: (j['price'] as num?)?.toDouble(),
        strategy: j['strategy']?.toString(),
        actionIntent: j['action_intent']?.toString(),
        reason: (j['reason'] ?? '').toString(),
        score: (j['score'] as num?)?.toDouble(),
        overrideApplied: j['override_applied'] == true,
        contributors: (j['contributors'] as List? ?? const [])
            .whereType<Map<String, dynamic>>()
            .map(BotContributor.fromJson)
            .toList(),
      );
}

/// The bot's actual buy/sell decisions for a symbol on a brokerage (newest
/// first), with reasoning. Reads the backend `BotTradeDecisions` log that live
/// instances write on each confirmed trade. Empty/never-throws so the screen
/// degrades gracefully if the backend isn't deployed yet or nothing's logged.
typedef StockBotActivityArgs = ({String brokerageId, String symbol});

final stockBotActivityProvider =
    FutureProvider.autoDispose.family<List<BotTradeEvent>, StockBotActivityArgs>(
  (ref, args) async {
    try {
      final data = await ref.read(apiClientProvider).get<Map<String, dynamic>>(
        '/brokerages/${args.brokerageId}/bot-activity',
        query: {'symbol': args.symbol, 'per_page': 20},
      );
      final list = (data['events'] as List? ?? const []);
      return list
          .whereType<Map<String, dynamic>>()
          .map(BotTradeEvent.fromJson)
          .toList();
    } catch (_) {
      return const [];
    }
  },
);

/// Recent fills for a symbol on a brokerage (newest first). Returns an empty
/// list — never throws — so the screen degrades gracefully if the backend
/// orders endpoint isn't deployed yet.
typedef StockOrdersArgs = ({String brokerageId, String symbol});

final stockOrdersProvider =
    FutureProvider.autoDispose.family<List<Trade>, StockOrdersArgs>(
  (ref, args) async {
    try {
      final data = await ref.read(apiClientProvider).get<Map<String, dynamic>>(
        '/brokerages/${args.brokerageId}/orders',
        query: {'symbol': args.symbol},
      );
      final list = (data['orders'] as List? ?? const []);
      return list
          .whereType<Map<String, dynamic>>()
          .map(Trade.fromJson)
          .toList();
    } catch (_) {
      return const [];
    }
  },
);
