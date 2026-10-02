import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'push_device.dart';
import 'push_repository.dart';

/// Loads the current user's registered push devices (GET /push/devices).
class PushDevicesController extends AutoDisposeAsyncNotifier<List<PushDevice>> {
  @override
  Future<List<PushDevice>> build() =>
      ref.read(pushRepositoryProvider).listDevices();

  Future<void> refresh() async {
    state = const AsyncLoading();
    state = await AsyncValue.guard(
      () => ref.read(pushRepositoryProvider).listDevices(),
    );
  }
}

final pushDevicesProvider =
    AutoDisposeAsyncNotifierProvider<PushDevicesController, List<PushDevice>>(
  PushDevicesController.new,
);
