import 'package:flutter_riverpod/flutter_riverpod.dart';
import '../../../core/polling/poller.dart';
import '../data/dashboard_repository.dart';

/// Polls the current holdings for a brokerage account (keyed by brokerage id)
/// so the dashboard's Holdings list stays live, like the chart. Lifecycle-aware
/// (pauses in the background). Errors keep the last good list.
class AccountPositionsNotifier
    extends AutoDisposeFamilyAsyncNotifier<List<AccountPosition>, String> {
  IntervalPoller? _poller;

  @override
  Future<List<AccountPosition>> build(String brokerageId) async {
    final lifecycle = ref.read(appLifecycleProvider);
    final data = await ref
        .read(dashboardRepositoryProvider)
        .accountPositions(brokerageId);

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
        await ref.read(dashboardRepositoryProvider).accountPositions(id),
      );
    } catch (_) {
      // keep the last good list on a transient failure
    }
  }
}

final accountPositionsProvider = AutoDisposeAsyncNotifierProviderFamily<
    AccountPositionsNotifier, List<AccountPosition>, String>(
  AccountPositionsNotifier.new,
);
