import 'package:flutter_test/flutter_test.dart';
import 'package:intellistock_mobile/core/network/api_client.dart';
import 'package:intellistock_mobile/core/push/push_repository.dart';

class _FakeApiClient implements ApiClient {
  final calls = <Map<String, dynamic>>[];

  @override
  Future<T> post<T>(String path, {Object? body, Map<String, dynamic>? query}) async {
    calls.add({'method': 'POST', 'path': path, 'body': body});
    return null as T;
  }

  @override
  Future<T> delete<T>(String path, {Map<String, dynamic>? query}) async {
    calls.add({'method': 'DELETE', 'path': path});
    return null as T;
  }

  @override
  Future<T> get<T>(String path, {Map<String, dynamic>? query}) async => null as T;
  @override
  Future<T> put<T>(String path, {Object? body}) async => null as T;
  @override
  Future<T> patch<T>(String path, {Object? body}) async => null as T;
}

void main() {
  test('registerToken POSTs the device payload', () async {
    final api = _FakeApiClient();
    final repo = PushRepository(api);
    await repo.registerToken('TOKEN123', env: 'sandbox', appVersion: '1.0.0');
    expect(api.calls.length, 1);
    final c = api.calls.first;
    expect(c['method'], 'POST');
    expect(c['path'], '/push/devices');
    final body = c['body'] as Map<String, dynamic>;
    expect(body['device_token'], 'TOKEN123');
    expect(body['platform'], 'ios');
    expect(body['env'], 'sandbox');
    expect(body['app_version'], '1.0.0');
  });

  test('unregister DELETEs by token', () async {
    final api = _FakeApiClient();
    final repo = PushRepository(api);
    await repo.unregister('TOKEN123');
    expect(api.calls.single, {'method': 'DELETE', 'path': '/push/devices/TOKEN123'});
  });
}
