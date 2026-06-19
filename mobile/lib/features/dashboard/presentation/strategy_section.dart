import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';

import '../../../core/theme/app_colors.dart';
import '../../../core/theme/app_text_styles.dart';
import '../../../core/widgets/glass_card.dart';
import '../../../core/widgets/material_symbols.dart';
import '../../../core/widgets/skeleton.dart';
import '../../stock/presentation/stock_screen.dart';
import '../application/dashboard_controller.dart';
import '../application/selected_account_controller.dart';
import '../application/nexus_strategy_controller.dart';
import '../data/nexus_models.dart';

/// Eyebrow label, matching insights_section.dart's _tileLabel.
Widget _label(String s) => Text(
      s,
      style: AppTextStyles.nano.copyWith(
        color: AppColors.textFaint,
        fontWeight: FontWeight.w700,
        letterSpacing: 0.8,
      ),
    );

void _openStock(BuildContext context, String sym, String brokerageId) {
  if (sym.isEmpty) return;
  context.push('/stock/$sym', extra: StockScreenArgs(brokerageId: brokerageId));
}

GlassCard _cardShell({required List<Widget> children}) => GlassCard(
      frosted: true,
      padding: const EdgeInsets.all(16),
      child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: children),
    );

/// The bot strategy's live telemetry, grouped as its own dashboard section.
/// Every card self-hides when its data is empty, so non-nexus accounts (and
/// accounts where a feature is disabled) see nothing new.
class StrategySection extends ConsumerWidget {
  const StrategySection({super.key});

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final accounts = ref.watch(brokeragesProvider).valueOrNull;
    if (accounts == null || accounts.isEmpty) return const SizedBox.shrink();
    final selectedId = ref.watch(selectedAccountProvider);
    final id = (selectedId != null && accounts.any((a) => a.id == selectedId))
        ? selectedId
        : accounts.first.id;

    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Text('Strategy', style: AppTextStyles.h3),
        const SizedBox(height: 14),
        _MarketTrendsCard(brokerageId: id),
        _ReversalWatchCard(brokerageId: id),
        _BackfillQueueCard(brokerageId: id),
        _DiscoveredStocksCard(brokerageId: id),
        _BotRationaleCard(brokerageId: id),
        _OutcomeScorecardCard(brokerageId: id),
        _MomentumWatchlistCard(brokerageId: id),
      ],
    );
  }
}

// ── 1. Market Trends (active + recently ended) ──────────────────────────────

class _MarketTrendsCard extends ConsumerWidget {
  const _MarketTrendsCard({required this.brokerageId});
  final String brokerageId;

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final view = ref.watch(nexusTrendsProvider(brokerageId)).valueOrNull;
    if (view != null && view.isEmpty) return const SizedBox.shrink();
    return Padding(
      padding: const EdgeInsets.only(bottom: 12),
      child: _cardShell(children: [
        Row(children: [
          Icon(symbol('trending_up'), size: 15, color: AppColors.primary),
          const SizedBox(width: 6),
          _label('MARKET TRENDS'),
        ]),
        const SizedBox(height: 10),
        if (view == null)
          const Skeleton(height: 80, radius: 7)
        else ...[
          for (final t in view.active) _TrendRow(trend: t, brokerageId: brokerageId),
          if (view.recentlyEnded.isNotEmpty) ...[
            const SizedBox(height: 6),
            _label('RECENTLY ENDED'),
            const SizedBox(height: 4),
            for (final t in view.recentlyEnded) _EndedTrendRow(trend: t),
          ],
        ],
      ]),
    );
  }
}

class _TrendRow extends StatelessWidget {
  const _TrendRow({required this.trend, required this.brokerageId});
  final MarketTrend trend;
  final String brokerageId;

  @override
  Widget build(BuildContext context) {
    final c = trend.bullish ? AppColors.success : AppColors.danger;
    return Padding(
      padding: const EdgeInsets.symmetric(vertical: 6),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Row(children: [
            Icon(trend.bullish ? Icons.arrow_upward : Icons.arrow_downward, size: 13, color: c),
            const SizedBox(width: 6),
            Expanded(
              child: Text(trend.name,
                  maxLines: 1,
                  overflow: TextOverflow.ellipsis,
                  style: AppTextStyles.micro.copyWith(
                      color: AppColors.textHi, fontWeight: FontWeight.w700)),
            ),
            Text('${(trend.strength * 100).round()}%',
                style: AppTextStyles.nano.copyWith(color: c, fontWeight: FontWeight.w700)),
          ]),
          const SizedBox(height: 4),
          ClipRRect(
            borderRadius: BorderRadius.circular(3),
            child: LinearProgressIndicator(
              value: trend.strength.clamp(0.0, 1.0),
              minHeight: 4,
              backgroundColor: Colors.white.withValues(alpha: 0.06),
              valueColor: AlwaysStoppedAnimation<Color>(c),
            ),
          ),
          if (trend.tickers.isNotEmpty) ...[
            const SizedBox(height: 5),
            Wrap(
              spacing: 6,
              children: [
                for (final s in trend.tickers.take(5))
                  GestureDetector(
                    onTap: () => _openStock(context, s, brokerageId),
                    child: Text(s,
                        style: AppTextStyles.nano.copyWith(color: AppColors.textMuted)),
                  ),
                if (trend.tickers.length > 5)
                  Text('+${trend.tickers.length - 5}',
                      style: AppTextStyles.nano.copyWith(color: AppColors.textFaint)),
              ],
            ),
          ],
        ],
      ),
    );
  }
}

