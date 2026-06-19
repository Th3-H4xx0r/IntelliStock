import 'package:flutter_test/flutter_test.dart';
import 'package:intellistock_mobile/features/dashboard/data/nexus_models.dart';

void main() {
  test('MarketTrend.fromJson parses direction/strength/tickers', () {
    final t = MarketTrend.fromJson({
      'id': 'inst_ai',
      'name': 'AI Rally',
      'status': 'active',
      'direction': 'bullish',
      'strength': 0.78,
      'affected_tickers': ['NVDA', 'AMD'],
      'reversal_articles': [],
      'end_date': null,
    });
    expect(t.name, 'AI Rally');
    expect(t.bullish, isTrue);
    expect(t.strength, 0.78);
    expect(t.tickers, ['NVDA', 'AMD']);
    expect(t.hasReversal, isFalse);
  });

  test('MarketTrend prefers ended_at then end_date; weakening => hasReversal', () {
    final t = MarketTrend.fromJson({
      'name': 'X',
      'status': 'weakening',
      'direction': 'bearish',
      'ended_at': '2026-06-16T00:00:00',
      'end_date': '2026-06-10',
    });
    expect(t.endedAt, '2026-06-16T00:00:00');
    expect(t.bullish, isFalse);
    expect(t.hasReversal, isTrue);
  });

  test('NexusTrendsView.reversalWatch filters active weakening/reversal', () {
    final view = NexusTrendsView(active: [
      MarketTrend.fromJson({'name': 'A', 'status': 'active', 'direction': 'bullish'}),
      MarketTrend.fromJson({
        'name': 'B',
        'status': 'active',
        'direction': 'bullish',
        'reversal_articles': [
          {'headline': 'x'}
        ]
      }),
    ], recentlyEnded: const []);
    expect(view.reversalWatch.map((t) => t.name), ['B']);
    expect(view.isEmpty, isFalse);
  });

  test('BackfillItem.fromJson reads normalized server shape', () {
    final b = BackfillItem.fromJson({
      'ticker': 'NVDA',
      'score': 1.4,
      'n_paths': 3,
      'source': 'propagation',
      'priority': true
    });
    expect(b.ticker, 'NVDA');
    expect(b.priority, isTrue);
    expect(b.nPaths, 3);
  });

  test('OutcomeStats.fromJson parses hit rate + recent; correctness flag', () {
    final s = OutcomeStats.fromJson({
      'hit_rate': 0.6,
      'n': 10,
      'n_correct': 6,
      'avg_return': 1.2,
      'recent': [
        {
          'symbol': 'A',
          'action_intent': 'buy',
          'latest_return': 2.0,
          'dominant_event_type': 'm_and_a',
          'entry_date': '2026-06-01'
        }
      ],
    });
    expect(s.hitRate, 0.6);
    expect(s.n, 10);
    expect(s.recent.single.symbol, 'A');
    expect(s.recent.single.correct, isTrue);
  });

  test('WatchlistSummary.fromJson parses count + newest', () {
    final w = WatchlistSummary.fromJson({
      'count': 42,
      'newest': [
        {'symbol': 'NVDA', 'first_seen_bar': 30, 'ret_20d': 1.1}
      ],
    });
    expect(w.count, 42);
    expect(w.newest.single.symbol, 'NVDA');
    expect(w.isEmpty, isFalse);
  });

  test('DiscoveredStock + TradeRationale fromJson', () {
    final d = DiscoveredStock.fromJson({
      'ticker': 'AVGO',
      'source': 'sector_peer',
      'source_ticker': 'NVDA',
      'discovered_at': '2026-06-15'
    });
    expect(d.ticker, 'AVGO');
    expect(d.sourceTicker, 'NVDA');
    final r = TradeRationale.fromJson({
      'symbol': 'NVDA',
      'reason': 'capex',
      'dominant_event_type': 'supply_disruption',
      'score': 3.0
    });
    expect(r.symbol, 'NVDA');
    expect(r.reason, 'capex');
  });
}
