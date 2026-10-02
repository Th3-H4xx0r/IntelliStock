import 'package:flutter_riverpod/flutter_riverpod.dart';
import '../../../core/network/api_client.dart';

// ── Data models ────────────────────────────────────────────────────────────────

class TelemetryHealth {
  const TelemetryHealth({
    this.bufferDepth,
    this.lastFlushAgeS,
    this.writeErrors24h = 0,
  });
  final int? bufferDepth;
  final num? lastFlushAgeS;
  final int writeErrors24h;

  factory TelemetryHealth.fromJson(Map<String, dynamic> j) => TelemetryHealth(
        bufferDepth: (j['buffer_depth'] as num?)?.toInt(),
        lastFlushAgeS: j['last_flush_age_s'] as num?,
        writeErrors24h: (j['write_errors_24h'] as num?)?.toInt() ?? 0,
      );
}

class ProviderBreakdown {
  const ProviderBreakdown({required this.provider, this.costUsd, this.tokens, this.calls});
  final String provider;
  final double? costUsd;
  final int? tokens;
  final int? calls;

  factory ProviderBreakdown.fromJson(Map<String, dynamic> j) => ProviderBreakdown(
        provider: (j['provider'] as String?) ?? '',
        costUsd: (j['cost_usd'] as num?)?.toDouble(),
        tokens: (j['tokens'] as num?)?.toInt(),
        calls: (j['calls'] as num?)?.toInt(),
      );
}

class UsageSummary {
  const UsageSummary({
    this.totalCostUsd,
    this.totalCalls,
    this.totalTokens,
    this.maxPlanEstimateUsd,
    this.byProvider = const [],
    this.telemetryHealth,
  });
  final double? totalCostUsd;
  final int? totalCalls;
  final int? totalTokens;
  final double? maxPlanEstimateUsd;
  final List<ProviderBreakdown> byProvider;
  final TelemetryHealth? telemetryHealth;

  factory UsageSummary.fromJson(Map<String, dynamic> j) => UsageSummary(
        totalCostUsd: (j['total_cost_usd'] as num?)?.toDouble(),
        totalCalls: (j['total_calls'] as num?)?.toInt(),
        totalTokens: (j['total_tokens'] as num?)?.toInt(),
        maxPlanEstimateUsd: (j['max_plan_estimate_usd'] as num?)?.toDouble(),
        byProvider: (j['by_provider'] as List?)
                ?.whereType<Map<String, dynamic>>()
                .map(ProviderBreakdown.fromJson)
                .toList() ??
            [],
        telemetryHealth: j['telemetry_health'] is Map<String, dynamic>
            ? TelemetryHealth.fromJson(j['telemetry_health'] as Map<String, dynamic>)
            : null,
      );
}

class TimeseriesRow {
  const TimeseriesRow({
    required this.provider,
    required this.bucketStartTs,
    this.costUsd,
    this.tokens,
    this.calls,
  });
  final String provider;
  final int bucketStartTs; // epoch ms
  final double? costUsd;
  final int? tokens;
  final int? calls;

  factory TimeseriesRow.fromJson(Map<String, dynamic> j) => TimeseriesRow(
        provider: (j['provider'] as String?) ?? 'unknown',
        bucketStartTs: (j['bucket_start_ts'] as num?)?.toInt() ?? 0,
        costUsd: (j['cost_usd'] as num?)?.toDouble(),
        tokens: (j['tokens'] as num?)?.toInt(),
        calls: (j['calls'] as num?)?.toInt(),
      );
}

class SpenderRow {
  const SpenderRow({required this.key, this.calls, this.tokens, this.costUsd});
  final String key;
  final int? calls;
  final int? tokens;
  final double? costUsd;

  factory SpenderRow.fromJson(Map<String, dynamic> j) => SpenderRow(
        key: (j['key'] as String?) ?? '',
        calls: (j['calls'] as num?)?.toInt(),
        tokens: (j['tokens'] as num?)?.toInt(),
        costUsd: (j['cost_usd'] as num?)?.toDouble(),
      );
}

class BacktestUsageRow {
  const BacktestUsageRow({
    this.backtestId,
    this.displayLabel,
    this.kind,
    this.instanceId,
    this.firstTs,
    this.calls,
    this.tokens,
    this.costUsd,
    this.okCalls,
    this.failedCalls,
  });
  final String? backtestId;
  final String? displayLabel;
  final String? kind;
  final String? instanceId;
  final int? firstTs; // epoch ms
  final int? calls;
  final int? tokens;
  final double? costUsd;
  final int? okCalls;
  final int? failedCalls;

  factory BacktestUsageRow.fromJson(Map<String, dynamic> j) => BacktestUsageRow(
        backtestId: j['backtest_id'] as String?,
        displayLabel: j['display_label'] as String?,
        kind: j['kind'] as String?,
        instanceId: j['instance_id'] as String?,
        firstTs: (j['first_ts'] as num?)?.toInt(),
        calls: (j['calls'] as num?)?.toInt(),
        tokens: (j['tokens'] as num?)?.toInt(),
        costUsd: (j['cost_usd'] as num?)?.toDouble(),
        okCalls: (j['ok_calls'] as num?)?.toInt(),
        failedCalls: (j['failed_calls'] as num?)?.toInt(),
      );
}

