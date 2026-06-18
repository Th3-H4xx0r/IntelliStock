import 'package:flutter_riverpod/flutter_riverpod.dart';
import '../../../core/formatters/formatters.dart';
import '../../../core/network/api_client.dart';
import '../../../core/polling/poller.dart';
import '../../agent_runs/data/agent_repository.dart';
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

/// Whole-word, regex-escaped matcher for a ticker (so `A` doesn't match inside
/// `AAPL`, and `BRK.B`'s dot is literal). Operate on already-upper-cased text.
RegExp _wordRegex(String sym) =>
    RegExp(r'(?<![A-Z0-9.])' + RegExp.escape(sym) + r'(?![A-Z0-9.])');

/// Recent bot decision cycles that involved this symbol (from the agent cycle
/// log). Each [AgentRun] has stages (with the stocks they touched) + a
/// final-result summary of what the bot decided. Empty/never-throws.
final stockDecisionsProvider =
    FutureProvider.autoDispose.family<List<AgentRun>, String>(
  (ref, symbol) async {
    try {
      final data = await ref.read(apiClientProvider).get<Map<String, dynamic>>(
        '/agent/runs',
        query: {'page': 1, 'per_page': 60},
      );
      final page = AgentRunsPage.fromJson(data);
      final sym = symbol.toUpperCase();
      // A cycle may reference the symbol in a stage's structured `stocks`, or
      // only mention it in the stage details / final-result narrative — match
      // any of those so a real buy/sell decision isn't missed.
      bool mentions(String? text) =>
          text != null && _wordRegex(sym).hasMatch(text.toUpperCase());
      return page.runs
          .where((run) =>
              run.stages.any((st) =>
                  st.stocks.any((s) => s.toUpperCase() == sym) ||
                  mentions(st.details)) ||
              mentions(run.finalResult) ||
              mentions(run.name))
          .take(6)
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
