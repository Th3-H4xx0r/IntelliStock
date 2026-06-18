import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';

import '../../../core/formatters/formatters.dart';
import '../../../core/theme/app_colors.dart';
import '../../../core/theme/app_text_styles.dart';
import '../../../core/widgets/glass_card.dart';
import '../../../core/widgets/material_symbols.dart';
import '../../../core/widgets/skeleton.dart';
import '../../stock/presentation/stock_screen.dart';
import '../application/dashboard_controller.dart';
import '../application/insights_controller.dart';
import '../application/portfolio_analytics.dart';
import '../application/selected_account_controller.dart';
import '../data/insight_models.dart';

/// The "Insights" + "Bot ideas" block beneath the portfolio section: today's
/// movers, day P&L, diversification, sector allocation, and the bot's
/// discovered opportunities + detected market trends. Scoped to the selected
/// account; renders nothing until one resolves.
class InsightsSection extends ConsumerWidget {
  const InsightsSection({super.key});

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
        Text('Insights', style: AppTextStyles.h3),
        const SizedBox(height: 14),
        _TodaysMoversStrip(brokerageId: id),
        Row(
          crossAxisAlignment: CrossAxisAlignment.stretch,
          children: [
            Expanded(child: _DayPnlTile(brokerageId: id)),
            const SizedBox(width: 12),
            Expanded(child: _DiversificationTile(brokerageId: id)),
          ],
        ),
        const SizedBox(height: 12),
        _SectorAllocationCard(brokerageId: id),
        _BotIdeasGroup(brokerageId: id),
      ],
    );
  }
}

// ── Today's movers ────────────────────────────────────────────────────────────

class _TodaysMoversStrip extends ConsumerWidget {
  const _TodaysMoversStrip({required this.brokerageId});
  final String brokerageId;

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final movers = ref.watch(todaysMoversProvider(brokerageId)).valueOrNull;
    if (movers == null || movers.isEmpty) return const SizedBox.shrink();
    return Padding(
      padding: const EdgeInsets.only(bottom: 12),
      child: SizedBox(
        height: 38,
        child: ListView.separated(
          scrollDirection: Axis.horizontal,
          itemCount: movers.length,
          separatorBuilder: (_, _) => const SizedBox(width: 8),
          itemBuilder: (_, i) => _MoverChip(mover: movers[i], brokerageId: brokerageId),
        ),
      ),
    );
  }
}

class _MoverChip extends StatelessWidget {
  const _MoverChip({required this.mover, required this.brokerageId});
  final Mover mover;
  final String brokerageId;

  @override
  Widget build(BuildContext context) {
    final up = mover.pct >= 0;
    final c = up ? AppColors.success : AppColors.danger;
    return GestureDetector(
      behavior: HitTestBehavior.opaque,
      onTap: () => context.push('/stock/${mover.symbol}',
          extra: StockScreenArgs(brokerageId: brokerageId)),
      child: Container(
        padding: const EdgeInsets.symmetric(horizontal: 11, vertical: 7),
        decoration: BoxDecoration(
          color: AppColors.fill(c),
          borderRadius: BorderRadius.circular(9),
          border: Border.all(color: c.withValues(alpha: 0.25)),
        ),
        child: Row(
          mainAxisSize: MainAxisSize.min,
          children: [
            Text(mover.symbol,
                style: AppTextStyles.micro
                    .copyWith(color: AppColors.textHi, fontWeight: FontWeight.w700)),
            const SizedBox(width: 6),
            Text('${up ? '▲' : '▼'} ${fmtPct(mover.pct)}',
                style: AppTextStyles.micro
                    .copyWith(color: c, fontWeight: FontWeight.w700)),
          ],
        ),
      ),
    );
  }
}

// ── Day P&L + Diversification tiles ─────────────────────────────────────────

class _DayPnlTile extends ConsumerWidget {
  const _DayPnlTile({required this.brokerageId});
  final String brokerageId;

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final async = ref.watch(dayChangeProvider(brokerageId));
    final d = async.valueOrNull;
    return GlassCard(
      padding: const EdgeInsets.all(14),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          _tileLabel('TODAY'),
          const SizedBox(height: 6),
          if (async.isLoading && d == null)
            const Skeleton(width: 90, height: 22, radius: 6)
          else if (d == null)
            Text('—', style: AppTextStyles.valueLg)
          else
            _DayPnlValue(d: d),
        ],
      ),
    );
  }
}