class _EndedTrendRow extends StatelessWidget {
  const _EndedTrendRow({required this.trend});
  final MarketTrend trend;

  @override
  Widget build(BuildContext context) {
    return Padding(
      padding: const EdgeInsets.symmetric(vertical: 3),
      child: Row(children: [
        Icon(symbol('check'), size: 12, color: AppColors.textFaint),
        const SizedBox(width: 6),
        Expanded(
          child: Text(trend.name,
              maxLines: 1,
              overflow: TextOverflow.ellipsis,
              style: AppTextStyles.nano.copyWith(color: AppColors.textMuted)),
        ),
        Text(_agoLabel(trend.endedAt),
            style: AppTextStyles.nano.copyWith(color: AppColors.textFaint)),
      ]),
    );
  }
}

/// "ended 2d ago" from an ISO date string; empty when unparseable.
String _agoLabel(String? iso) {
  if (iso == null || iso.isEmpty) return '';
  final dt = DateTime.tryParse(iso);
  if (dt == null) return '';
  final days = DateTime.now().difference(dt).inDays;
  if (days <= 0) return 'ended today';
  return 'ended ${days}d ago';
}

// ── 2. Reversal Watch ───────────────────────────────────────────────────────

class _ReversalWatchCard extends ConsumerWidget {
  const _ReversalWatchCard({required this.brokerageId});
  final String brokerageId;

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final view = ref.watch(nexusTrendsProvider(brokerageId)).valueOrNull;
    final items = view?.reversalWatch ?? const <MarketTrend>[];
    if (items.isEmpty) return const SizedBox.shrink();
    return Padding(
      padding: const EdgeInsets.only(bottom: 12),
      child: _cardShell(children: [
        Row(children: [
          Icon(symbol('warning'), size: 15, color: AppColors.warning),
          const SizedBox(width: 6),
          _label('REVERSAL WATCH'),
        ]),
        const SizedBox(height: 10),
        for (final t in items)
          Padding(
            padding: const EdgeInsets.symmetric(vertical: 5),
            child: Row(children: [
              Expanded(
                child: Text(t.name,
                    maxLines: 1,
                    overflow: TextOverflow.ellipsis,
                    style: AppTextStyles.micro.copyWith(color: AppColors.textHi)),
              ),
              Text('${t.reversalCount} signal${t.reversalCount == 1 ? '' : 's'}',
                  style: AppTextStyles.nano.copyWith(color: AppColors.warning)),
            ]),
          ),
      ]),
    );
  }
}

// ── 3. Backfill Queue ───────────────────────────────────────────────────────

class _BackfillQueueCard extends ConsumerWidget {
  const _BackfillQueueCard({required this.brokerageId});
  final String brokerageId;

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final items = ref.watch(backfillQueueProvider(brokerageId)).valueOrNull;
    if (items != null && items.isEmpty) return const SizedBox.shrink();
    return Padding(
      padding: const EdgeInsets.only(bottom: 12),
      child: _cardShell(children: [
        Row(children: [
          Icon(symbol('hourglass_empty'), size: 15, color: AppColors.info),
          const SizedBox(width: 6),
          _label('BACKFILL QUEUE'),
          const Spacer(),
          if (items != null)
            Text('${items.length} pending',
                style: AppTextStyles.nano.copyWith(color: AppColors.textFaint)),
        ]),
        const SizedBox(height: 10),
        if (items == null)
          const Skeleton(height: 60, radius: 7)
        else
          for (final q in items.take(12))
            GestureDetector(
              behavior: HitTestBehavior.opaque,
              onTap: () => _openStock(context, q.ticker, brokerageId),
              child: Padding(
                padding: const EdgeInsets.symmetric(vertical: 5),
                child: Row(children: [
                  if (q.priority) ...[
                    Icon(symbol('push_pin'), size: 12, color: AppColors.warning),
                    const SizedBox(width: 4),
                  ],
                  SizedBox(
                    width: 64,
                    child: Text(q.ticker,
                        style: AppTextStyles.micro.copyWith(
                            color: AppColors.textHi, fontWeight: FontWeight.w700)),
                  ),
                  Expanded(
                    child: Text(q.source,
                        maxLines: 1,
                        overflow: TextOverflow.ellipsis,
                        style: AppTextStyles.nano.copyWith(color: AppColors.textFaint)),
                  ),
                  if (q.nPaths > 0)
                    Text('${q.nPaths} paths',
                        style: AppTextStyles.nano.copyWith(color: AppColors.textMuted)),
                ]),
              ),
            ),
      ]),
    );
  }
}

