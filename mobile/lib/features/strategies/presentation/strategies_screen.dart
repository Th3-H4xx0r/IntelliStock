import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';
import '../../../core/theme/app_colors.dart';
import '../../../core/theme/app_text_styles.dart';
import '../../../core/widgets/common_widgets.dart';
import '../../../core/widgets/glass_card.dart';
import '../../../core/widgets/app_button.dart';
import '../../../core/widgets/material_symbols.dart';
import '../../../core/formatters/formatters.dart';
import '../../../core/widgets/skeleton.dart';
import '../application/strategies_controller.dart';
import '../data/models/strategy.dart';

// ── Rank helpers ──────────────────────────────────────────────────────────────

Color _rankAccentColor(int rank) {
  switch (rank) {
    case 1:
      return const Color(0xFFF59E0B); // amber-500
    case 2:
      return const Color(0xFF94A3B8); // slate-400
    case 3:
      return const Color(0xFFEA580C); // orange-600
    default:
      return AppColors.primary;
  }
}

Color _rankTextColor(int rank) {
  switch (rank) {
    case 1:
      return const Color(0xFFFCD34D); // amber-300
    case 2:
      return const Color(0xFFCBD5E1); // slate-300
    case 3:
      return const Color(0xFFFB923C); // orange-400
    default:
      return AppColors.primary;
  }
}

