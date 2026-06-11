import 'package:flutter_riverpod/flutter_riverpod.dart';
import '../../../core/network/api_client.dart';
import 'models/backtest.dart';

class BacktestRepository {
  const BacktestRepository(this._client);

  final ApiClient _client;

  /// GET /backtests?page=&per_page=&sort_by=&sort_order=
  Future<BacktestListResponse> list({
    int page = 1,
    int perPage = 15,
    String sortBy = 'completed_at',
    String sortOrder = 'desc',
  }) async {
    final data = await _client.get<Map<String, dynamic>>(
      '/backtests',
      query: {
        'page': page.toString(),
        'per_page': perPage.toString(),
        'sort_by': sortBy,
        'sort_order': sortOrder,
      },
    );
    return BacktestListResponse.fromJson(data);
  }

  /// GET /backtests/:id  (summary by default; fallback used as detail root)
  Future<BacktestSummary> get(String id) async {
    final data =
        await _client.get<Map<String, dynamic>>('/backtests/$id');
    return BacktestSummary.fromJson(data);
  }

  /// DELETE /backtests/:id
  Future<void> delete(String id) =>
      _client.delete<dynamic>('/backtests/$id');

  /// GET /backtests/:id/status
  Future<BacktestStatus> status(String id) async {
    final data =
        await _client.get<Map<String, dynamic>>('/backtests/$id/status');
    return BacktestStatus.fromJson(data);
  }

  /// GET /backtests/:id/summary
  Future<BacktestSummary> summary(String id) async {
    final data =
        await _client.get<Map<String, dynamic>>('/backtests/$id/summary');
    return BacktestSummary.fromJson(data);
  }

  /// GET /backtests/:id/graph-data
  Future<BacktestGraphData> graphData(String id) async {
    final data =
        await _client.get<Map<String, dynamic>>('/backtests/$id/graph-data');
    return BacktestGraphData.fromJson(data);
  }

  /// GET /backtests/:id/playback-data
  Future<PlaybackData> playbackData(String id) async {
    final data = await _client
        .get<Map<String, dynamic>>('/backtests/$id/playback-data');
    return PlaybackData.fromJson(data);
  }

  /// GET /backtests/:id/logs?since_line=N
  Future<Map<String, dynamic>> logs(String id, {int sinceLine = 0}) =>
      _client.get<Map<String, dynamic>>(
        '/backtests/$id/logs',
        query: {'since_line': sinceLine.toString()},
      );

  /// GET /backtests/:id/llm-cost
  Future<LlmCost> llmCost(String id) async {
    final data =
        await _client.get<Map<String, dynamic>>('/backtests/$id/llm-cost');
    return LlmCost.fromJson(data);
  }

  /// POST /backtests/:id/:action  (pause | resume | stop)
  Future<Map<String, dynamic>> action(String id, String name) =>
      _client.post<Map<String, dynamic>>('/backtests/$id/$name');

  /// POST /backtests
  Future<Map<String, dynamic>> create(Map<String, dynamic> body) =>
      _client.post<Map<String, dynamic>>('/backtests', body: body);
}

final backtestRepositoryProvider = Provider<BacktestRepository>(
  (ref) => BacktestRepository(ref.watch(apiClientProvider)),
);