class RecentCall {
  const RecentCall({
    this.id,
    this.ts,
    this.provider,
    this.model,
    this.inputTokens,
    this.outputTokens,
    this.totalCostUsd,
    this.strategy,
    this.callSite,
    this.raw = const {},
  });
  final String? id;
  final int? ts; // epoch ms
  final String? provider;
  final String? model;
  final int? inputTokens;
  final int? outputTokens;
  final double? totalCostUsd;
  final String? strategy;
  final String? callSite;
  final Map<String, dynamic> raw; // full JSON for detail dialog

  factory RecentCall.fromJson(Map<String, dynamic> j) => RecentCall(
        id: j['id'] as String?,
        ts: (j['ts'] as num?)?.toInt(),
        provider: j['provider'] as String?,
        model: j['model'] as String?,
        inputTokens: (j['input_tokens'] as num?)?.toInt(),
        outputTokens: (j['output_tokens'] as num?)?.toInt(),
        totalCostUsd: (j['total_cost_usd'] as num?)?.toDouble(),
        strategy: j['strategy'] as String?,
        callSite: j['call_site'] as String?,
        raw: j,
      );
}

class TokenUsageData {
  const TokenUsageData({
    this.summary,
    this.timeseries = const [],
    this.topByModel = const [],
    this.topByCallSite = const [],
    this.byBacktest = const [],
    this.recentCalls = const [],
    this.partialError,
  });

  final UsageSummary? summary;
  final List<TimeseriesRow> timeseries;
  final List<SpenderRow> topByModel;
  final List<SpenderRow> topByCallSite;
  final List<BacktestUsageRow> byBacktest;
  final List<RecentCall> recentCalls;
  final String? partialError;
}

// ── Repository ─────────────────────────────────────────────────────────────────

class TokenUsageRepository {
  const TokenUsageRepository(this._client);

  final ApiClient _client;

  /// GET /llm-usage/summary?range=
  Future<UsageSummary> summary(String range) async {
    final data = await _client.get<Map<String, dynamic>>(
        '/llm-usage/summary', query: {'range': range});
    return UsageSummary.fromJson(data);
  }

  /// GET /llm-usage/timeseries?range=&bucket=
  Future<List<TimeseriesRow>> timeseries(String range, String bucket) async {
    final raw = await _client.get<dynamic>(
        '/llm-usage/timeseries', query: {'range': range, 'bucket': bucket});
    final list = raw is List ? raw : (raw as Map?)?['rows'] ?? [];
    return (list as List).whereType<Map<String, dynamic>>().map(TimeseriesRow.fromJson).toList();
  }

  /// GET /llm-usage/top-spenders?range=&group_by=&limit=
  Future<List<SpenderRow>> topSpenders(String range, String groupBy, int limit) async {
    final raw = await _client.get<dynamic>('/llm-usage/top-spenders',
        query: {'range': range, 'group_by': groupBy, 'limit': limit.toString()});
    final list = raw is List ? raw : <dynamic>[];
    return list.whereType<Map<String, dynamic>>().map(SpenderRow.fromJson).toList();
  }

  /// GET /llm-usage/by-backtest?range=&limit=
  Future<List<BacktestUsageRow>> byBacktest(String range, int limit) async {
    final raw = await _client.get<dynamic>('/llm-usage/by-backtest',
        query: {'range': range, 'limit': limit.toString()});
    final list = raw is List ? raw : <dynamic>[];
    return list.whereType<Map<String, dynamic>>().map(BacktestUsageRow.fromJson).toList();
  }

  /// GET /llm-usage/calls?limit=&range=
  Future<List<RecentCall>> calls(int limit, String range) async {
    final raw = await _client.get<dynamic>('/llm-usage/calls',
        query: {'limit': limit.toString(), 'range': range});
    final list = raw is List ? raw : <dynamic>[];
    return list.whereType<Map<String, dynamic>>().map(RecentCall.fromJson).toList();
  }

  /// Fetch all 6 endpoints in parallel; partial failure populates partialError.
  Future<TokenUsageData> fetchAll(String range) async {
    final bucket = range == '24h' ? 'hour' : 'day';
    final results = await Future.wait([
      summary(range).then<dynamic>((v) => v).catchError((e) => e),
      timeseries(range, bucket).then<dynamic>((v) => v).catchError((e) => e),
      topSpenders(range, 'model', 10).then<dynamic>((v) => v).catchError((e) => e),
      topSpenders(range, 'call_site', 10).then<dynamic>((v) => v).catchError((e) => e),
      byBacktest(range, 50).then<dynamic>((v) => v).catchError((e) => e),
      calls(50, 'now').then<dynamic>((v) => v).catchError((e) => e),
    ]);

    int failures = 0;
    String? firstError;
    T? pick<T>(dynamic r) {
      if (r is T) return r;
      failures++;
      firstError ??= r.toString();
      return null;
    }

    return TokenUsageData(
      summary: pick<UsageSummary>(results[0]),
      timeseries: pick<List<TimeseriesRow>>(results[1]) ?? [],
      topByModel: pick<List<SpenderRow>>(results[2]) ?? [],
      topByCallSite: pick<List<SpenderRow>>(results[3]) ?? [],
      byBacktest: pick<List<BacktestUsageRow>>(results[4]) ?? [],
      recentCalls: pick<List<RecentCall>>(results[5]) ?? [],
      partialError: failures > 0 ? '$failures of 6 requests failed: $firstError' : null,
    );
  }
}

final tokenUsageRepositoryProvider = Provider<TokenUsageRepository>(
  (ref) => TokenUsageRepository(ref.watch(apiClientProvider)),
);