String _rankMedal(int rank) {
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

// ── Screen ────────────────────────────────────────────────────────────────────

class StrategiesScreen extends ConsumerWidget {
  const StrategiesScreen({super.key});

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final ctrl = ref.watch(strategiesControllerProvider);
    final notifier = ref.read(strategiesControllerProvider.notifier);

    return Scaffold(
      backgroundColor: AppColors.canvas,
      body: SafeArea(
        child: RefreshIndicator(
          color: AppColors.primary,
          onRefresh: notifier.fetchAll,
          child: CustomScrollView(
            physics: const AlwaysScrollableScrollPhysics(),
            slivers: [
              // ── Header ────────────────────────────────────────────────────
              SliverToBoxAdapter(
                child: Padding(
                  padding: const EdgeInsets.fromLTRB(16, 24, 16, 0),
                  child: Column(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      Row(
                        crossAxisAlignment: CrossAxisAlignment.start,
                        children: [
                          Expanded(
                            child: Column(
                              crossAxisAlignment: CrossAxisAlignment.start,
                              children: [
                                Text('Strategies', style: AppTextStyles.h1),
                                const SizedBox(height: 4),
                                Text(
                                  'All trading strategies with their best AI backtest results.',
                                  style: AppTextStyles.body
                                      .copyWith(color: AppColors.textDim),
                                ),
                              ],
                            ),
                          ),
                          const SizedBox(width: 12),
                          // Refresh
                          GestureDetector(
                            onTap: notifier.fetchAll,
                            child: Container(
                              padding: const EdgeInsets.all(8),
                              decoration: BoxDecoration(
                                border: Border.all(color: AppColors.border),
                                borderRadius: BorderRadius.circular(10),
                              ),
                              child: ctrl.loading
                                  ? SizedBox(
                                      width: 16,
                                      height: 16,
                                      child: CircularProgressIndicator(
                                        strokeWidth: 1.5,
                                        color: AppColors.textMuted,
                                      ),
                                    )
                                  : Icon(symbol('refresh'),
                                      size: 18, color: AppColors.textMuted),
                            ),
                          ),
                        ],
                      ),
                      const SizedBox(height: 12),
                      Row(
                        children: [
                          Expanded(
                            child: AppButton.semantic(
                              label: 'Create Strategy',
                              color: AppColors.primary,
                              icon: symbol('add_circle'),
                              dense: true,
                              onPressed: () => context.push(
                                  '/instances?createStrategy=1'),
                            ),
                          ),
                          const SizedBox(width: 8),
                          // Per-page selector
                          _PerPageSelector(
                            value: ctrl.perPage,
                            onChanged: notifier.setPerPage,
                          ),
                        ],
                      ),
                      const SizedBox(height: 20),
                    ],
                  ),
                ),
              ),

              // ── Loading skeleton ───────────────────────────────────────────
              if (ctrl.loading)
                SliverPadding(
                  padding: const EdgeInsets.symmetric(horizontal: 16),
                  sliver: SliverList(
                    delegate: SliverChildListDelegate([
                      // Top-5 rank card skeletons (3 large)
                      ...List.generate(
                        3,
                        (_) => const Padding(
                          padding: EdgeInsets.only(bottom: 10),
                          child: _Top5CardSkeleton(),
                        ),
                      ),
                      const SizedBox(height: 6),
                      // Strategy row skeletons (5 compact)
                      ...List.generate(
                        5,
                        (_) => const Padding(
                          padding: EdgeInsets.only(bottom: 10),
                          child: _StrategyRowSkeleton(),
                        ),
                      ),
                    ]),
                  ),
                )
              else ...[
                // ── Top-5 section ────────────────────────────────────────────
                if (notifier.top5Enriched.isNotEmpty)
                  SliverToBoxAdapter(
                    child: Padding(
                      padding: const EdgeInsets.fromLTRB(16, 0, 16, 20),
                      child: Column(
                        crossAxisAlignment: CrossAxisAlignment.start,
                        children: [
                          Row(
                            children: [
                              Icon(symbol('emoji_events'),
                                  size: 18,
                                  color: AppColors.warning),
                              const SizedBox(width: 6),
                              Text(
                                'TOP ${notifier.top5Enriched.length} BEST STRATEGIES',
                                style: AppTextStyles.eyebrow.copyWith(
                                  color: AppColors.warning,
                                ),
                              ),
                              const SizedBox(width: 8),
                              Text('ranked by P&L%',
                                  style: AppTextStyles.nano.copyWith(
                                      color: AppColors.textFaint)),
                            ],
                          ),
                          const SizedBox(height: 12),
                          ...notifier.top5Enriched
                              .map((e) => Padding(
                                    padding: const EdgeInsets.only(bottom: 10),
                                    child: _Top5Card(entry: e),
                                  ))
                              ,
                        ],
                      ),
                    ),
                  ),

                // ── Sort controls ────────────────────────────────────────────
                SliverToBoxAdapter(
                  child: Padding(
                    padding: const EdgeInsets.fromLTRB(16, 0, 16, 12),
                    child: _SortBar(
                      sortField: ctrl.sortField,
                      sortAsc: ctrl.sortAsc,
                      onSort: notifier.setSort,
                    ),
                  ),
                ),

                // ── Strategy cards ───────────────────────────────────────────
                if (notifier.pagedRows.isEmpty)
                  SliverPadding(
                    padding: const EdgeInsets.symmetric(horizontal: 16),
                    sliver: SliverToBoxAdapter(
                      child: EmptyState(
                        icon: symbol('schema'),
                        title: 'No strategies found.',
                        subtitle: 'Create your first strategy to get started.',
                      ),
                    ),
                  )
                else
                  SliverPadding(
                    padding:
                        const EdgeInsets.symmetric(horizontal: 16),
                    sliver: SliverList(
                      delegate: SliverChildBuilderDelegate(
                        (ctx, i) {
                          final row = notifier.pagedRows[i];
                          return Padding(
                            padding: const EdgeInsets.only(bottom: 10),
                            child: _StrategyCard(
                              row: row,
                              onTap: () =>
                                  ctx.push('/strategies/${row.id}'),
                              onBacktest: row.bestPnlBid != null
                                  ? () => ctx
                                      .push('/backtests/${row.bestPnlBid}')
                                  : null,
                            ),
                          );
                        },
                        childCount: notifier.pagedRows.length,
                      ),
                    ),
                  ),

                // ── Pagination ───────────────────────────────────────────────
                SliverToBoxAdapter(
                  child: Padding(
                    padding: const EdgeInsets.fromLTRB(16, 4, 16, 24),
                    child: _PaginationBar(
                      page: ctrl.page,
                      totalPages: notifier.totalPages,
                      totalCount: notifier.rows.length,
                      onPage: notifier.setPage,
                    ),
                  ),
                ),
              ],
            ],
          ),
        ),
      ),
    );
  }
}

// ── Top-5 rank card ───────────────────────────────────────────────────────────

class _Top5Card extends StatelessWidget {
  const _Top5Card({required this.entry});

  final Map<String, dynamic> entry;