class _DayPnlValue extends StatelessWidget {
  const _DayPnlValue({required this.d});
  final DayChange d;

  @override
  Widget build(BuildContext context) {
    final up = d.abs >= 0;
    final c = up ? AppColors.success : AppColors.danger;
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Text(fmtPnl(d.abs),
            style: AppTextStyles.valueLg.copyWith(color: c)),
        const SizedBox(height: 2),
        Row(
          children: [
            Icon(up ? symbol('trending_up') : symbol('trending_down'),
                size: 13, color: c),
            const SizedBox(width: 3),
            Text(fmtPct(d.pct),
                style: AppTextStyles.micro
                    .copyWith(color: c, fontWeight: FontWeight.w700)),
          ],
        ),
      ],
    );
  }
}

class _DiversificationTile extends ConsumerWidget {
  const _DiversificationTile({required this.brokerageId});
  final String brokerageId;

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final async = ref.watch(concentrationProvider(brokerageId));
    final s = async.valueOrNull;
    return GlassCard(
      padding: const EdgeInsets.all(14),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          _tileLabel('DIVERSIFICATION'),
          const SizedBox(height: 6),
          if (async.isLoading && s == null)
            const Skeleton(width: 70, height: 22, radius: 6)
          else if (s == null || s.isEmpty)
            Text('—', style: AppTextStyles.valueLg)
          else
            _DiversificationValue(s: s),
        ],
      ),
    );
  }
}

class _DiversificationValue extends StatelessWidget {
  const _DiversificationValue({required this.s});
  final ConcentrationStats s;

  @override
  Widget build(BuildContext context) {
    // Green when well-spread, amber mid, red when concentrated.
    final c = s.score >= 66
        ? AppColors.success
        : (s.score >= 33 ? const Color(0xFFFBBF24) : AppColors.danger);
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Row(
          crossAxisAlignment: CrossAxisAlignment.baseline,
          textBaseline: TextBaseline.alphabetic,
          children: [
            Text('${s.score}',
                style: AppTextStyles.valueLg.copyWith(color: c)),
            Text(' /100',
                style: AppTextStyles.micro.copyWith(color: AppColors.textFaint)),
          ],
        ),
        const SizedBox(height: 2),
        Text('Top ${s.topWeight.round()}% · ${s.count} holdings',
            maxLines: 1,
            overflow: TextOverflow.ellipsis,
            style: AppTextStyles.micro.copyWith(color: AppColors.textMuted)),
      ],
    );
  }
}

// ── Sector allocation ───────────────────────────────────────────────────────

const _sectorPalette = <Color>[
  Color(0xFFA78BFA),
  Color(0xFF34D399),
  Color(0xFF60A5FA),
  Color(0xFFFBBF24),
  Color(0xFFF472B6),
  Color(0xFF22D3EE),
  Color(0xFFF87171),
  Color(0xFFA3E635),
];

class _SectorAllocationCard extends ConsumerWidget {
  const _SectorAllocationCard({required this.brokerageId});
  final String brokerageId;

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final async = ref.watch(sectorAllocationProvider(brokerageId));
    final slices = async.valueOrNull;
    // Hide entirely once loaded with nothing (e.g. all cash / no sectors).
    if (slices != null && slices.isEmpty) return const SizedBox.shrink();
    return Padding(
      padding: const EdgeInsets.only(bottom: 12),
      child: GlassCard(
        padding: const EdgeInsets.all(16),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            _tileLabel('SECTOR ALLOCATION'),
            const SizedBox(height: 12),
            if (slices == null)
              const Skeleton(height: 14, radius: 7)
            else ...[
              _SectorBar(slices: slices),
              const SizedBox(height: 14),
              _SectorLegend(slices: slices),
            ],
          ],
        ),
      ),
    );
  }
}

class _SectorBar extends StatelessWidget {
  const _SectorBar({required this.slices});
  final List<SectorSlice> slices;

