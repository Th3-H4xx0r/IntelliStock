import 'package:flutter_riverpod/flutter_riverpod.dart';
import '../network/api_client.dart';
import 'push_device.dart';

/// Data access for /push/devices (APNs device-token registration).
class PushRepository {
  const PushRepository(this._client);

  final ApiClient _client;

  /// GET /push/devices — the current user's registered devices.
  Future<List<PushDevice>> listDevices() async {
    final data = await _client.get<Map<String, dynamic>>('/push/devices');
    final raw = data['devices'] as List<dynamic>? ?? const [];
    return raw.whereType<Map<String, dynamic>>().map(PushDevice.fromJson).toList();
  }

  /// POST /push/devices — register/refresh this device's APNs token.
  Future<void> registerToken(String token, {required String env, String? appVersion}) async {
    await _client.post<dynamic>('/push/devices', body: {
      'device_token': token,
      'platform': 'ios',
      'env': env,
      if (appVersion != null) 'app_version': appVersion,
    });
  }

  /// DELETE /push/devices/{token} — unregister (logout).
  Future<void> unregister(String token) async {
    await _client.delete<dynamic>('/push/devices/$token');
  }
}

final pushRepositoryProvider = Provider<PushRepository>(
  (ref) => PushRepository(ref.watch(apiClientProvider)),
);