  @override
  Widget build(BuildContext context) {
    final rank = (entry['rank'] as num?)?.toInt() ?? 5;
    final accentColor = _rankAccentColor(rank);
    final textColor = _rankTextColor(rank);
    final name = (entry['strategy_name'] ?? 'Strategy').toString();
    final pnl = (entry['overall_profit'] is num)
        ? (entry['overall_profit'] as num).toDouble()
        : double.tryParse(entry['overall_profit']?.toString() ?? '');
    final pct = (entry['pnl_percent'] is num)
        ? (entry['pnl_percent'] as num).toDouble()
        : double.tryParse(entry['pnl_percent']?.toString() ?? '');
    final subs = (entry['sub_strategies'] as List? ?? const [])
        .map((s) => s.toString())
        .toList();
    final stratId = entry['strategy_id'];
    final backtestId = entry['backtest_id'];

    return GlassCard(
      padding: EdgeInsets.zero,
      borderColor: accentColor.withValues(alpha: 0.4),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          // Top accent line
          Container(
            height: 2,
            decoration: BoxDecoration(
              gradient: LinearGradient(
                colors: [
                  accentColor.withValues(alpha: 0.6),
                  accentColor,
                  accentColor.withValues(alpha: 0.6),
                ],
              ),
            ),
          ),
          Padding(
            padding: const EdgeInsets.all(14),
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                // Header row
                Row(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    // Rank icon
                    Container(
                      width: 38,
                      height: 38,
                      decoration: BoxDecoration(
                        color: accentColor.withValues(alpha: 0.15),
                        borderRadius: BorderRadius.circular(10),
                        border: Border.all(
                            color: accentColor.withValues(alpha: 0.3)),
                      ),
                      alignment: Alignment.center,
                      child: rank <= 3
                          ? Text(_rankMedal(rank),
                              style: const TextStyle(fontSize: 18))
                          : Text('#$rank',
                              style: AppTextStyles.cardTitle
                                  .copyWith(color: textColor)),
                    ),
                    const SizedBox(width: 10),
                    Expanded(
                      child: Column(
                        crossAxisAlignment: CrossAxisAlignment.start,
                        children: [
                          Row(
                            children: [
                              Expanded(
                                child: Text(
                                  name,
                                  style: AppTextStyles.cardTitle
                                      .copyWith(color: textColor),
                                  overflow: TextOverflow.ellipsis,
                                ),
                              ),
                              const SizedBox(width: 6),
                              Container(
                                padding: const EdgeInsets.symmetric(
                                    horizontal: 6, vertical: 2),
                                decoration: BoxDecoration(
                                  color: accentColor.withValues(alpha: 0.2),
                                  borderRadius: BorderRadius.circular(4),
                                  border: Border.all(
                                      color:
                                          accentColor.withValues(alpha: 0.3)),
                                ),
                                child: Text(
                                  'RANK $rank',
                                  style: AppTextStyles.nano.copyWith(
                                    color: textColor,
                                    fontWeight: FontWeight.w700,
                                  ),
                                ),
                              ),
                            ],
                          ),
                          const SizedBox(height: 2),
                          Text(
                            '${subs.length} sub-strategies',
                            style: AppTextStyles.nano
                                .copyWith(color: AppColors.textFaint),
                          ),
                        ],
                      ),
                    ),
                  ],
                ),
                const SizedBox(height: 12),
                // P&L stats
                Row(
                  children: [
                    _MiniStat(
                        label: 'BEST P&L', value: fmtPnl(pnl), color: pnlColor(pnl)),
                    const SizedBox(width: 20),
                    _MiniStat(
                        label: 'BEST P&L%',
                        value: fmtPct(pct),
                        color: pnlColor(pct)),
                  ],
                ),
                // Sub-strategy pills
                if (subs.isNotEmpty) ...[
                  const SizedBox(height: 10),
                  Wrap(
                    spacing: 4,
                    runSpacing: 4,
                    children: [
                      ...subs.take(5).map((s) => Container(
                            padding: const EdgeInsets.symmetric(
                                horizontal: 6, vertical: 2),
                            decoration: BoxDecoration(
                              color: accentColor.withValues(alpha: 0.1),
                              borderRadius: BorderRadius.circular(4),
                              border: Border.all(
                                  color: accentColor.withValues(alpha: 0.2)),
                            ),
                            child: Text(
                              s,
                              style: AppTextStyles.nano.copyWith(
                                  fontFamily: 'JetBrains Mono',
                                  color:
                                      textColor.withValues(alpha: 0.8)),
                            ),
                          )),
                      if (subs.length > 5)
                        Text('+${subs.length - 5} more',
                            style: AppTextStyles.nano
                                .copyWith(color: AppColors.textFaint)),
                    ],
                  ),
                ],
                const SizedBox(height: 12),
                // Action buttons
                Row(
                  children: [
                    if (stratId != null)
                      _RankButton(
                        label: 'View Strategy',
                        icon: symbol('open_in_new'),
                        accentColor: accentColor,
                        textColor: textColor,
                        onTap: () =>
                            context.push('/strategies/$stratId'),
                      ),
                    if (stratId != null) const SizedBox(width: 8),
                    if (backtestId != null)
                      _RankButton(
                        label: 'Backtest',
                        icon: symbol('analytics'),
                        accentColor: AppColors.textDim,
                        textColor: AppColors.textMuted,
                        onTap: () =>
                            context.push('/backtests/$backtestId'),
                      ),
                  ],
                ),
              ],
            ),
          ),
        ],
      ),
    );
  }
}