  @override
  Widget build(BuildContext context) {
    return ClipRRect(
      borderRadius: BorderRadius.circular(7),
      child: SizedBox(
        height: 14,
        child: Row(
          children: [
            for (var i = 0; i < slices.length; i++)
              Expanded(
                flex: (slices[i].pct * 100).round().clamp(1, 1000000),
                child: Container(
                    color: _sectorPalette[i % _sectorPalette.length]),
              ),
          ],
        ),
      ),
    );
  }
}

class _SectorLegend extends StatelessWidget {
  const _SectorLegend({required this.slices});
  final List<SectorSlice> slices;

  @override
  Widget build(BuildContext context) {
    return Column(
      children: [
        for (var i = 0; i < slices.length; i++)
          Padding(
            padding: const EdgeInsets.only(bottom: 8),
            child: Row(
              children: [
                Container(
                  width: 9,
                  height: 9,
                  decoration: BoxDecoration(
                    color: _sectorPalette[i % _sectorPalette.length],
                    borderRadius: BorderRadius.circular(2.5),
                  ),
                ),
                const SizedBox(width: 8),
                Expanded(
                  child: Text(slices[i].sector,
                      maxLines: 1,
                      overflow: TextOverflow.ellipsis,
                      style: AppTextStyles.micro
                          .copyWith(color: AppColors.textMd)),
                ),
                Text('${slices[i].pct.round()}%',
                    style: AppTextStyles.micro.copyWith(
                        color: AppColors.textHi, fontWeight: FontWeight.w700)),
              ],
            ),
          ),
      ],
    );
  }
}

// ── Bot ideas: discovered opportunities + trends ─────────────────────────────

class _BotIdeasGroup extends ConsumerWidget {
  const _BotIdeasGroup({required this.brokerageId});
  final String brokerageId;

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final discovered = ref.watch(discoveredProvider(brokerageId)).valueOrNull;
    final trends = ref.watch(trendsProvider(brokerageId)).valueOrNull;
    final hasDiscovered = discovered != null && discovered.isNotEmpty;
    final hasTrends = trends != null && trends.isNotEmpty;
    // Whole group hides when neither engine has produced anything.
    if (!hasDiscovered && !hasTrends) return const SizedBox.shrink();
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        const SizedBox(height: 20),
        Text('Bot ideas', style: AppTextStyles.h3),
        const SizedBox(height: 14),
        if (hasTrends) _TrendsCard(trends: trends, brokerageId: brokerageId),
        if (hasDiscovered)
          _DiscoveredCard(stocks: discovered, brokerageId: brokerageId),
      ],
    );
  }
}

class _TrendsCard extends StatelessWidget {
  const _TrendsCard({required this.trends, required this.brokerageId});
  final List<MarketTrend> trends;
  final String brokerageId;

  @override
  Widget build(BuildContext context) {
    return Padding(
      padding: const EdgeInsets.only(bottom: 12),
      child: GlassCard(
        padding: const EdgeInsets.all(16),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Row(
              children: [
                Icon(symbol('trending_up'), size: 15, color: AppColors.primary),
                const SizedBox(width: 6),
                _tileLabel('MARKET TRENDS'),
              ],
            ),
            const SizedBox(height: 12),
            for (var i = 0; i < trends.length; i++) ...[
              if (i > 0)
                Divider(
                    height: 18,
                    color: Colors.white.withValues(alpha: 0.06)),
              _TrendRow(trend: trends[i], brokerageId: brokerageId),
            ],
          ],
        ),
      ),
    );
  }
}

class _TrendRow extends StatelessWidget {
  const _TrendRow({required this.trend, required this.brokerageId});
  final MarketTrend trend;
  final String brokerageId;