// ── 4. Discovered Stocks ────────────────────────────────────────────────────

class _DiscoveredStocksCard extends ConsumerWidget {
  const _DiscoveredStocksCard({required this.brokerageId});
  final String brokerageId;

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final items = ref.watch(discoveredStocksProvider(brokerageId)).valueOrNull;
    if (items != null && items.isEmpty) return const SizedBox.shrink();
    return Padding(
      padding: const EdgeInsets.only(bottom: 12),
      child: _cardShell(children: [
        Row(children: [
          Icon(symbol('search'), size: 15, color: AppColors.teal),
          const SizedBox(width: 6),
          _label('DISCOVERED'),
        ]),
        const SizedBox(height: 10),
        if (items == null)
          const Skeleton(height: 60, radius: 7)
        else
          for (final d in items.take(12))
            GestureDetector(
              behavior: HitTestBehavior.opaque,
              onTap: () => _openStock(context, d.ticker, brokerageId),
              child: Padding(
                padding: const EdgeInsets.symmetric(vertical: 5),
                child: Row(children: [
                  SizedBox(
                    width: 64,
                    child: Text(d.ticker,
                        style: AppTextStyles.micro.copyWith(
                            color: AppColors.textHi, fontWeight: FontWeight.w700)),
                  ),
                  Expanded(
                    child: Text(
                        d.sourceTicker != null && d.sourceTicker!.isNotEmpty
                            ? '${d.source} · via ${d.sourceTicker}'
                            : d.source,
                        maxLines: 1,
                        overflow: TextOverflow.ellipsis,
                        style: AppTextStyles.nano.copyWith(color: AppColors.textFaint)),
                  ),
                ]),
              ),
            ),
      ]),
    );
  }
}

// ── 5. Bot Rationale ────────────────────────────────────────────────────────

class _BotRationaleCard extends ConsumerWidget {
  const _BotRationaleCard({required this.brokerageId});
  final String brokerageId;

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final items = ref.watch(tradeContextsProvider(brokerageId)).valueOrNull;
    if (items != null && items.isEmpty) return const SizedBox.shrink();
    return Padding(
      padding: const EdgeInsets.only(bottom: 12),
      child: _cardShell(children: [
        Row(children: [
          Icon(symbol('psychology'), size: 15, color: AppColors.primary),
          const SizedBox(width: 6),
          _label('BOT RATIONALE'),
        ]),
        const SizedBox(height: 10),
        if (items == null)
          const Skeleton(height: 70, radius: 7)
        else
          for (final r in items.take(8))
            GestureDetector(
              behavior: HitTestBehavior.opaque,
              onTap: () => _openStock(context, r.symbol, brokerageId),
              child: Padding(
                padding: const EdgeInsets.symmetric(vertical: 6),
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Row(children: [
                      Text(r.symbol,
                          style: AppTextStyles.micro.copyWith(
                              color: AppColors.textHi, fontWeight: FontWeight.w700)),
                      const SizedBox(width: 8),
                      if (r.eventType.isNotEmpty)
                        Container(
                          padding: const EdgeInsets.symmetric(horizontal: 6, vertical: 2),
                          decoration: BoxDecoration(
                            color: AppColors.fill(AppColors.primary),
                            borderRadius: BorderRadius.circular(5),
                          ),
                          child: Text(r.eventType,
                              style: AppTextStyles.nano.copyWith(color: AppColors.primary)),
                        ),
                    ]),
                    if (r.reason.isNotEmpty) ...[
                      const SizedBox(height: 3),
                      Text(r.reason,
                          maxLines: 2,
                          overflow: TextOverflow.ellipsis,
                          style: AppTextStyles.nano.copyWith(
                              color: AppColors.textMuted, height: 1.3)),
                    ],
                  ],
                ),
              ),
            ),
      ]),
    );
  }
}

// ── 6. Outcome Scorecard ────────────────────────────────────────────────────