class _MiniStat extends StatelessWidget {
  const _MiniStat(
      {required this.label, required this.value, required this.color});

  final String label;
  final String value;
  final Color color;

  @override
  Widget build(BuildContext context) {
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Text(label,
            style: AppTextStyles.nano.copyWith(
                color: AppColors.textFaint, letterSpacing: 0.8)),
        const SizedBox(height: 2),
        Text(value,
            style: AppTextStyles.value.copyWith(
                color: color, fontWeight: FontWeight.w700)),
      ],
    );
  }
}

class _RankButton extends StatelessWidget {
  const _RankButton({
    required this.label,
    required this.icon,
    required this.accentColor,
    required this.textColor,
    required this.onTap,
  });

  final String label;
  final IconData icon;
  final Color accentColor;
  final Color textColor;
  final VoidCallback onTap;

  @override
  Widget build(BuildContext context) {
    return GestureDetector(
      onTap: onTap,
      child: Container(
        padding:
            const EdgeInsets.symmetric(horizontal: 10, vertical: 6),
        decoration: BoxDecoration(
          color: accentColor.withValues(alpha: 0.1),
          borderRadius: BorderRadius.circular(8),
          border:
              Border.all(color: accentColor.withValues(alpha: 0.3)),
        ),
        child: Row(
          mainAxisSize: MainAxisSize.min,
          children: [
            Icon(icon, size: 13, color: textColor),
            const SizedBox(width: 4),
            Text(label,
                style: AppTextStyles.micro
                    .copyWith(color: textColor, fontWeight: FontWeight.w600)),
          ],
        ),
      ),
    );
  }
}

// ── Strategy list card ────────────────────────────────────────────────────────

class _StrategyCard extends StatelessWidget {
  const _StrategyCard({
    required this.row,
    required this.onTap,
    this.onBacktest,
  });

  final StrategyListRow row;
  final VoidCallback onTap;
  final VoidCallback? onBacktest;

