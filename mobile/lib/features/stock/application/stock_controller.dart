import 'package:flutter_riverpod/flutter_riverpod.dart';
import '../../../core/formatters/formatters.dart';
import '../../../core/network/api_client.dart';
import '../../live_trading/data/live_repository.dart';
import '../../live_trading/data/models/live_state.dart';

/// A symbol's price series for a given range. For 1D it's relative to the
/// device's local midnight (the "since 12 AM" view used across the app).
typedef StockSeriesArgs = ({String symbol, String range});
typedef StockSeries = ({List<DateTime> ts, List<double> vals});

final stockHistoryProvider =
    FutureProvider.autoDispose.family<StockSeries, StockSeriesArgs>(
  (ref, args) async {
    final map = await ref
        .read(liveRepositoryProvider)
        .symbolHistoricals([args.symbol], args.range);
    final pts = map[args.symbol] ?? const [];
    DateTime? midnight;
    if (args.range == '1D') {
      final n = DateTime.now();
      midnight = DateTime(n.year, n.month, n.day);
    }
    final ts = <DateTime>[];
    final vals = <double>[];
    for (final p in pts) {
      final t = parseDateTime(p.ts);
      if (t == null) continue;
      if (midnight != null && t.isBefore(midnight)) continue;
      ts.add(t);
      vals.add(p.value);
    }
    return (ts: ts, vals: vals);
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