class _OutcomeScorecardCard extends ConsumerWidget {
  const _OutcomeScorecardCard({required this.brokerageId});
  final String brokerageId;

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final s = ref.watch(nexusOutcomesProvider(brokerageId)).valueOrNull;
    if (s != null && s.isEmpty) return const SizedBox.shrink();
    return Padding(
      padding: const EdgeInsets.only(bottom: 12),
      child: _cardShell(children: [
        Row(children: [
          Icon(symbol('score'), size: 15, color: AppColors.info),
          const SizedBox(width: 6),
          _label('OUTCOME SCORECARD'),
        ]),
        const SizedBox(height: 10),
        if (s == null)
          const Skeleton(height: 60, radius: 7)
        else ...[
          Row(children: [
            Text('${(s.hitRate * 100).round()}%',
                style: AppTextStyles.valueLg.copyWith(
                    color: s.hitRate >= 0.5 ? AppColors.success : AppColors.danger)),
            const SizedBox(width: 8),
            Text('hit rate · ${s.nCorrect}/${s.n} signals',
                style: AppTextStyles.nano.copyWith(color: AppColors.textFaint)),
          ]),
          const SizedBox(height: 8),
          for (final o in s.recent)
            Padding(
              padding: const EdgeInsets.symmetric(vertical: 3),
              child: Row(children: [
                Icon(o.correct ? symbol('check') : symbol('close'),
                    size: 12, color: o.correct ? AppColors.success : AppColors.danger),
                const SizedBox(width: 6),
                SizedBox(
                  width: 56,
                  child: Text(o.symbol,
                      style: AppTextStyles.nano.copyWith(
                          color: AppColors.textHi, fontWeight: FontWeight.w700)),
                ),
                Expanded(
                  child: Text(o.eventType,
                      maxLines: 1,
                      overflow: TextOverflow.ellipsis,
                      style: AppTextStyles.nano.copyWith(color: AppColors.textFaint)),
                ),
                Text('${o.latestReturn >= 0 ? '+' : ''}${o.latestReturn.toStringAsFixed(1)}%',
                    style: AppTextStyles.nano.copyWith(
                        color: o.latestReturn >= 0 ? AppColors.success : AppColors.danger)),
              ]),
            ),
        ],
      ]),
    );
  }
}

// ── 7. Momentum Watchlist ───────────────────────────────────────────────────

class _MomentumWatchlistCard extends ConsumerWidget {
  const _MomentumWatchlistCard({required this.brokerageId});
  final String brokerageId;

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final w = ref.watch(momentumWatchlistProvider(brokerageId)).valueOrNull;
    if (w != null && w.isEmpty) return const SizedBox.shrink();
    return Padding(
      padding: const EdgeInsets.only(bottom: 12),
      child: _cardShell(children: [
        Row(children: [
          Icon(symbol('visibility'), size: 15, color: AppColors.teal),
          const SizedBox(width: 6),
          _label('MOMENTUM WATCHLIST'),
          const Spacer(),
          if (w != null)
            Text('monitoring ${w.count}',
                style: AppTextStyles.nano.copyWith(color: AppColors.textFaint)),
        ]),
        const SizedBox(height: 10),
        if (w == null)
          const Skeleton(height: 40, radius: 7)
        else
          Wrap(
            spacing: 8,
            runSpacing: 8,
            children: [
              for (final e in w.newest)
                GestureDetector(
                  onTap: () => _openStock(context, e.symbol, brokerageId),
                  child: Container(
                    padding: const EdgeInsets.symmetric(horizontal: 9, vertical: 6),
                    decoration: BoxDecoration(
                      color: AppColors.fill(AppColors.teal),
                      borderRadius: BorderRadius.circular(8),
                      border: Border.all(color: AppColors.stroke(AppColors.teal)),
                    ),
                    child: Row(mainAxisSize: MainAxisSize.min, children: [
                      Text(e.symbol,
                          style: AppTextStyles.nano.copyWith(
                              color: AppColors.textHi, fontWeight: FontWeight.w700)),
                      if (e.ret20d != 0) ...[
                        const SizedBox(width: 5),
                        Text('${e.ret20d >= 0 ? '+' : ''}${e.ret20d.toStringAsFixed(0)}%',
                            style: AppTextStyles.nano.copyWith(
                                color: e.ret20d >= 0 ? AppColors.success : AppColors.danger)),
                      ],
                    ]),
                  ),
                ),
            ],
          ),
      ]),
    );
  }
}

/// Test-only public wrapper around the private Market Trends card so golden
/// tests can render it in isolation. Not used by the app.
@visibleForTesting
class MarketTrendsCardForTest extends StatelessWidget {
  const MarketTrendsCardForTest({super.key, required this.brokerageId});
  final String brokerageId;
  @override
  Widget build(BuildContext context) => _MarketTrendsCard(brokerageId: brokerageId);
}