  @override
  Widget build(BuildContext context) {
    final title = trend.title.trim().isNotEmpty ? trend.title.trim() : 'Trend';
    // Strength may arrive 0..1 or 0..100; normalize to a 0..1 bar fraction.
    final raw = trend.strength;
    final frac = raw == null
        ? null
        : (raw > 1 ? raw / 100 : raw).clamp(0.0, 1.0);
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Row(
          children: [
            Expanded(
              child: Text(title,
                  maxLines: 1,
                  overflow: TextOverflow.ellipsis,
                  style: AppTextStyles.bodyHi
                      .copyWith(fontWeight: FontWeight.w600)),
            ),
            if (frac != null)
              Text('${(frac * 100).round()}%',
                  style: AppTextStyles.nano.copyWith(color: AppColors.primary)),
          ],
        ),
        if (frac != null) ...[
          const SizedBox(height: 6),
          ClipRRect(
            borderRadius: BorderRadius.circular(3),
            child: LinearProgressIndicator(
              value: frac,
              minHeight: 4,
              backgroundColor: Colors.white.withValues(alpha: 0.08),
              valueColor:
                  const AlwaysStoppedAnimation<Color>(AppColors.primary),
            ),
          ),
        ],
        if (trend.symbols.isNotEmpty) ...[
          const SizedBox(height: 8),
          Wrap(
            spacing: 6,
            runSpacing: 6,
            children: [
              for (final s in trend.symbols.take(8))
                GestureDetector(
                  behavior: HitTestBehavior.opaque,
                  onTap: () => context.push('/stock/$s',
                      extra: StockScreenArgs(brokerageId: brokerageId)),
                  child: Container(
                    padding:
                        const EdgeInsets.symmetric(horizontal: 8, vertical: 3),
                    decoration: BoxDecoration(
                      color: AppColors.fill(AppColors.primary),
                      borderRadius: BorderRadius.circular(6),
                    ),
                    child: Text(s,
                        style: AppTextStyles.nano.copyWith(
                            color: AppColors.primary,
                            fontWeight: FontWeight.w700)),
                  ),
                ),
            ],
          ),
        ],
      ],
    );
  }
}

class _DiscoveredCard extends StatelessWidget {
  const _DiscoveredCard({required this.stocks, required this.brokerageId});
  final List<DiscoveredStock> stocks;
  final String brokerageId;

  @override
  Widget build(BuildContext context) {
    return GlassCard(
      padding: const EdgeInsets.fromLTRB(16, 16, 16, 6),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Row(
            children: [
              Icon(symbol('auto_awesome'), size: 15, color: AppColors.primary),
              const SizedBox(width: 6),
              _tileLabel('DISCOVERED'),
            ],
          ),
          const SizedBox(height: 6),
          for (final s in stocks)
            _DiscoveredRow(stock: s, brokerageId: brokerageId),
        ],
      ),
    );
  }
}

class _DiscoveredRow extends StatelessWidget {
  const _DiscoveredRow({required this.stock, required this.brokerageId});
  final DiscoveredStock stock;
  final String brokerageId;

  @override
  Widget build(BuildContext context) {
    final reason = stock.reason.trim();
    return GestureDetector(
      behavior: HitTestBehavior.opaque,
      onTap: () => context.push('/stock/${stock.ticker}',
          extra: StockScreenArgs(brokerageId: brokerageId)),
      child: Padding(
        padding: const EdgeInsets.symmetric(vertical: 10),
        child: Row(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Expanded(
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Row(
                    children: [
                      Text(stock.ticker,
                          style: AppTextStyles.bodyHi
                              .copyWith(fontWeight: FontWeight.w700)),
                      if (stock.discoveredAt != null) ...[
                        const SizedBox(width: 8),
                        Text(fmtRelative(stock.discoveredAt),
                            style: AppTextStyles.nano
                                .copyWith(color: AppColors.textDim)),
                      ],
                    ],
                  ),
                  if (reason.isNotEmpty) ...[
                    const SizedBox(height: 3),
                    Text(reason,
                        maxLines: 2,
                        overflow: TextOverflow.ellipsis,
                        style: AppTextStyles.micro
                            .copyWith(color: AppColors.textMd, height: 1.35)),
                  ],
                ],
              ),
            ),
            const SizedBox(width: 8),
            Icon(symbol('arrow_forward'),
                size: 13, color: AppColors.textFaint),
          ],
        ),
      ),
    );
  }
}

// ── Shared bits ───────────────────────────────────────────────────────────────

Widget _tileLabel(String s) => Text(
      s,
      style: AppTextStyles.nano.copyWith(
        color: AppColors.textFaint,
        fontWeight: FontWeight.w700,
        letterSpacing: 0.8,
      ),
    );
