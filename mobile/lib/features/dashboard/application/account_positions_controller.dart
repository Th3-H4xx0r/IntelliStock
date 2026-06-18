import 'package:flutter_riverpod/flutter_riverpod.dart';
import '../../../core/formatters/formatters.dart';
import '../../../core/polling/poller.dart';
import '../../live_trading/data/live_repository.dart';
import '../data/dashboard_repository.dart';

/// Polls the account's uninvested cash + holdings for a brokerage (keyed by
/// brokerage id) so the dashboard's Holdings section stays live, like the
/// chart. Lifecycle-aware (pauses in the background); errors keep the last
/// good data.
class AccountHoldingsNotifier
    extends AutoDisposeFamilyAsyncNotifier<AccountHoldings, String> {
  IntervalPoller? _poller;

  @override
  Future<AccountHoldings> build(String brokerageId) async {
    final lifecycle = ref.read(appLifecycleProvider);
    final data = await ref
        .read(dashboardRepositoryProvider)
        .accountHoldings(brokerageId);

    _poller = IntervalPoller(
      fetch: () => _refresh(brokerageId),
      interval: () => const Duration(seconds: 15),
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

  Future<void> _refresh(String id) async {
    try {
      state = AsyncData(
        await ref.read(dashboardRepositoryProvider).accountHoldings(id),
      );
    } catch (_) {
      // keep the last good data on a transient failure
    }
  }
}

final accountHoldingsProvider = AutoDisposeAsyncNotifierProviderFamily<
    AccountHoldingsNotifier, AccountHoldings, String>(
  AccountHoldingsNotifier.new,
);

/// Which P&L each holding row shows: lifetime unrealized [total] or [daily]
/// (since 12 AM, derived from the 1D sparkline). Toggled by the Holdings pill.
enum HoldingsPnlMode { total, daily }

final holdingsPnlModeProvider =
    StateProvider<HoldingsPnlMode>((ref) => HoldingsPnlMode.daily);

/// Acquisition date per currently-held symbol (when its current open position
/// started), for the Total sparkline's holding-period clip. Alpaca-only; empty
/// for other brokers or when the opening fill is outside the broker's fetched
/// order window (callers then keep the full series).
final holdingOpensProvider = FutureProvider.autoDispose
    .family<Map<String, DateTime>, String>((ref, brokerageId) async {
  try {
    return await ref.read(liveRepositoryProvider).holdingOpens(brokerageId);
  } catch (_) {
    return const {};
  }
});

/// Which brokerage + which range to fetch holding sparklines for.
typedef HoldingsSparkArgs = ({String brokerageId, String range});

/// Per-holding price sparklines for a brokerage at [range]. For `1D` the points
/// are trimmed to the device's local midnight (the "since 12 AM" intraday view);
/// longer ranges keep the full series. Maps `symbol → values`. Keyed by
/// (brokerage, range) so the Daily/Total toggle re-fetches a different range.
/// Reuses the live-trading `/symbol-historicals` endpoint.
final holdingsSparklinesProvider = FutureProvider.autoDispose
    .family<Map<String, List<double>>, HoldingsSparkArgs>((ref, args) async {
  final holdings =
      await ref.read(accountHoldingsProvider(args.brokerageId).future);
  final symbols = holdings.positions
      .map((p) => p.symbol)
      .where((s) => s.isNotEmpty)
      .toList();
  if (symbols.isEmpty) return const {};
  final hist = await ref
      .read(liveRepositoryProvider)
      .symbolHistoricals(symbols, args.range);
  DateTime? midnight;
  if (args.range == '1D') {
    final now = DateTime.now();
    midnight = DateTime(now.year, now.month, now.day);
  }
  // Total view: clip each holding's series to start at when it was bought, so
  // the sparkline shows the holding period — not the stock's whole history.
  Map<String, DateTime> opens = const {};
  if (args.range == 'ALL') {
    opens = await ref.read(holdingOpensProvider(args.brokerageId).future);
  }
  final out = <String, List<double>>{};
  hist.forEach((sym, pts) {
    final all = <double>[];
    final sinceMidnight = <double>[];
    final sinceBuy = <double>[];
    final boughtAt = opens[sym];
    for (final p in pts) {
      all.add(p.value);
      final t = parseDateTime(p.ts);
      if (midnight != null && (t == null || !t.isBefore(midnight))) {
        sinceMidnight.add(p.value);
      }
      if (boughtAt != null && (t == null || !t.isBefore(boughtAt))) {
        sinceBuy.add(p.value);
      }
    }
    // 1D = since midnight; Total = since purchase. Both fall back to the full
    // series when there aren't ≥2 points in the window (e.g. just after 12 AM,
    // or when the purchase date is unknown), so the spark always draws.
    final List<double> vals;
    if (midnight != null) {
      vals = sinceMidnight.length >= 2 ? sinceMidnight : all;
    } else if (boughtAt != null) {
      vals = sinceBuy.length >= 2 ? sinceBuy : all;
    } else {
      vals = all;
    }
    if (vals.length >= 2) out[sym] = vals;
  });
  return out;
});
