import 'package:flutter_test/flutter_test.dart';
import 'package:intellistock_mobile/core/network/api_client.dart';
import 'package:intellistock_mobile/core/push/push_device.dart';
import 'package:intellistock_mobile/core/push/push_repository.dart';

class _FakeApiClient implements ApiClient {
  final calls = <Map<String, dynamic>>[];
  Object? getResponse;

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
  Future<T> get<T>(String path, {Map<String, dynamic>? query}) async {
    calls.add({'method': 'GET', 'path': path});
    return (getResponse ?? <String, dynamic>{}) as T;
  }

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
    final c = api.calls.firstWhere((c) => c['method'] == 'POST');
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

  test('listDevices parses the devices array', () async {
    final api = _FakeApiClient()
      ..getResponse = {
        'devices': [
          {
            'device_token': 'abcdef0123456789',
            'platform': 'ios',
            'env': 'sandbox',
            'app_version': '1.2.0',
            'last_seen': '2026-06-11T00:00:00Z',
          },
        ],
      };
    final repo = PushRepository(api);
    final devices = await repo.listDevices();
    expect(devices.length, 1);
    expect(devices.first.platform, 'ios');
    expect(devices.first.env, 'sandbox');
    expect(devices.first.tokenSuffix, '…23456789');
  });

  test('listDevices tolerates an empty/missing array', () async {
    final api = _FakeApiClient()..getResponse = {'devices': []};
    final repo = PushRepository(api);
    expect(await repo.listDevices(), isEmpty);
  });

  group('PushDevice', () {
    test('fromJson + tokenSuffix masks long tokens', () {
      final d = PushDevice.fromJson({'device_token': '0123456789abcdef', 'platform': 'ios', 'env': 'prod'});
      expect(d.tokenSuffix, '…89abcdef');
      expect(d.env, 'prod');
    });
    test('short token is shown as-is', () {
      final d = PushDevice.fromJson({'device_token': 'short', 'platform': 'ios', 'env': 'prod'});
      expect(d.tokenSuffix, 'short');
    });
  });
}
