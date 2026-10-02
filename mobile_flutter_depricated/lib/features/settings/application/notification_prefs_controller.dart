import 'package:flutter_riverpod/flutter_riverpod.dart';
import '../data/models/notification_prefs.dart';
import '../data/notification_prefs_repository.dart';

/// Loads the per-category notification matrix and applies optimistic toggles
/// (revert on failure), following the app's [AutoDisposeAsyncNotifier] style.
class NotificationPrefsController
    extends AutoDisposeAsyncNotifier<NotificationPrefs> {
  @override
  Future<NotificationPrefs> build() =>
      ref.read(notificationPrefsRepositoryProvider).get();

  /// Flip one channel of one category. Updates state immediately, persists the
  /// full matrix, and reverts on failure. Returns an error string on failure,
  /// or null on success (the screen surfaces it as a snackbar).
  Future<String?> toggle(String category, NotifChannel channel, bool value) async {
    final current = state.value;
    if (current == null) return 'Preferences not loaded yet';

    final route = current.routeFor(category);
    final updatedRoute = channel == NotifChannel.discord
        ? route.copyWith(discord: value)
        : route.copyWith(push: value);
    final optimistic = current.withRoute(category, updatedRoute);

    state = AsyncData(optimistic);
    try {
      final saved =
          await ref.read(notificationPrefsRepositoryProvider).save(optimistic);
      state = AsyncData(saved);
      return null;
    } catch (e) {
      state = AsyncData(current); // revert
      return e.toString();
    }
  }

  /// Send a test notification via [channel]. Returns the backend result map.
  Future<Map<String, dynamic>> sendTest(NotifChannel channel) =>
      ref.read(notificationPrefsRepositoryProvider).sendTest(channel);
}

final notificationPrefsControllerProvider = AutoDisposeAsyncNotifierProvider<
    NotificationPrefsController, NotificationPrefs>(
  NotificationPrefsController.new,
);