  @override
  Widget build(BuildContext context) {
    final rankColor = row.isTop5 ? _rankAccentColor(row.rank!) : null;
    final rankText = row.isTop5 ? _rankTextColor(row.rank!) : null;

    return GlassCard(
      padding: const EdgeInsets.all(14),
      borderColor: rankColor?.withValues(alpha: 0.3),
      onTap: onTap,
      child: Row(
        children: [
          // Icon / rank tile
          Container(
            width: 34,
            height: 34,
            decoration: BoxDecoration(
              color: rankColor != null
                  ? rankColor.withValues(alpha: 0.15)
                  : AppColors.surface,
              borderRadius: BorderRadius.circular(8),
              border: Border.all(
                  color: rankColor != null
                      ? rankColor.withValues(alpha: 0.3)
                      : AppColors.border),
            ),
            alignment: Alignment.center,
            child: row.isTop5 && row.rank! <= 3
                ? Text(_rankMedal(row.rank!),
                    style: const TextStyle(fontSize: 16))
                : row.isTop5
                    ? Text('#${row.rank}',
                        style: AppTextStyles.micro
                            .copyWith(color: rankText!, fontWeight: FontWeight.w700))
                    : Icon(symbol('schema'),
                        size: 16, color: AppColors.textFaint),
          ),
          const SizedBox(width: 10),
          // Name + meta
          Expanded(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Row(
                  children: [
                    Expanded(
                      child: Text(
                        row.name,
                        style: AppTextStyles.cardTitle.copyWith(
                          color: rankText ?? AppColors.textHi,
                        ),
                        overflow: TextOverflow.ellipsis,
                      ),
                    ),
                    if (row.isTop5) ...[
                      const SizedBox(width: 6),
                      Container(
                        padding: const EdgeInsets.symmetric(
                            horizontal: 5, vertical: 1),
                        decoration: BoxDecoration(
                          color: rankColor!.withValues(alpha: 0.2),
                          borderRadius: BorderRadius.circular(3),
                          border: Border.all(
                              color: rankColor.withValues(alpha: 0.3)),
                        ),
                        child: Text(
                          'RANK ${row.rank}',
                          style: AppTextStyles.nano.copyWith(
                            color: rankText,
                            fontWeight: FontWeight.w700,
                          ),
                        ),
                      ),
                    ],
                  ],
                ),
                const SizedBox(height: 2),
                Row(
                  children: [
                    Text('ID ${row.id}',
                        style: AppTextStyles.nano
                            .copyWith(color: AppColors.textFaint)),
                    const SizedBox(width: 8),
                    Text('${row.subCount} subs',
                        style: AppTextStyles.nano
                            .copyWith(color: AppColors.textFaint)),
                    if (row.runCount > 0) ...[
                      const SizedBox(width: 8),
                      Text('${row.runCount} runs',
                          style: AppTextStyles.nano
                              .copyWith(color: AppColors.textFaint)),
                    ],
                  ],
                ),
              ],
            ),
          ),
          const SizedBox(width: 8),
          // P&L stats
          Column(
            crossAxisAlignment: CrossAxisAlignment.end,
            children: [
              if (row.bestPnl != null)
                Text(
                  fmtPnl(row.bestPnl),
                  style: AppTextStyles.mono(12,
                      color: pnlColor(row.bestPnl),
                      weight: FontWeight.w600),
                ),
              if (row.bestPct != null)
                Text(
                  fmtPct(row.bestPct),
                  style: AppTextStyles.mono(11,
                      color: pnlColor(row.bestPct),
                      weight: FontWeight.w600),
                ),
              if (row.bestPnl == null && row.bestPct == null)
                Text('—',
                    style:
                        AppTextStyles.meta.copyWith(color: AppColors.textFaint)),
            ],
          ),
          const SizedBox(width: 8),
          // Backtest button
          if (onBacktest != null)
            GestureDetector(
              onTap: onBacktest,
              child: Container(
                padding: const EdgeInsets.all(6),
                decoration: BoxDecoration(
                  border: Border.all(color: AppColors.border),
                  borderRadius: BorderRadius.circular(6),
                ),
                child: Icon(symbol('analytics'),
                    size: 14, color: AppColors.textMuted),
              ),
            ),
        ],
      ),
    );
  }
}

// ── Sort bar ──────────────────────────────────────────────────────────────────

class _SortBar extends StatelessWidget {
  const _SortBar({
    required this.sortField,
    required this.sortAsc,
    required this.onSort,
  });

  final StrategySortField sortField;
  final bool sortAsc;
  final void Function(StrategySortField) onSort;

