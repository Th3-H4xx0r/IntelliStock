import 'package:flutter_riverpod/flutter_riverpod.dart';
import '../../../core/models/portfolio_history.dart';
import '../../../core/network/api_client.dart';
import '../../../core/network/api_error.dart';
import 'models/live_state.dart';

/// Result of a command submission.
class CommandResult {
  const CommandResult({
    required this.commandId,
    required this.status,
    this.result,
    this.error,
  });

  final String commandId;
  final String status;
  final Map<String, dynamic>? result;
  final String? error;

  factory CommandResult.fromJson(Map<String, dynamic> json) {
    return CommandResult(
      commandId: (json['command_id'] as String?) ?? '',
      status: (json['status'] as String?) ?? 'pending',
      result: json['result'] is Map<String, dynamic>
          ? json['result'] as Map<String, dynamic>
          : null,
      error: json['error'] as String?,
    );
  }

  bool get isTerminal => status == 'completed' || status == 'failed';
}

/// Accesses the live-trading REST endpoints for a single instance.
class LiveRepository {
  const LiveRepository(this._client);

  final ApiClient _client;

  /// `GET /instances/{id}/live-state` → null on 404 (not running).
  Future<LiveState?> liveState(String id) async {
    try {
      final data = await _client.get<Map<String, dynamic>>(
        '/instances/$id/live-state',
      );
      return LiveState.fromJson(data);
    } on ApiError catch (e) {
      if (e.statusCode == 404) return null;
      rethrow;
    }
  }

  /// `GET /instances/{id}/portfolio-history?range=` → [PortfolioHistory].
  Future<PortfolioHistory> equityHistory(String id, String range) async {
    final data = await _client.get<Map<String, dynamic>>(
      '/instances/$id/portfolio-history',
      query: {'range': range},
    );
    return PortfolioHistory.fromJson(data);
  }

  /// `GET /symbol-historicals?symbols=&range=`
  /// Returns a map: symbol → list of {ts, value} points.
  Future<Map<String, List<HistPoint>>> symbolHistoricals(
    List<String> symbols,
    String range,
  ) async {
    if (symbols.isEmpty) return {};
    final data = await _client.get<Map<String, dynamic>>(
      '/symbol-historicals',
      query: {'symbols': symbols.join(','), 'range': range},
    );
    final results = data['results'];
    if (results is! Map) return {};
    return (results).map((key, value) {
      final pts = (value as List?)
              ?.whereType<Map<String, dynamic>>()
              .map((p) => HistPoint(
                    ts: p['ts'],
                    value: (p['value'] as num?)?.toDouble() ?? 0,
                  ))
              .toList() ??
          [];
      return MapEntry(key as String, pts);
    });
  }

  /// `GET /brokerages/{id}/holding-opens` → map of symbol → acquisition date
  /// (when the current open position started). Empty for non-Alpaca accounts or
  /// when the open fill is outside the fetched order window; callers fall back
  /// to the full series in that case.
  Future<Map<String, DateTime>> holdingOpens(String brokerageId) async {
    final data = await _client.get<Map<String, dynamic>>(
      '/brokerages/$brokerageId/holding-opens',
    );
    final opens = data['opens'];
    if (opens is! Map) return {};
    final out = <String, DateTime>{};
    opens.forEach((k, v) {
      final t = DateTime.tryParse(v?.toString() ?? '');
      if (t != null) out[k.toString()] = t.toLocal();
    });
    return out;
  }

  /// `POST /instances/{id}/live-command` → [CommandResult].
  Future<CommandResult> sendCommand(
    String id,
    String type,
    Map<String, dynamic> payload,
  ) async {
    final data = await _client.post<Map<String, dynamic>>(
      '/instances/$id/live-command',
      body: {'type': type, 'payload': payload},
    );
    return CommandResult.fromJson(data);
  }

  /// `GET /live-commands/{commandId}` → [CommandResult].
  Future<CommandResult> commandStatus(String commandId) async {
    final data = await _client.get<Map<String, dynamic>>(
      '/live-commands/$commandId',
    );
    return CommandResult.fromJson(data);
  }
}

/// A raw historical price point from the symbol-historicals endpoint.
class HistPoint {
  const HistPoint({required this.ts, required this.value});
  final dynamic ts; // ISO string or epoch
  final double value;
}

final liveRepositoryProvider = Provider<LiveRepository>(
  (ref) => LiveRepository(ref.watch(apiClientProvider)),
);
