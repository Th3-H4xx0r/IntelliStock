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
/// Pick a `/symbol-historicals` range whose bar resolution suits a holding
/// [days] old, so a since-purchase slice has enough points (a 5-day-old name
/// gets 10-min bars; months-old gets daily). Mirrors the backend range map.
String _rangeForHoldingAge(int days) {
  if (days <= 6) return '1W'; // 10-min bars
  if (days <= 31) return '1M'; // hourly
  if (days <= 93) return '3M'; // daily
  if (days <= 366) return '1Y'; // daily
  return 'ALL'; // weekly
}

final holdingsSparklinesProvider = FutureProvider.autoDispose
    .family<Map<String, List<double>>, HoldingsSparkArgs>((ref, args) async {
  final holdings =
      await ref.read(accountHoldingsProvider(args.brokerageId).future);
  final symbols = holdings.positions
      .map((p) => p.symbol)
      .where((s) => s.isNotEmpty)
      .toList();
  if (symbols.isEmpty) return const {};
  final repo = ref.read(liveRepositoryProvider);

  // ── Daily (1D): one batch fetch, trimmed to the device's local midnight. ──
  if (args.range == '1D') {
    final hist = await repo.symbolHistoricals(symbols, '1D');
    final now = DateTime.now();
    final midnight = DateTime(now.year, now.month, now.day);
    final out = <String, List<double>>{};
    hist.forEach((sym, pts) {
      final all = <double>[];
      final since = <double>[];
      for (final p in pts) {
        all.add(p.value);
        final t = parseDateTime(p.ts);
        if (t == null || !t.isBefore(midnight)) since.add(p.value);
      }
      // Right after 12 AM there may be < 2 post-midnight bars → fall back.
      final vals = since.length >= 2 ? since : all;
      if (vals.length >= 2) out[sym] = vals;
    });
    return out;
  }

  // ── Total: each holding's series from WHEN IT WAS BOUGHT → today. Because a
  // single coarse range (ALL = 5yr weekly) has too few points since a recent
  // purchase, fetch each holding at a resolution matched to its age, then clip
  // to the purchase date. Watch holdingOpens so this recomputes once dates load.
  final opens = await ref.watch(holdingOpensProvider(args.brokerageId).future);
  // Cost basis + current price per symbol — used to anchor the series ends so
  // the spark's direction matches the position's Total P&L exactly.
  final entryOf = {
    for (final p in holdings.positions) p.symbol: p.avgEntryPrice
  };
  final lastOf = {for (final p in holdings.positions) p.symbol: p.lastPrice};

  final now = DateTime.now();
  final byRange = <String, List<String>>{};
  for (final s in symbols) {
    final boughtAt = opens[s];
    final range = boughtAt == null
        ? '3M'
        : _rangeForHoldingAge(now.difference(boughtAt).inDays);
    byRange.putIfAbsent(range, () => []).add(s);
  }

  final out = <String, List<double>>{};
  for (final entry in byRange.entries) {
    final hist = await repo.symbolHistoricals(entry.value, entry.key);
    hist.forEach((sym, pts) {
      final boughtAt = opens[sym];
      final all = <double>[];
      final sinceBuy = <double>[];
      for (final p in pts) {
        all.add(p.value);
        final t = parseDateTime(p.ts);
        if (boughtAt != null && (t == null || !t.isBefore(boughtAt))) {
          sinceBuy.add(p.value);
        }
      }
      if (boughtAt != null) {
        // Anchor start = avg entry (cost basis), end = current price, with the
        // since-purchase market path between. The first historical bar after a
        // purchase isn't your fill price, so without this the line can point
        // the opposite way to the P&L for volatile names (e.g. NET).
        final series = <double>[];
        final ent = entryOf[sym];
        if (ent != null && ent > 0) series.add(ent);
        series.addAll(sinceBuy);
        final last = lastOf[sym];
        if (last != null && last > 0) series.add(last);
        if (series.length >= 2) {
          out[sym] = series;
          return;
        }
      }
      // No purchase date (or too few points) → full age-matched series.
      if (all.length >= 2) out[sym] = all;
    });
  }
  return out;
});
