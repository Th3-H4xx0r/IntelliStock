import 'package:flutter_test/flutter_test.dart';
import 'package:intellistock_mobile/core/network/api_client.dart';
import 'package:intellistock_mobile/features/strategies/data/strategy_repository.dart';

/// Captures the path/body of the last put/post so we can assert the wire shape.
class _FakeApiClient implements ApiClient {
  String? lastPath;
  Object? lastBody;
  Map<String, dynamic> response = const {};

  @override
  Future<T> put<T>(String path, {Object? body}) async {
    lastPath = path;
    lastBody = body;
    return response as T;
  }

  @override
  Future<T> post<T>(String path, {Object? body, Map<String, dynamic>? query}) async {
    lastPath = path;
    lastBody = body;
    return response as T;
  }

  @override
  dynamic noSuchMethod(Invocation invocation) => super.noSuchMethod(invocation);
}

void main() {
  group('StrategyRepository preserve-history wiring', () {
    test('update without preserveHistory omits the flag', () async {
      final api = _FakeApiClient();
      final repo = StrategyRepository(api);
      await repo.update('179', {'name': 'X', 'strategies': []});
      expect(api.lastPath, '/strategies/179');
      final body = api.lastBody as Map<String, dynamic>;
      expect(body.containsKey('preserve_history'), isFalse);
    });

    test('update with preserveHistory adds preserve_history: true', () async {
      final api = _FakeApiClient();
      final repo = StrategyRepository(api);
      await repo.update('179', {'name': 'X', 'strategies': []},
          preserveHistory: true);
      final body = api.lastBody as Map<String, dynamic>;
      expect(body['preserve_history'], isTrue);
      expect(body['name'], 'X');
    });

    test('previewConfigChange posts strategies to the preview endpoint', () async {
      final api = _FakeApiClient()..response = {'needs_prompt': true};
      final repo = StrategyRepository(api);
      final out = await repo.previewConfigChange('179', [
        {'strategy': 'graph_nexus_analysis', 'config': {}}
      ]);
      expect(api.lastPath, '/strategies/179/config-change-preview');
      expect((api.lastBody as Map)['strategies'], isA<List>());
      expect(out['needs_prompt'], isTrue);
    });
  });
}
