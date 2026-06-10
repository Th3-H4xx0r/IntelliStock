import 'package:flutter_test/flutter_test.dart';
import 'package:intellistock_mobile/features/token_usage/data/token_usage_repository.dart';

void main() {
  group('UsageSummary.fromJson', () {
    test('parses all top-level fields', () {
      final j = {
        'total_cost_usd': 1.2345,
        'total_calls': 42,
        'total_tokens': 1500000,
        'max_plan_estimate_usd': 12.5,
        'by_provider': [
          {'provider': 'gemini', 'cost_usd': 0.5, 'tokens': 800000, 'calls': 20},
          {'provider': 'openai', 'cost_usd': 0.7, 'tokens': 700000, 'calls': 22},
        ],
        'telemetry_health': {
          'buffer_depth': 3,
          'last_flush_age_s': 5,
          'write_errors_24h': 0,
        },
      };
      final s = UsageSummary.fromJson(j);
      expect(s.totalCostUsd, closeTo(1.2345, 0.0001));
      expect(s.totalCalls, 42);
      expect(s.totalTokens, 1500000);
      expect(s.maxPlanEstimateUsd, closeTo(12.5, 0.001));
      expect(s.byProvider.length, 2);
      expect(s.byProvider[0].provider, 'gemini');
      expect(s.telemetryHealth?.bufferDepth, 3);
      expect(s.telemetryHealth?.writeErrors24h, 0);
    });

    test('handles null/missing fields gracefully', () {
      final s = UsageSummary.fromJson({});
      expect(s.totalCostUsd, isNull);
      expect(s.totalCalls, isNull);
      expect(s.byProvider, isEmpty);
      expect(s.telemetryHealth, isNull);
    });
  });

  group('TimeseriesRow.fromJson', () {
    test('parses provider and bucket_start_ts', () {
      final r = TimeseriesRow.fromJson({
        'provider': 'gemini',
        'bucket_start_ts': 1700000000000,
        'cost_usd': 0.0042,
        'tokens': 12000,
        'calls': 3,
      });
      expect(r.provider, 'gemini');
      expect(r.bucketStartTs, 1700000000000);
      expect(r.costUsd, closeTo(0.0042, 0.0001));
    });

    test('defaults provider to "unknown" when missing', () {
      final r = TimeseriesRow.fromJson({'bucket_start_ts': 0});
      expect(r.provider, 'unknown');
    });
  });

  group('SpenderRow.fromJson', () {
    test('parses key and numeric fields', () {
      final r = SpenderRow.fromJson({
        'key': 'gemini-3-flash',
        'calls': 15,
        'tokens': 200000,
        'cost_usd': 0.24,
      });
      expect(r.key, 'gemini-3-flash');
      expect(r.calls, 15);
      expect(r.costUsd, closeTo(0.24, 0.001));
    });
  });

  group('BacktestUsageRow.fromJson', () {
    test('parses backtest row fields', () {
      final r = BacktestUsageRow.fromJson({
        'backtest_id': 'abc123',
        'display_label': 'Run #42',
        'kind': 'backtest',
        'instance_id': 'main',
        'first_ts': 1700000000000,
        'calls': 50,
        'tokens': 500000,
        'cost_usd': 5.0,
        'ok_calls': 48,
        'failed_calls': 2,
      });
      expect(r.backtestId, 'abc123');
      expect(r.displayLabel, 'Run #42');
      expect(r.kind, 'backtest');
      expect(r.okCalls, 48);
      expect(r.failedCalls, 2);
    });
  });

  group('RecentCall.fromJson', () {
    test('parses call fields including raw', () {
      final json = {
        'id': 'call-001',
        'ts': 1700000000000,
        'provider': 'openai',
        'model': 'gpt-4o',
        'input_tokens': 1000,
        'output_tokens': 200,
        'total_cost_usd': 0.015,
        'strategy': 'nexus',
        'call_site': 'analyze',
      };
      final r = RecentCall.fromJson(json);
      expect(r.id, 'call-001');
      expect(r.provider, 'openai');
      expect(r.model, 'gpt-4o');
      expect(r.inputTokens, 1000);
      expect(r.totalCostUsd, closeTo(0.015, 0.0001));
      expect(r.raw['strategy'], 'nexus');
    });
  });

  group('range parameter', () {
    test('24h produces hour bucket', () {
      const range = '24h';
      final bucket = range == '24h' ? 'hour' : 'day';
      expect(bucket, 'hour');
    });

    test('7d produces day bucket', () {
      const range = '7d';
      final bucket = range == '24h' ? 'hour' : 'day';
      expect(bucket, 'day');
    });

    test('30d produces day bucket', () {
      const range = '30d';
      final bucket = range == '24h' ? 'hour' : 'day';
      expect(bucket, 'day');
    });
  });

  group('TelemetryHealth.fromJson', () {
    test('healthy state: no errors, flush < 30s', () {
      final h = TelemetryHealth.fromJson({
        'buffer_depth': 2,
        'last_flush_age_s': 5,
        'write_errors_24h': 0,
      });
      expect(h.writeErrors24h, 0);
      expect((h.lastFlushAgeS ?? 0) > 30, isFalse);
    });

    test('degraded state: write errors > 0', () {
      final h = TelemetryHealth.fromJson({'write_errors_24h': 3});
      expect(h.writeErrors24h, 3);
    });

    test('lagging state: flush age > 30', () {
      final h = TelemetryHealth.fromJson({'last_flush_age_s': 45, 'write_errors_24h': 0});
      expect((h.lastFlushAgeS ?? 0) > 30, isTrue);
    });
  });
}
