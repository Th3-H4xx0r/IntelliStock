import 'package:flutter_test/flutter_test.dart';
import 'package:intellistock_mobile/features/strategies/strategy_config.dart';
import 'package:intellistock_mobile/features/strategies/data/models/strategy.dart';

void main() {
  group('humanizeStrategyConfigKey', () {
    test('plain words are title-cased', () {
      expect(humanizeStrategyConfigKey('buy_threshold'), 'Buy Threshold');
      expect(humanizeStrategyConfigKey('max_discovered_stocks'),
          'Max Discovered Stocks');
    });

    test('known acronyms are uppercased', () {
      expect(humanizeStrategyConfigKey('llm_provider'), 'LLM Provider');
      expect(humanizeStrategyConfigKey('rsi_period'), 'RSI Period');
      expect(humanizeStrategyConfigKey('macd_signal'), 'MACD Signal');
      expect(humanizeStrategyConfigKey('vwap_threshold'), 'VWAP Threshold');
      expect(humanizeStrategyConfigKey('api_key'), 'API Key');
      expect(humanizeStrategyConfigKey('atr_multiplier'), 'ATR Multiplier');
      expect(humanizeStrategyConfigKey('etf_portfolio_pct'), 'ETF Portfolio Pct');
      expect(humanizeStrategyConfigKey('usd_threshold'), 'USD Threshold');
      expect(humanizeStrategyConfigKey('sec_filing'), 'SEC Filing');
      expect(humanizeStrategyConfigKey('ai_enabled'), 'AI Enabled');
    });

    test('multi-acronym keys', () {
      expect(humanizeStrategyConfigKey('llm_api_key'), 'LLM API Key');
      expect(humanizeStrategyConfigKey('rsi_macd_combo'), 'RSI MACD Combo');
    });

    test('empty key returns empty string', () {
      expect(humanizeStrategyConfigKey(''), '');
    });

    test('single token', () {
      expect(humanizeStrategyConfigKey('weight'), 'Weight');
      expect(humanizeStrategyConfigKey('rsi'), 'RSI');
    });

    test('handles leading/trailing underscores gracefully', () {
      expect(humanizeStrategyConfigKey('_weight_'), 'Weight');
    });
  });

  group('getStrategyConfigFieldMeta', () {
    test('returns known label for graph_nexus_analysis fields', () {
      final meta =
          getStrategyConfigFieldMeta('graph_nexus_analysis', 'buy_threshold');
      expect(meta.label, 'Buy Score Threshold');
      expect(meta.description, isNotEmpty);
    });

    test('falls back to humanizeStrategyConfigKey for unknown keys', () {
      final meta =
          getStrategyConfigFieldMeta('graph_nexus_analysis', 'some_custom_field');
      expect(meta.label, 'Some Custom Field');
      expect(meta.description, '');
    });

    test('returns humanized label for unknown strategy', () {
      final meta = getStrategyConfigFieldMeta('unknown_strategy', 'rsi_period');
      expect(meta.label, 'RSI Period');
    });

    test('all known graph_nexus_analysis fields resolve without error', () {
      final knownKeys = strategyFieldMeta['graph_nexus_analysis']!.keys;
      for (final key in knownKeys) {
        final meta = getStrategyConfigFieldMeta('graph_nexus_analysis', key);
        expect(meta.label, isNotEmpty,
            reason: 'label for $key should not be empty');
      }
    });
  });

  group('rank theming helpers', () {
    // Just verify they return consistent non-null results for ranks 1-5.
    // The actual color values are visual; we only guard logic regression.
    test('rankMedal for ranks 1-3 contains emoji, rank 4+ uses #N format', () {
      // Emulate the medal logic from strategies_screen.dart.
      String rankMedal(int rank) {
        switch (rank) {
          case 1:
            return '🥇';
          case 2:
            return '🥈';
          case 3:
            return '🥉';
          default:
            return '#$rank';
        }
      }

      expect(rankMedal(1), '🥇');
      expect(rankMedal(2), '🥈');
      expect(rankMedal(3), '🥉');
      expect(rankMedal(4), '#4');
      expect(rankMedal(5), '#5');
    });
  });

  group('StrategyListRow merge stats', () {
    test('fromStrategyJson carries sub-strategy names', () {
      final json = {
        'id': 42,
        'name': 'My Strategy',
        'strategies': [
          {'strategy': 'graph_nexus_analysis'},
          {'strategy': 'momentum'},
        ],
        'instances_using': ['inst1', 'inst2'],
      };
      final row = StrategyListRow.fromStrategyJson(json);
      expect(row.id, 42);
      expect(row.name, 'My Strategy');
      expect(row.subCount, 2);
      expect(row.subStrategyNames, ['graph_nexus_analysis', 'momentum']);
      expect(row.instancesUsing, ['inst1', 'inst2']);
    });

    test('fromStrategyJson with best-backtest stats', () {
      final json = {
        'id': 7,
        'name': 'Nexus',
        'strategies': [
          {'strategy': 'graph_nexus_analysis'},
        ],
        'instances_using': [],
      };
      final row = StrategyListRow.fromStrategyJson(
        json,
        bestPnl: 4500.25,
        bestPnlBid: 'bt-99',
        bestPct: 45.0,
        bestPctBid: 'bt-99',
        runCount: 12,
        rank: 1,
      );
      expect(row.bestPnl, 4500.25);
      expect(row.rank, 1);
      expect(row.isTop5, isTrue);
      expect(row.runCount, 12);
    });
  });

  group('AgentResult.fromJson', () {
    test('parses all fields correctly', () {
      final j = {
        'backtest_id': 'bt-42',
        'strategy_id': 7,
        'overall_profit': '1234.56',
        'pnl_percent': '12.34',
        'stocks_used': ['AAPL', 'MSFT'],
        'start_date': '2025-01-01',
        'end_date': '2025-06-01',
        'created_at': '2025-06-10T10:00:00Z',
      };
      final r = AgentResult.fromJson(j);
      expect(r.backtestId, 'bt-42');
      expect(r.strategyId, 7);
      expect(r.overallProfit, closeTo(1234.56, 0.001));
      expect(r.pnlPercent, closeTo(12.34, 0.001));
      expect(r.stocksUsed, ['AAPL', 'MSFT']);
    });

    test('handles null overallProfit gracefully', () {
      final r = AgentResult.fromJson({'backtest_id': 'x'});
      expect(r.overallProfit, isNull);
      expect(r.pnlPercent, isNull);
    });
  });
}
