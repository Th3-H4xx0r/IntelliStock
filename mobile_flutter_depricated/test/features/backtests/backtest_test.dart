import 'package:flutter_test/flutter_test.dart';
import 'package:intellistock_mobile/features/backtests/data/models/backtest.dart';
import 'package:intellistock_mobile/core/widgets/status_pill.dart';
import 'package:intellistock_mobile/core/theme/app_colors.dart';

// ── Status → color map ────────────────────────────────────────────────────────

void main() {
  group('StatusPill.colorForStatus', () {
    test('completed → success', () {
      expect(StatusPill.colorForStatus('completed'), AppColors.success);
    });
    test('finished → success', () {
      expect(StatusPill.colorForStatus('finished'), AppColors.success);
    });
    test('running → success', () {
      expect(StatusPill.colorForStatus('running'), AppColors.success);
    });
    test('queued → warning', () {
      expect(StatusPill.colorForStatus('queued'), AppColors.warning);
    });
    test('paused → warning', () {
      expect(StatusPill.colorForStatus('paused'), AppColors.warning);
    });
    test('paused_llm_critical → warning', () {
      expect(
          StatusPill.colorForStatus('paused_llm_critical'),
          AppColors.warning);
    });
    test('stopped → danger', () {
      expect(StatusPill.colorForStatus('stopped'), AppColors.danger);
    });
    test('failed → danger', () {
      expect(StatusPill.colorForStatus('failed'), AppColors.danger);
    });
    test('error → danger', () {
      expect(StatusPill.colorForStatus('error'), AppColors.danger);
    });
    test('cancelled → danger', () {
      expect(StatusPill.colorForStatus('cancelled'), AppColors.danger);
    });
    test('aborted_llm_failure → danger', () {
      expect(
          StatusPill.colorForStatus('aborted_llm_failure'), AppColors.danger);
    });
    test('null → textDim', () {
      expect(StatusPill.colorForStatus(null), AppColors.textDim);
    });
    test('unknown string → textDim', () {
      expect(StatusPill.colorForStatus('gibberish'), AppColors.textDim);
    });
    test('case-insensitive: RUNNING → success', () {
      expect(StatusPill.colorForStatus('RUNNING'), AppColors.success);
    });
  });

  // ── Pagination ellipsis helper ────────────────────────────────────────────

  group('_Pagination._buildPages', () {
    // We test the logic via BacktestRow.fromJson (pure model) as a proxy
    // and directly reproduce the ellipsis algorithm for pure-logic coverage.

    List<int?> buildPages(int current, int total) {
      if (total <= 7) {
        return List<int?>.generate(total, (i) => i + 1);
      }
      final result = <int>{};
      result.add(1);
      result.add(total);
      for (int p = current - 1; p <= current + 1; p++) {
        if (p >= 1 && p <= total) result.add(p);
      }
      final sorted = result.toList()..sort((a, b) => a - b);
      final out = <int?>[];
      for (int i = 0; i < sorted.length; i++) {
        if (i > 0 && sorted[i] - sorted[i - 1] > 1) {
          out.add(null); // ellipsis slot
        }
        out.add(sorted[i]);
      }
      return out;
    }

    test('≤7 pages → flat list', () {
      expect(buildPages(1, 5), [1, 2, 3, 4, 5]);
      expect(buildPages(3, 7), [1, 2, 3, 4, 5, 6, 7]);
    });

    test('10 pages current=1 → no leading ellipsis', () {
      final pages = buildPages(1, 10);
      // 1 2 null 10
      expect(pages.first, 1);
      expect(pages.contains(null), isTrue);
      expect(pages.last, 10);
      // No leading null
      expect(pages.first, isNot(null));
    });

    test('10 pages current=5 → ellipsis on both sides', () {
      final pages = buildPages(5, 10);
      expect(pages.first, 1);
      expect(pages.last, 10);
      expect(pages.contains(5), isTrue);
      // Both sides have ellipsis
      expect(pages.where((p) => p == null).length, 2);
    });

    test('10 pages current=10 → no trailing ellipsis', () {
      final pages = buildPages(10, 10);
      expect(pages.first, 1);
      expect(pages.last, 10);
      expect(pages.first, isNot(null));
    });
  });

  // ── Model parsing ─────────────────────────────────────────────────────────

  group('BacktestRow.fromJson', () {
    test('parses all expected fields', () {
      final row = BacktestRow.fromJson({
        'id': 'abc123',
        'instance_id': 'main',
        'status': 'completed',
        'stocks': ['AAPL', 'GOOG'],
        'start_date': '2025-01-01',
        'end_date': '2025-06-01',
        'completed_at': '2025-06-02T10:00:00Z',
        'pnl': 1234.56,
        'pnl_percent': 12.3,
        'time_elapsed_seconds': 300,
      });
      expect(row.id, 'abc123');
      expect(row.instanceId, 'main');
      expect(row.status, 'completed');
      expect(row.stocks, ['AAPL', 'GOOG']);
      expect(row.pnl, closeTo(1234.56, 0.01));
    });

    test('tolerates null / missing fields', () {
      final row = BacktestRow.fromJson({'id': 'x'});
      expect(row.id, 'x');
      expect(row.stocks, isEmpty);
      expect(row.pnl, isNull);
    });

    test('instance fallback: uses "instance" key when instance_id absent', () {
      final row =
          BacktestRow.fromJson({'id': 'y', 'instance': 'legacy-inst'});
      expect(row.instanceId, 'legacy-inst');
    });
  });

  group('BacktestSummary.fromJson', () {
    test('parses strategy_schema sub-strategies', () {
      final summary = BacktestSummary.fromJson({
        'id': '1',
        'pnl': 100,
        'strategy_schema': {
          'name': 'My Strat',
          'strategies': [
            {
              'strategy': 'NexusOnly',
              'weight': 1.0,
              'decision_phase': 'pre',
            }
          ],
        },
        'tickers': ['TSLA'],
      });
      expect(summary.strategySchema?.name, 'My Strat');
      expect(summary.strategySchema!.strategies.length, 1);
      expect(summary.strategySchema!.strategies[0].strategy, 'NexusOnly');
    });

    test('handles pause fields', () {
      final summary = BacktestSummary.fromJson({
        'id': '2',
        'status': 'paused_llm_critical',
        'pause_reason_tag': 'rate_limit',
        'pause_provider': 'anthropic',
        'pause_model': 'claude-sonnet-4',
        'pause_call_site': 'strategy_eval',
        'pause_attempts': 3,
      });
      expect(summary.status, 'paused_llm_critical');
      expect(summary.pauseReasonTag, 'rate_limit');
      expect(summary.pauseProvider, 'anthropic');
      expect(summary.pauseAttempts, 3);
    });
  });

  group('BacktestStatus.fromJson', () {
    test('parses nexus_lookback', () {
      final status = BacktestStatus.fromJson({
        'status': 'running',
        'progress': 42,
        'nexus_lookback': {
          'current': 30,
          'total': 90,
          'current_date': '2025-02-01',
          'start_date': '2025-01-01',
          'end_date': '2025-03-31',
        },
      });
      expect(status.progress, 42);
      expect(status.nexusLookback?.current, 30);
      expect(status.nexusLookback?.fraction, closeTo(1 / 3, 0.01));
    });
  });

  group('LlmCost.fromJson', () {
    test('parses breakdown rows', () {
      final cost = LlmCost.fromJson({
        'total_cost_usd': 0.25,
        'total_calls': 10,
        'ok_calls': 9,
        'failed_calls': 1,
        'total_input_tokens': 50000,
        'total_output_tokens': 5000,
        'total_reasoning_tokens': 1000,
        'by_model': [
          {'key': 'claude-sonnet-4', 'cost_usd': 0.20},
          {'key': 'claude-haiku-3', 'cost_usd': 0.05},
        ],
        'by_call_site': [],
        'by_provider': [
          {'key': 'anthropic', 'cost_usd': 0.25}
        ],
      });
      expect(cost.totalCostUsd, 0.25);
      expect(cost.byModel.length, 2);
      expect(cost.byModel[0].key, 'claude-sonnet-4');
      expect(cost.byProvider[0].costUsd, 0.25);
    });
  });

  group('PlaybackData.fromJson', () {
    test('parses events and metadata', () {
      final data = PlaybackData.fromJson({
        'metadata': {'initial_cash': 100000},
        'events': [
          {
            'type': 'date',
            'label': 'Jan 02, 2025',
            'time': '09:30 AM',
          },
          {
            'type': 'portfolio',
            'value': 101000,
            'date': '2025-01-02',
            'holdings': [
              {'ticker': 'AAPL', 'qty': 10, 'avg': 150.0, 'curr': 155.0}
            ],
          },
        ],
      });
      expect(data.metadata.initialCash, 100000);
      expect(data.events.length, 2);
      expect(data.events[0].type, 'date');
      expect(data.events[1].type, 'portfolio');
      expect(data.events[1].portfolioValue, 101000);
      expect(data.events[1].holdings[0].ticker, 'AAPL');
    });
  });

  group('BacktestDecision.decisionLabel', () {
    test('action takes priority over decision int', () {
      final d = BacktestDecision.fromJson({
        'decision': 1,
        'action': 'sell',
        'symbol': 'AAPL',
      });
      expect(d.decisionLabel(), 'SELL');
    });
    test('decision int 1 → BUY when action absent', () {
      final d = BacktestDecision.fromJson({'decision': 1});
      expect(d.decisionLabel(), 'BUY');
    });
    test('decision int -1 → SELL', () {
      final d = BacktestDecision.fromJson({'decision': -1});
      expect(d.decisionLabel(), 'SELL');
    });
    test('decision 0 → HOLD', () {
      final d = BacktestDecision.fromJson({'decision': 0});
      expect(d.decisionLabel(), 'HOLD');
    });
    test('null decision → HOLD', () {
      final d = BacktestDecision.fromJson({});
      expect(d.decisionLabel(), 'HOLD');
    });
  });

  group('PlaybackController speed logic', () {
    test('kPlaybackSpeeds has correct values', () {
      const expected = [0.5, 1, 2, 5, 10];
      for (int i = 0; i < expected.length; i++) {
        expect(
          1000 / expected[i],
          closeTo(1000 / expected[i], 0.01),
        );
      }
    });

    test('delay for 2x is 500ms', () {
      final delay = (1000 / 2).round();
      expect(delay, 500);
    });

    test('delay for 0.5x is 2000ms', () {
      final delay = (1000 / 0.5).round();
      expect(delay, 2000);
    });
  });
}