  @override
  Widget build(BuildContext context) {
    return SingleChildScrollView(
      scrollDirection: Axis.horizontal,
      child: Row(
        children: [
          Text('Sort:',
              style:
                  AppTextStyles.nano.copyWith(color: AppColors.textFaint)),
          const SizedBox(width: 8),
          ...[
            (StrategySortField.name, 'Name'),
            (StrategySortField.bestPnl, 'Best P&L'),
            (StrategySortField.bestPct, 'Best P&L%'),
            (StrategySortField.backtests, 'Backtests'),
          ].map((pair) {
            final (field, label) = pair;
            final active = sortField == field;
            return Padding(
              padding: const EdgeInsets.only(right: 6),
              child: GestureDetector(
                onTap: () => onSort(field),
                child: Container(
                  padding: const EdgeInsets.symmetric(
                      horizontal: 10, vertical: 5),
                  decoration: BoxDecoration(
                    color: active
                        ? AppColors.fill(AppColors.primary)
                        : Colors.transparent,
                    borderRadius: BorderRadius.circular(8),
                    border: Border.all(
                        color: active
                            ? AppColors.stroke(AppColors.primary)
                            : AppColors.border),
                  ),
                  child: Row(
                    children: [
                      Text(label,
                          style: AppTextStyles.micro.copyWith(
                            color: active
                                ? AppColors.primary
                                : AppColors.textMuted,
                            fontWeight: active ? FontWeight.w600 : null,
                          )),
                      if (active) ...[
                        const SizedBox(width: 3),
                        Icon(
                          sortAsc
                              ? symbol('arrow_upward')
                              : symbol('arrow_downward'),
                          size: 11,
                          color: AppColors.primary,
                        ),
                      ],
                    ],
                  ),
                ),
              ),
            );
          }),
        ],
      ),
    );
  }
}

// ── Per-page selector ─────────────────────────────────────────────────────────

class _PerPageSelector extends StatelessWidget {
  const _PerPageSelector({required this.value, required this.onChanged});

  final int value;
  final void Function(int) onChanged;

  static const _options = [10, 20, 50];

  @override
  Widget build(BuildContext context) {
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 6),
      decoration: BoxDecoration(
        border: Border.all(color: AppColors.border),
        borderRadius: BorderRadius.circular(10),
      ),
      child: DropdownButtonHideUnderline(
        child: DropdownButton<int>(
          value: value,
          isDense: true,
          dropdownColor: AppColors.panel,
          style: AppTextStyles.micro.copyWith(color: AppColors.textMd),
          items: _options
              .map((n) => DropdownMenuItem(
                    value: n,
                    child: Text('$n/page'),
                  ))
              .toList(),
          onChanged: (v) {
            if (v != null) onChanged(v);
          },
        ),
      ),
    );
  }
}

// ── Pagination bar ────────────────────────────────────────────────────────────

class _PaginationBar extends StatelessWidget {
  const _PaginationBar({
    required this.page,
    required this.totalPages,
    required this.totalCount,
    required this.onPage,
  });

  final int page;
  final int totalPages;
  final int totalCount;
  final void Function(int) onPage;

  @override
  Widget build(BuildContext context) {
    if (totalPages <= 1) {
      return Text(
        '$totalCount strategies',
        style: AppTextStyles.nano.copyWith(color: AppColors.textFaint),
      );
    }
    return Row(
      children: [
        Text('$totalCount strategies · page $page of $totalPages',
            style: AppTextStyles.nano.copyWith(color: AppColors.textDim)),
        const Spacer(),
        _PageBtn(
          icon: symbol('arrow_back'),
          enabled: page > 1,
          onTap: () => onPage(page - 1),
        ),
        const SizedBox(width: 4),
        // Compact: show up to 5 page buttons.
        ...List.generate(
          totalPages > 5 ? 5 : totalPages,
          (i) {
            int p;
            if (totalPages <= 5) {
              p = i + 1;
            } else if (page <= 3) {
              p = i + 1;
            } else if (page >= totalPages - 2) {
              p = totalPages - 4 + i;
            } else {
              p = page - 2 + i;
            }
            final active = p == page;
            return Padding(
              padding: const EdgeInsets.only(right: 4),
              child: GestureDetector(
                onTap: () => onPage(p),
                child: Container(
                  width: 28,
                  height: 28,
                  alignment: Alignment.center,
                  decoration: BoxDecoration(
                    color: active
                        ? AppColors.fill(AppColors.primary)
                        : Colors.transparent,
                    borderRadius: BorderRadius.circular(6),
                    border: Border.all(
                        color: active
                            ? AppColors.stroke(AppColors.primary)
                            : AppColors.border),
                  ),
                  child: Text(
                    '$p',
                    style: AppTextStyles.micro.copyWith(
                      color: active ? AppColors.primary : AppColors.textMuted,
                      fontWeight: active ? FontWeight.w600 : null,
                    ),
                  ),
                ),
              ),
            );
          },
        ),
        const SizedBox(width: 4),
        _PageBtn(
          icon: symbol('arrow_forward'),
          enabled: page < totalPages,
          onTap: () => onPage(page + 1),
        ),
      ],
    );
  }
}

