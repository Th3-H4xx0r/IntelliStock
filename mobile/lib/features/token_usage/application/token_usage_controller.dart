import 'package:flutter_riverpod/flutter_riverpod.dart';
import '../../../core/polling/poller.dart';
import '../data/token_usage_repository.dart';

/// PollingNotifier that fetches all token-usage data every 10 s.
/// Responds to lifecycle (pauses on background, resumes on foreground).
class TokenUsageController extends PollingNotifier<TokenUsageData> {
  String range = '24h';

  @override
  Future<TokenUsageData> fetch() =>
      ref.read(tokenUsageRepositoryProvider).fetchAll(range);

  @override
  Duration interval() => const Duration(seconds: 10);

  /// Change range and force an immediate refresh.
  Future<void> setRange(String newRange) async {
    range = newRange;
    await refreshNow();
  }
}

final tokenUsageControllerProvider =
    AutoDisposeAsyncNotifierProvider<TokenUsageController, TokenUsageData>(
  TokenUsageController.new,
);
