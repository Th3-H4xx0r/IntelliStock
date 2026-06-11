import 'package:flutter_riverpod/flutter_riverpod.dart';
import '../../../core/network/api_client.dart';
import 'models/notification_prefs.dart';

/// Data access for /notification-preferences + /notifications/test.
class NotificationPrefsRepository {
  const NotificationPrefsRepository(this._client);

  final ApiClient _client;

  /// GET /notification-preferences
  Future<NotificationPrefs> get() async {
    final data =
        await _client.get<Map<String, dynamic>>('/notification-preferences');
    return NotificationPrefs.fromJson(data);
  }

  /// PUT /notification-preferences (replace the full matrix).
  Future<NotificationPrefs> save(NotificationPrefs prefs) async {
    final data = await _client.put<Map<String, dynamic>>(
      '/notification-preferences',
      body: prefs.toJson(),
    );
    return NotificationPrefs.fromJson(data);
  }

  /// POST /notifications/test — send a sample notification via [channel] so the
  /// operator can confirm the delivery option works.
  Future<Map<String, dynamic>> sendTest(NotifChannel channel) async {
    return _client.post<Map<String, dynamic>>(
      '/notifications/test',
      body: {'channel': channel == NotifChannel.discord ? 'discord' : 'push'},
    );
  }
}

final notificationPrefsRepositoryProvider =
    Provider<NotificationPrefsRepository>(
  (ref) => NotificationPrefsRepository(ref.watch(apiClientProvider)),
);
