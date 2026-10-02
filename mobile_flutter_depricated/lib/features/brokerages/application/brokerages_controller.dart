import 'package:flutter_riverpod/flutter_riverpod.dart';
import '../data/brokerage_repository.dart';
import '../data/models/brokerage.dart';

/// Holds the list of linked brokerage accounts and exposes a [refresh] method.
///
/// Uses plain [AutoDisposeAsyncNotifier] — no code generation.
class BrokeragesController
    extends AutoDisposeAsyncNotifier<List<Brokerage>> {
  @override
  Future<List<Brokerage>> build() => _fetch();

  Future<List<Brokerage>> _fetch() =>
      ref.read(brokerageRepositoryProvider).list();

  /// Force a network reload and update state.
  Future<void> refresh() async {
    state = const AsyncLoading();
    state = await AsyncValue.guard(_fetch);
  }
}

final brokeragesControllerProvider =
    AutoDisposeAsyncNotifierProvider<BrokeragesController, List<Brokerage>>(
  BrokeragesController.new,
);
