import 'package:flutter_riverpod/flutter_riverpod.dart';
import '../../../core/polling/poller.dart';
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
