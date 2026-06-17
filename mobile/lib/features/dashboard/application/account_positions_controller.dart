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

/// Per-holding 1D price sparklines for a brokerage, relative to the device's
/// local midnight — the same "since 12 AM" intraday view as the portfolio 1D
/// chart and the live-trading screen. Maps `symbol → intraday values`. Reuses
/// the live-trading `/symbol-historicals` endpoint; fetched once per brokerage.
final holdingsSparklinesProvider =
    FutureProvider.autoDispose.family<Map<String, List<double>>, String>(
        (ref, brokerageId) async {
  final holdings = await ref.read(accountHoldingsProvider(brokerageId).future);
  final symbols = holdings.positions
      .map((p) => p.symbol)
      .where((s) => s.isNotEmpty)
      .toList();
  if (symbols.isEmpty) return const {};
  final hist =
      await ref.read(liveRepositoryProvider).symbolHistoricals(symbols, '1D');
  final now = DateTime.now();
  final midnight = DateTime(now.year, now.month, now.day);
  final out = <String, List<double>>{};
  hist.forEach((sym, pts) {
    final vals = <double>[];
    for (final p in pts) {
      final t = parseDateTime(p.ts);
      // Keep points from local midnight onward (1D = since 12 AM).
      if (t == null || !t.isBefore(midnight)) vals.add(p.value);
    }
    if (vals.length >= 2) out[sym] = vals;
  });
  return out;
});