class _PageBtn extends StatelessWidget {
  const _PageBtn(
      {required this.icon, required this.enabled, required this.onTap});

  final IconData icon;
  final bool enabled;
  final VoidCallback onTap;

  @override
  Widget build(BuildContext context) {
    return GestureDetector(
      onTap: enabled ? onTap : null,
      child: Opacity(
        opacity: enabled ? 1 : 0.3,
        child: Container(
          width: 28,
          height: 28,
          alignment: Alignment.center,
          decoration: BoxDecoration(
            border: Border.all(color: AppColors.border),
            borderRadius: BorderRadius.circular(6),
          ),
          child: Icon(icon, size: 14, color: AppColors.textMuted),
        ),
      ),
    );
  }
}

// ── Skeleton widgets ──────────────────────────────────────────────────────────

class _Top5CardSkeleton extends StatelessWidget {
  const _Top5CardSkeleton();

  @override
  Widget build(BuildContext context) {
    return GlassCard(
      padding: EdgeInsets.zero,
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          // Top accent line placeholder
          Skeleton(height: 2, radius: 0),
          Padding(
            padding: const EdgeInsets.all(14),
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                // Rank icon + name
                Row(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Skeleton(width: 38, height: 38, radius: 10),
                    const SizedBox(width: 10),
                    Expanded(
                      child: Column(
                        crossAxisAlignment: CrossAxisAlignment.start,
                        children: [
                          Skeleton.line(width: 160, height: 14),
                          const SizedBox(height: 4),
                          Skeleton.line(width: 90, height: 10),
                        ],
                      ),
                    ),
                    Skeleton(width: 52, height: 20, radius: 4),
                  ],
                ),
                const SizedBox(height: 12),
                // P&L stats
                Row(
                  children: [
                    Column(
                      crossAxisAlignment: CrossAxisAlignment.start,
                      children: [
                        Skeleton.line(width: 50, height: 9),
                        const SizedBox(height: 4),
                        Skeleton(width: 80, height: 18, radius: 4),
                      ],
                    ),
                    const SizedBox(width: 20),
                    Column(
                      crossAxisAlignment: CrossAxisAlignment.start,
                      children: [
                        Skeleton.line(width: 60, height: 9),
                        const SizedBox(height: 4),
                        Skeleton(width: 70, height: 18, radius: 4),
                      ],
                    ),
                  ],
                ),
                const SizedBox(height: 10),
                // Sub-strategy pills
                Row(
                  children: [
                    Skeleton(width: 60, height: 18, radius: 4),
                    const SizedBox(width: 4),
                    Skeleton(width: 72, height: 18, radius: 4),
                    const SizedBox(width: 4),
                    Skeleton(width: 54, height: 18, radius: 4),
                  ],
                ),
                const SizedBox(height: 12),
                // Action buttons
                Row(
                  children: [
                    Skeleton(width: 100, height: 28, radius: 8),
                    const SizedBox(width: 8),
                    Skeleton(width: 80, height: 28, radius: 8),
                  ],
                ),
              ],
            ),
          ),
        ],
      ),
    );
  }
}

class _StrategyRowSkeleton extends StatelessWidget {
  const _StrategyRowSkeleton();

  @override
  Widget build(BuildContext context) {
    return GlassCard(
      padding: const EdgeInsets.all(14),
      child: Row(
        children: [
          // Icon/rank tile
          Skeleton(width: 34, height: 34, radius: 8),
          const SizedBox(width: 10),
          // Name + meta
          Expanded(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Skeleton.line(width: 130, height: 13),
                const SizedBox(height: 5),
                Skeleton.line(width: 90, height: 10),
              ],
            ),
          ),
          const SizedBox(width: 8),
          // P&L
          Column(
            crossAxisAlignment: CrossAxisAlignment.end,
            children: [
              Skeleton(width: 60, height: 13, radius: 4),
              const SizedBox(height: 4),
              Skeleton(width: 48, height: 11, radius: 4),
            ],
          ),
          const SizedBox(width: 8),
          Skeleton(width: 26, height: 26, radius: 6),
        ],
      ),
    );
  }
}
