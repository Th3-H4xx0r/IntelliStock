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

/// The "Insights" + "Market" block beneath the portfolio section: today's
/// movers, day P&L, diversification, and sector allocation for the account,
/// plus real market data (major indices + sector performance). Scoped to the
/// selected account; renders nothing until one resolves.
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
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Expanded(child: _DayPnlTile(brokerageId: id)),
            const SizedBox(width: 12),
            Expanded(child: _DiversificationTile(brokerageId: id)),
          ],
        ),
        const SizedBox(height: 12),
        _SectorAllocationCard(brokerageId: id),
        _RiskCard(brokerageId: id),
        const SizedBox(height: 20),
        Text('Market', style: AppTextStyles.h3),
        const SizedBox(height: 14),
        const _MarketIndicesCard(),
        const _SectorPerformanceCard(),
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
      frosted: true,
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
      frosted: true,
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

// ── Risk (volatility / drawdown / Sharpe from the equity curve) ──────────────

class _RiskCard extends ConsumerWidget {
  const _RiskCard({required this.brokerageId});
  final String brokerageId;

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final async = ref.watch(riskMetricsProvider(brokerageId));
    final r = async.valueOrNull;
    if (r != null && r.isEmpty) return const SizedBox.shrink();
    return Padding(
      padding: const EdgeInsets.only(bottom: 12),
      child: GlassCard(
        frosted: true,
        padding: const EdgeInsets.all(16),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            _tileLabel('RISK'),
            const SizedBox(height: 12),
            if (r == null)
              const Skeleton(height: 22, radius: 6)
            else
              Row(
                children: [
                  Expanded(
                      child: _riskMetric(
                          'Volatility', '${r.volatility.round()}%')),
                  Expanded(
                      child: _riskMetric(
                          'Max drawdown', '${r.maxDrawdown.round()}%')),
                  Expanded(
                      child: _riskMetric('Sharpe',
                          r.sharpe == null ? '—' : r.sharpe!.toStringAsFixed(2))),
                ],
              ),
          ],
        ),
      ),
    );
  }

  Widget _riskMetric(String label, String value) => Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Text(value,
              maxLines: 1,
              overflow: TextOverflow.ellipsis,
              style: AppTextStyles.value.copyWith(fontWeight: FontWeight.w700)),
          const SizedBox(height: 3),
          Text(label,
              maxLines: 1,
              overflow: TextOverflow.ellipsis,
              style: AppTextStyles.nano.copyWith(color: AppColors.textFaint)),
        ],
      );
}

// ── Sector allocation (your portfolio, by sector) ────────────────────────────

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
        frosted: true,
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

// ── Market indices (real market data) ────────────────────────────────────────

class _MarketIndicesCard extends ConsumerWidget {
  const _MarketIndicesCard();

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final async = ref.watch(marketIndicesProvider);
    final quotes = async.valueOrNull;
    if (quotes != null && quotes.isEmpty) return const SizedBox.shrink();
    return Padding(
      padding: const EdgeInsets.only(bottom: 12),
      child: GlassCard(
        frosted: true,
        padding: const EdgeInsets.all(16),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            _tileLabel('INDICES'),
            const SizedBox(height: 12),
            if (quotes == null)
              const Skeleton(height: 34, radius: 7)
            else
              Row(
                children: [
                  for (var i = 0; i < quotes.length; i++) ...[
                    if (i > 0) const SizedBox(width: 10),
                    Expanded(child: _IndexCell(quote: quotes[i])),
                  ],
                ],
              ),
          ],
        ),
      ),
    );
  }
}

class _IndexCell extends StatelessWidget {
  const _IndexCell({required this.quote});
  final MarketQuote quote;

  @override
  Widget build(BuildContext context) {
    final up = quote.pct >= 0;
    final c = up ? AppColors.success : AppColors.danger;
    return GestureDetector(
      behavior: HitTestBehavior.opaque,
      onTap: () =>
          context.push('/stock/${quote.symbol}', extra: const StockScreenArgs()),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Text(quote.label,
              maxLines: 1,
              overflow: TextOverflow.ellipsis,
              style: AppTextStyles.nano.copyWith(color: AppColors.textMuted)),
          const SizedBox(height: 4),
          Text('${up ? '▲' : '▼'} ${fmtPct(quote.pct)}',
              maxLines: 1,
              overflow: TextOverflow.ellipsis,
              style: AppTextStyles.micro
                  .copyWith(color: c, fontWeight: FontWeight.w700)),
        ],
      ),
    );
  }
}

// ── Sector performance (today's market sectors, ranked) ──────────────────────

class _SectorPerformanceCard extends ConsumerWidget {
  const _SectorPerformanceCard();

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final async = ref.watch(sectorPerformanceProvider);
    final quotes = async.valueOrNull;
    if (quotes != null && quotes.isEmpty) return const SizedBox.shrink();
    return Padding(
      padding: const EdgeInsets.only(bottom: 12),
      child: GlassCard(
        frosted: true,
        padding: const EdgeInsets.all(16),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            _tileLabel('SECTOR PERFORMANCE'),
            const SizedBox(height: 12),
            if (quotes == null)
              const Skeleton(height: 80, radius: 7)
            else
              for (final q in quotes) _SectorPerfRow(quote: q),
          ],
        ),
      ),
    );
  }
}

class _SectorPerfRow extends StatelessWidget {
  const _SectorPerfRow({required this.quote});
  final MarketQuote quote;

  @override
  Widget build(BuildContext context) {
    final up = quote.pct >= 0;
    final c = up ? AppColors.success : AppColors.danger;
    // Scale the bar against a nominal 3% daily move so typical moves are visible.
    final frac = (quote.pct.abs() / 3.0).clamp(0.0, 1.0);
    return Padding(
      padding: const EdgeInsets.symmetric(vertical: 5),
      child: Row(
        children: [
          SizedBox(
            width: 108,
            child: Text(quote.label,
                maxLines: 1,
                overflow: TextOverflow.ellipsis,
                style: AppTextStyles.micro.copyWith(color: AppColors.textMd)),
          ),
          Expanded(
            child: ClipRRect(
              borderRadius: BorderRadius.circular(3),
              child: LinearProgressIndicator(
                value: frac,
                minHeight: 5,
                backgroundColor: Colors.white.withValues(alpha: 0.06),
                valueColor: AlwaysStoppedAnimation<Color>(c),
              ),
            ),
          ),
          const SizedBox(width: 10),
          SizedBox(
            width: 58,
            child: Text(fmtPct(quote.pct),
                textAlign: TextAlign.right,
                style: AppTextStyles.micro
                    .copyWith(color: c, fontWeight: FontWeight.w700)),
          ),
        ],
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
