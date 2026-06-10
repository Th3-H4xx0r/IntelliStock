import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';
import '../../../core/theme/app_colors.dart';
import '../../../core/theme/app_text_styles.dart';
import '../../../core/widgets/common_widgets.dart';
import '../../../core/widgets/glass_card.dart';
import '../../../core/widgets/app_button.dart';
import '../../../core/widgets/app_background.dart';
import '../../../core/widgets/material_symbols.dart';
import '../../../core/formatters/formatters.dart';
import '../../../core/widgets/skeleton.dart';
import '../application/strategies_controller.dart';
import '../data/models/strategy.dart';
import '../data/strategy_repository.dart' as strategy_repo;
import '../strategy_config.dart';

// ── Phase theming ─────────────────────────────────────────────────────────────

Color _phaseColor(String phase) {
  switch (phase.toLowerCase()) {
    case 'pre':
      return AppColors.info; // sky
    case 'post':
      return const Color(0xFFC084FC); // purple-400
    case 'entry':
      return AppColors.success; // emerald
    case 'exit':
      return AppColors.danger; // red
    default:
      return AppColors.textMuted;
  }
}

// ── Screen ────────────────────────────────────────────────────────────────────

class StrategyDetailScreen extends ConsumerWidget {
  const StrategyDetailScreen({super.key, required this.strategyId});

  final String strategyId;

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final ctrl = ref.watch(
        strategyDetailControllerProvider(strategyId));
    final notifier =
        ref.read(strategyDetailControllerProvider(strategyId).notifier);

    return Scaffold(
      backgroundColor: AppColors.canvas,
      body: AppBackground(
        child: SafeArea(
          child: Column(
            children: [
              // ── Top nav ────────────────────────────────────────────────────
              Padding(
                padding: const EdgeInsets.fromLTRB(16, 16, 16, 8),
                child: Row(
                  children: [
                    GestureDetector(
                      onTap: () => context.canPop()
                          ? context.pop()
                          : context.go('/strategies'),
                      child: Row(
                        children: [
                          Icon(symbol('arrow_back'),
                              size: 16, color: AppColors.textDim),
                          const SizedBox(width: 4),
                          Text('All Strategies',
                              style: AppTextStyles.meta
                                  .copyWith(color: AppColors.textDim)),
                        ],
                      ),
                    ),
                    const Spacer(),
                    if (ctrl.strategy != null)
                      AppButton.semantic(
                        label: 'Backtest this strategy',
                        color: AppColors.info,
                        icon: symbol('play_circle'),
                        dense: true,
                        onPressed: () => _showBacktestModal(context, ref),
                      ),
                  ],
                ),
              ),
              // ── Body ───────────────────────────────────────────────────────
              Expanded(
                child: ctrl.loading
                    ? const _LoadingSkeletons()
                    : ctrl.strategy == null
                        ? _NotFound(onBack: () => context.go('/strategies'))
                        : _DetailBody(
                            ctrl: ctrl,
                            notifier: notifier,
                          ),
              ),
            ],
          ),
        ),
      ),
    );
  }

  void _showBacktestModal(BuildContext context, WidgetRef ref) {
    showModalBottomSheet(
      context: context,
      isScrollControlled: true,
      backgroundColor: Colors.transparent,
      builder: (_) => _BacktestModal(
        strategyId: strategyId,
        strategyName: ref
                .read(strategyDetailControllerProvider(strategyId))
                .strategy
                ?.name ??
            '',
        linkedStrategyIdInt: ref
            .read(strategyDetailControllerProvider(strategyId))
            .strategy
            ?.id,
      ),
    );
  }
}

// ── Detail body ───────────────────────────────────────────────────────────────

class _DetailBody extends ConsumerWidget {
  const _DetailBody({required this.ctrl, required this.notifier});

  final StrategyDetailState ctrl;
  final StrategyDetailController notifier;

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final strategy = ctrl.strategy!;
    final best = ctrl.bestPnlBacktest;
    final isAgentBest = notifier.isAgentBest;

    return RefreshIndicator(
      color: AppColors.primary,
      onRefresh: notifier.refresh,
      child: SingleChildScrollView(
        physics: const AlwaysScrollableScrollPhysics(),
        padding: const EdgeInsets.fromLTRB(16, 0, 16, 32),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            // ── Header card ─────────────────────────────────────────────────
            GlassCard(
              padding: EdgeInsets.zero,
              borderColor: isAgentBest
                  ? AppColors.warning.withValues(alpha: 0.3)
                  : null,
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  if (isAgentBest)
                    Container(
                      height: 2,
                      decoration: const BoxDecoration(
                        gradient: LinearGradient(
                          colors: [
                            Color(0x99F59E0B),
                            Color(0xFFFBBF24),
                            Color(0x99F59E0B),
                          ],
                        ),
                      ),
                    ),
                  Padding(
                    padding: const EdgeInsets.all(18),
                    child: Column(
                      crossAxisAlignment: CrossAxisAlignment.start,
                      children: [
                        Row(
                          crossAxisAlignment: CrossAxisAlignment.start,
                          children: [
                            // Icon
                            Container(
                              width: 48,
                              height: 48,
                              decoration: BoxDecoration(
                                color: isAgentBest
                                    ? AppColors.warning.withValues(alpha: 0.15)
                                    : AppColors.surface,
                                borderRadius: BorderRadius.circular(14),
                                border: Border.all(
                                    color: isAgentBest
                                        ? AppColors.warning
                                            .withValues(alpha: 0.3)
                                        : AppColors.border),
                              ),
                              child: Icon(
                                isAgentBest
                                    ? symbol('auto_awesome')
                                    : symbol('schema'),
                                size: 22,
                                color: isAgentBest
                                    ? AppColors.warning
                                    : AppColors.textMuted,
                              ),
                            ),
                            const SizedBox(width: 12),
                            Expanded(
                              child: Column(
                                crossAxisAlignment: CrossAxisAlignment.start,
                                children: [
                                  Row(
                                    children: [
                                      Expanded(
                                        child: Text(
                                          strategy.name,
                                          style: AppTextStyles.h2.copyWith(
                                            color: isAgentBest
                                                ? const Color(0xFFFCD34D)
                                                : AppColors.textHi,
                                          ),
                                        ),
                                      ),
                                      if (isAgentBest) ...[
                                        const SizedBox(width: 8),
                                        Container(
                                          padding: const EdgeInsets.symmetric(
                                              horizontal: 7, vertical: 3),
                                          decoration: BoxDecoration(
                                            color: AppColors.warning
                                                .withValues(alpha: 0.2),
                                            borderRadius:
                                                BorderRadius.circular(5),
                                            border: Border.all(
                                                color: AppColors.warning
                                                    .withValues(alpha: 0.3)),
                                          ),
                                          child: Text(
                                            'AGENT BEST',
                                            style:
                                                AppTextStyles.nano.copyWith(
                                              color: AppColors.warning,
                                              fontWeight: FontWeight.w700,
                                            ),
                                          ),
                                        ),
                                      ],
                                    ],
                                  ),
                                  const SizedBox(height: 4),
                                  Text(
                                    'Strategy ID ${strategy.id} · ${strategy.strategies.length} sub-strategies · ${ctrl.strategyBacktests.length} backtests',
                                    style: AppTextStyles.meta
                                        .copyWith(color: AppColors.textDim),
                                  ),
                                ],
                              ),
                            ),
                          ],
                        ),
                        if (best != null) ...[
                          const SizedBox(height: 14),
                          Row(
                            children: [
                              _HeaderStat(
                                label: 'BEST P&L',
                                value: fmtPnl(best.overallProfit),
                                color: pnlColor(best.overallProfit),
                              ),
                              const SizedBox(width: 20),
                              _HeaderStat(
                                label: 'BEST P&L%',
                                value: fmtPct(best.pnlPercent),
                                color: pnlColor(best.pnlPercent),
                              ),
                              const Spacer(),
                              AppButton.semantic(
                                label: 'Best Backtest',
                                color: AppColors.primary,
                                icon: symbol('analytics'),
                                dense: true,
                                onPressed: () => context
                                    .push('/backtests/${best.backtestId}'),
                              ),
                            ],
                          ),
                        ],
                      ],
                    ),
                  ),
                ],
              ),
            ),
            const SizedBox(height: 20),

            // ── Sub-strategies ───────────────────────────────────────────────
            SectionHeader(
              title: 'Sub-strategies (${strategy.strategies.length})',
              eyebrow: 'COMPOSITION',
            ),
            const SizedBox(height: 12),
            if (strategy.strategies.isEmpty)
              GlassCard(
                child: Center(
                  child: Text('No sub-strategies defined.',
                      style:
                          AppTextStyles.body.copyWith(color: AppColors.textDim)),
                ),
              )
            else
              ...strategy.strategies
                  .map((sub) => Padding(
                        padding: const EdgeInsets.only(bottom: 10),
                        child: _SubStrategyCard(sub: sub),
                      ))
                  ,

            const SizedBox(height: 20),

            // ── Backtests ────────────────────────────────────────────────────
            Row(
              children: [
                Expanded(
                  child: SectionHeader(
                    title:
                        'Backtests (${ctrl.strategyBacktests.length})',
                    eyebrow: 'HISTORY',
                  ),
                ),
              ],
            ),
            const SizedBox(height: 8),
            // Sort controls
            if (ctrl.strategyBacktests.isNotEmpty)
              _BtSortBar(
                  sortField: ctrl.btSortField,
                  sortAsc: ctrl.btSortAsc,
                  onSort: (f) => ref
                      .read(strategyDetailControllerProvider(
                              notifier.strategyId)
                          .notifier)
                      .setBtSort(f)),
            const SizedBox(height: 10),
            if (ctrl.strategyBacktests.isEmpty)
              EmptyState(
                icon: symbol('analytics'),
                title: 'No backtests yet.',
                subtitle: 'Run a backtest to see results here.',
              )
            else
              ...ctrl.sortedBacktests
                  .map((bt) => Padding(
                        padding: const EdgeInsets.only(bottom: 8),
                        child: _BacktestRow(
                          bt: bt,
                          isBest: bt.backtestId ==
                              ctrl.bestPnlBacktest?.backtestId,
                          onTap: () =>
                              context.push('/backtests/${bt.backtestId}'),
                        ),
                      ))
                  ,
          ],
        ),
      ),
    );
  }
}

class _HeaderStat extends StatelessWidget {
  const _HeaderStat(
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
            style: AppTextStyles.valueLg.copyWith(color: color)),
      ],
    );
  }
}

// ── Sub-strategy card ─────────────────────────────────────────────────────────

class _SubStrategyCard extends StatelessWidget {
  const _SubStrategyCard({required this.sub});

  final SubStrategy sub;

  String _fmtVal(dynamic v) {
    if (v is bool) return v ? 'true' : 'false';
    if (v is Map || v is List) {
      // Simple JSON-like display.
      return v.toString();
    }
    return v.toString();
  }

  @override
  Widget build(BuildContext context) {
    final phaseColor = _phaseColor(sub.decisionPhase);

    return GlassCard(
      padding: const EdgeInsets.all(14),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          // Header
          Row(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Expanded(
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Text(
                      sub.strategy,
                      style: AppTextStyles.cardTitle
                          .copyWith(fontFamily: 'JetBrains Mono'),
                    ),
                    const SizedBox(height: 2),
                    Text(
                      'position ${sub.executionPosition}',
                      style: AppTextStyles.nano
                          .copyWith(color: AppColors.textFaint),
                    ),
                  ],
                ),
              ),
              Container(
                padding: const EdgeInsets.symmetric(
                    horizontal: 8, vertical: 3),
                decoration: BoxDecoration(
                  color: phaseColor.withValues(alpha: 0.1),
                  borderRadius: BorderRadius.circular(6),
                  border: Border.all(
                      color: phaseColor.withValues(alpha: 0.2)),
                ),
                child: Text(
                  sub.decisionPhase.toUpperCase(),
                  style: AppTextStyles.nano.copyWith(
                    color: phaseColor,
                    fontWeight: FontWeight.w700,
                  ),
                ),
              ),
            ],
          ),
          const SizedBox(height: 10),
          // Core fields
          Row(
            children: [
              _MetaChip(
                  label: 'Weight',
                  value: sub.weight != null ? '${sub.weight}' : '—'),
              const SizedBox(width: 12),
              _MetaChip(
                  label: 'Scope', value: sub.executionScope ?? '—'),
            ],
          ),
          // Config entries
          if (sub.config.isNotEmpty) ...[
            const SizedBox(height: 10),
            const Divider(height: 1, color: AppColors.border),
            const SizedBox(height: 8),
            Text(
              'CONFIG',
              style: AppTextStyles.nano
                  .copyWith(color: AppColors.textFaint, letterSpacing: 1),
            ),
            const SizedBox(height: 6),
            ...sub.config.entries
                .map((entry) => Padding(
                      padding: const EdgeInsets.only(bottom: 6),
                      child: Row(
                        crossAxisAlignment: CrossAxisAlignment.start,
                        children: [
                          Expanded(
                            flex: 5,
                            child: Text(
                              getStrategyConfigFieldMeta(
                                      sub.strategy, entry.key)
                                  .label,
                              style: AppTextStyles.nano.copyWith(
                                  color: AppColors.textMuted),
                            ),
                          ),
                          const SizedBox(width: 8),
                          Expanded(
                            flex: 3,
                            child: Text(
                              _fmtVal(entry.value),
                              style: AppTextStyles.mono(11,
                                  color: AppColors.textHi,
                                  weight: FontWeight.w500),
                              textAlign: TextAlign.right,
                              overflow: TextOverflow.ellipsis,
                            ),
                          ),
                        ],
                      ),
                    ))
                ,
          ] else
            Padding(
              padding: const EdgeInsets.only(top: 8),
              child: Text('No config.',
                  style: AppTextStyles.nano
                      .copyWith(color: AppColors.textFaint)),
            ),
        ],
      ),
    );
  }
}

class _MetaChip extends StatelessWidget {
  const _MetaChip({required this.label, required this.value});

  final String label;
  final String value;

  @override
  Widget build(BuildContext context) {
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Text(label,
            style: AppTextStyles.nano.copyWith(color: AppColors.textFaint)),
        const SizedBox(height: 2),
        Text(value,
            style: AppTextStyles.mono(12, color: AppColors.textMd)),
      ],
    );
  }
}

// ── Backtest row card ─────────────────────────────────────────────────────────

class _BacktestRow extends StatelessWidget {
  const _BacktestRow({
    required this.bt,
    required this.isBest,
    required this.onTap,
  });

  final AgentResult bt;
  final bool isBest;
  final VoidCallback onTap;

  @override
  Widget build(BuildContext context) {
    return GlassCard(
      padding: const EdgeInsets.symmetric(horizontal: 14, vertical: 10),
      borderColor: isBest
          ? AppColors.success.withValues(alpha: 0.3)
          : null,
      onTap: onTap,
      child: Row(
        children: [
          if (isBest) ...[
            Icon(symbol('auto_awesome'),
                size: 14, color: AppColors.warning),
            const SizedBox(width: 6),
          ],
          Expanded(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Text(fmtDateTime(bt.createdAt),
                    style: AppTextStyles.meta
                        .copyWith(color: AppColors.textMd)),
                const SizedBox(height: 2),
                Row(
                  children: [
                    if (bt.stocksUsed.isNotEmpty)
                      Text(
                        bt.stocksUsed.take(4).join(', ') +
                            (bt.stocksUsed.length > 4
                                ? ' +${bt.stocksUsed.length - 4}'
                                : ''),
                        style: AppTextStyles.mono(11,
                            color: AppColors.textMuted),
                      ),
                    if (bt.startDate != null) ...[
                      const SizedBox(width: 8),
                      Text(
                        '${fmtDate(bt.startDate)} – ${fmtDate(bt.endDate)}',
                        style: AppTextStyles.nano
                            .copyWith(color: AppColors.textFaint),
                      ),
                    ],
                  ],
                ),
              ],
            ),
          ),
          const SizedBox(width: 8),
          Column(
            crossAxisAlignment: CrossAxisAlignment.end,
            children: [
              Text(fmtPnl(bt.overallProfit),
                  style: AppTextStyles.mono(13,
                      color: pnlColor(bt.overallProfit),
                      weight: FontWeight.w600)),
              Text(fmtPct(bt.pnlPercent),
                  style: AppTextStyles.mono(12,
                      color: pnlColor(bt.pnlPercent),
                      weight: FontWeight.w600)),
            ],
          ),
          const SizedBox(width: 8),
          Icon(symbol('open_in_new'), size: 14, color: AppColors.textFaint),
        ],
      ),
    );
  }
}

// ── BT sort bar ───────────────────────────────────────────────────────────────

class _BtSortBar extends StatelessWidget {
  const _BtSortBar({
    required this.sortField,
    required this.sortAsc,
    required this.onSort,
  });

  final String sortField;
  final bool sortAsc;
  final void Function(String) onSort;

  @override
  Widget build(BuildContext context) {
    return Row(
      children: [
        Text('Sort:',
            style: AppTextStyles.nano.copyWith(color: AppColors.textFaint)),
        const SizedBox(width: 8),
        ...[
          ('created_at', 'Date'),
          ('pnl', 'P&L'),
          ('pct', 'P&L%'),
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
    );
  }
}

// ── Loading skeletons ─────────────────────────────────────────────────────────

class _LoadingSkeletons extends StatelessWidget {
  const _LoadingSkeletons();

  @override
  Widget build(BuildContext context) {
    return ListView(
      padding: const EdgeInsets.fromLTRB(16, 0, 16, 32),
      children: [
        // Header card skeleton
        GlassCard(
          padding: EdgeInsets.zero,
          child: Padding(
            padding: const EdgeInsets.all(18),
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Row(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Skeleton(width: 48, height: 48, radius: 14),
                    const SizedBox(width: 12),
                    Expanded(
                      child: Column(
                        crossAxisAlignment: CrossAxisAlignment.start,
                        children: [
                          Skeleton.line(width: 180, height: 20),
                          const SizedBox(height: 6),
                          Skeleton.line(width: 220, height: 11),
                        ],
                      ),
                    ),
                  ],
                ),
                const SizedBox(height: 14),
                Row(
                  children: [
                    Column(
                      crossAxisAlignment: CrossAxisAlignment.start,
                      children: [
                        Skeleton.line(width: 55, height: 9),
                        const SizedBox(height: 4),
                        Skeleton(width: 90, height: 20, radius: 4),
                      ],
                    ),
                    const SizedBox(width: 20),
                    Column(
                      crossAxisAlignment: CrossAxisAlignment.start,
                      children: [
                        Skeleton.line(width: 65, height: 9),
                        const SizedBox(height: 4),
                        Skeleton(width: 80, height: 20, radius: 4),
                      ],
                    ),
                    const Spacer(),
                    Skeleton(width: 100, height: 30, radius: 8),
                  ],
                ),
              ],
            ),
          ),
        ),
        const SizedBox(height: 20),

        // Sub-strategies header
        Skeleton.line(width: 160, height: 12),
        const SizedBox(height: 12),

        // Sub-strategy card skeletons (3)
        ...List.generate(
          3,
          (_) => Padding(
            padding: const EdgeInsets.only(bottom: 10),
            child: GlassCard(
              padding: const EdgeInsets.all(14),
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Row(
                    children: [
                      Expanded(
                        child: Column(
                          crossAxisAlignment: CrossAxisAlignment.start,
                          children: [
                            Skeleton.line(width: 140, height: 13),
                            const SizedBox(height: 4),
                            Skeleton.line(width: 80, height: 10),
                          ],
                        ),
                      ),
                      Skeleton(width: 50, height: 22, radius: 6),
                    ],
                  ),
                  const SizedBox(height: 10),
                  Row(
                    children: [
                      Skeleton(width: 60, height: 30, radius: 6),
                      const SizedBox(width: 12),
                      Skeleton(width: 80, height: 30, radius: 6),
                    ],
                  ),
                  const SizedBox(height: 10),
                  Skeleton.line(height: 10),
                  const SizedBox(height: 5),
                  Skeleton.line(height: 10),
                  const SizedBox(height: 5),
                  Skeleton.line(width: 200, height: 10),
                ],
              ),
            ),
          ),
        ),
        const SizedBox(height: 20),

        // Backtests header
        Skeleton.line(width: 130, height: 12),
        const SizedBox(height: 12),

        // Backtest row skeletons (4)
        ...List.generate(
          4,
          (_) => Padding(
            padding: const EdgeInsets.only(bottom: 8),
            child: GlassCard(
              padding: const EdgeInsets.symmetric(
                  horizontal: 14, vertical: 10),
              child: Row(
                children: [
                  Expanded(
                    child: Column(
                      crossAxisAlignment: CrossAxisAlignment.start,
                      children: [
                        Skeleton.line(width: 110, height: 12),
                        const SizedBox(height: 5),
                        Skeleton.line(width: 160, height: 10),
                      ],
                    ),
                  ),
                  const SizedBox(width: 8),
                  Column(
                    crossAxisAlignment: CrossAxisAlignment.end,
                    children: [
                      Skeleton(width: 65, height: 13, radius: 4),
                      const SizedBox(height: 4),
                      Skeleton(width: 50, height: 12, radius: 4),
                    ],
                  ),
                  const SizedBox(width: 8),
                  Skeleton(width: 14, height: 14, radius: 4),
                ],
              ),
            ),
          ),
        ),
      ],
    );
  }
}

// ── Not found ─────────────────────────────────────────────────────────────────

class _NotFound extends StatelessWidget {
  const _NotFound({required this.onBack});

  final VoidCallback onBack;

  @override
  Widget build(BuildContext context) {
    return Center(
      child: Column(
        mainAxisSize: MainAxisSize.min,
        children: [
          Icon(symbol('schema'), size: 48, color: AppColors.textFaint),
          const SizedBox(height: 12),
          Text('Strategy not found',
              style:
                  AppTextStyles.cardTitle.copyWith(color: AppColors.textMd)),
          const SizedBox(height: 16),
          AppButton.ghost(label: 'Back to Strategies', onPressed: onBack),
        ],
      ),
    );
  }
}

// ── Backtest modal ────────────────────────────────────────────────────────────

class _BacktestModal extends ConsumerStatefulWidget {
  const _BacktestModal({
    required this.strategyId,
    required this.strategyName,
    required this.linkedStrategyIdInt,
  });

  final String strategyId;
  final String strategyName;
  final int? linkedStrategyIdInt;

  @override
  ConsumerState<_BacktestModal> createState() => _BacktestModalState();
}

class _BacktestModalState extends ConsumerState<_BacktestModal> {
  // Form state
  final _stocksCtrl = TextEditingController();
  final _startCtrl = TextEditingController();
  final _endCtrl = TextEditingController();
  final _cashCtrl = TextEditingController(text: '10000');
  String _granularity = '86400';
  String _selectedInstId = '';
  final _newInstCtrl = TextEditingController();

  // Modal state
  bool _loadingInsts = true;
  bool _busy = false;
  String _msg = '';
  bool _msgOk = false;

  List<Map<String, dynamic>> _allInstances = [];

  static const _granularities = [
    ('86400', '1 day'),
    ('3600', '1 hour'),
    ('900', '15 min'),
    ('300', '5 min'),
    ('60', '1 min'),
  ];

  @override
  void initState() {
    super.initState();
    _loadInstances();
  }

  @override
  void dispose() {
    _stocksCtrl.dispose();
    _startCtrl.dispose();
    _endCtrl.dispose();
    _cashCtrl.dispose();
    _newInstCtrl.dispose();
    super.dispose();
  }

  Future<void> _loadInstances() async {
    setState(() => _loadingInsts = true);
    try {
      final repo = ref.read(strategy_repo.strategyRepositoryProvider);
      _allInstances = await repo.instances();
      // Default selection: first linked, then first free, else new
      final linked = _linkedInstances;
      final free = _freeInstances;
      if (linked.isNotEmpty) {
        _selectedInstId = linked[0]['id'].toString();
      } else if (free.isNotEmpty) {
        _selectedInstId = free[0]['id'].toString();
      } else {
        _selectedInstId = '';
      }
    } catch (_) {
      // non-critical
    } finally {
      if (mounted) setState(() => _loadingInsts = false);
    }
  }

  List<Map<String, dynamic>> get _linkedInstances => _allInstances
      .where((i) =>
          i['strategy_id']?.toString() ==
          widget.linkedStrategyIdInt?.toString())
      .toList();

  List<Map<String, dynamic>> get _freeInstances =>
      _allInstances.where((i) => i['strategy_id'] == null).toList();

  Future<void> _submit() async {
    final stocks = _stocksCtrl.text
        .split(',')
        .map((s) => s.trim().toUpperCase())
        .where((s) => s.isNotEmpty)
        .toList();
    if (stocks.isEmpty) {
      setState(() {
        _msg = 'At least one stock is required';
        _msgOk = false;
      });
      return;
    }
    final start = _startCtrl.text.trim();
    final end = _endCtrl.text.trim();
    if (start.isEmpty) {
      setState(() {
        _msg = 'Start date is required';
        _msgOk = false;
      });
      return;
    }
    if (end.isEmpty) {
      setState(() {
        _msg = 'End date is required';
        _msgOk = false;
      });
      return;
    }
    if (start.compareTo(end) >= 0) {
      setState(() {
        _msg = 'End date must be after start date';
        _msgOk = false;
      });
      return;
    }
    if (_selectedInstId.isEmpty && _newInstCtrl.text.trim().isEmpty) {
      setState(() {
        _msg = 'Instance name is required when creating a new one';
        _msgOk = false;
      });
      return;
    }

    setState(() {
      _busy = true;
      _msg = 'Working...';
      _msgOk = false;
    });

    try {
      final repo = ref.read(strategy_repo.strategyRepositoryProvider);

      String instanceId = _selectedInstId;

      // Create new instance if needed.
      if (instanceId.isEmpty) {
        final newId =
            (100000 + DateTime.now().millisecondsSinceEpoch % 900000)
                .toString();
        final created = await repo.createInstance({
          'id': newId,
          'name': _newInstCtrl.text.trim(),
          'run_command': false,
        });
        instanceId = (created['id'] ?? created['instance_id'] ?? '').toString();
        if (instanceId.isEmpty) throw Exception('Instance created but no ID returned');
      }

      // Link strategy if not already linked.
      final isLinked = _linkedInstances
          .any((i) => i['id'].toString() == instanceId);
      if (!isLinked && widget.linkedStrategyIdInt != null) {
        await repo.linkStrategy(instanceId, widget.linkedStrategyIdInt!);
      }

      // Create backtest.
      final result = await repo.createBacktest({
        'instance_id': instanceId,
        'stocks': stocks,
        'start_date': start,
        'end_date': end,
        'granularity': _granularity,
        'initial_cash': double.tryParse(_cashCtrl.text) ?? 10000,
      });

      final btId = (result['id'] ?? result['backtest_id'])?.toString() ?? '';
      setState(() {
        _msgOk = true;
        _msg = 'Backtest #$btId queued!';
      });

      await Future.delayed(const Duration(milliseconds: 900));
      if (mounted) {
        Navigator.of(context).pop();
        context.push('/backtests/$btId');
      }
    } catch (e) {
      setState(() {
        _msg = e.toString();
        _msgOk = false;
      });
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    return DraggableScrollableSheet(
      initialChildSize: 0.9,
      maxChildSize: 0.95,
      minChildSize: 0.5,
      expand: false,
      builder: (ctx, scrollCtrl) {
        return Container(
          decoration: const BoxDecoration(
            color: Color(0xFF0D1117),
            borderRadius: BorderRadius.vertical(top: Radius.circular(20)),
          ),
          child: Column(
            children: [
              // Drag handle
              Container(
                margin: const EdgeInsets.only(top: 10, bottom: 8),
                width: 36,
                height: 4,
                decoration: BoxDecoration(
                  color: AppColors.border,
                  borderRadius: BorderRadius.circular(2),
                ),
              ),
              // Header
              Padding(
                padding: const EdgeInsets.fromLTRB(20, 4, 16, 12),
                child: Row(
                  children: [
                    Container(
                      width: 36,
                      height: 36,
                      decoration: BoxDecoration(
                        color: AppColors.info.withValues(alpha: 0.1),
                        borderRadius: BorderRadius.circular(10),
                        border: Border.all(
                            color: AppColors.info.withValues(alpha: 0.2)),
                      ),
                      child: Icon(symbol('play_circle'),
                          size: 18, color: AppColors.info),
                    ),
                    const SizedBox(width: 12),
                    Expanded(
                      child: Column(
                        crossAxisAlignment: CrossAxisAlignment.start,
                        children: [
                          Text('Backtest this strategy',
                              style: AppTextStyles.cardTitle),
                          Text(widget.strategyName,
                              style: AppTextStyles.meta.copyWith(
                                  color: AppColors.textDim),
                              overflow: TextOverflow.ellipsis),
                        ],
                      ),
                    ),
                    IconButton(
                      onPressed: _busy ? null : () => Navigator.pop(context),
                      icon: Icon(symbol('close'),
                          size: 20, color: AppColors.textMuted),
                    ),
                  ],
                ),
              ),
              const Divider(height: 1, color: AppColors.border),
              // Body
              Expanded(
                child: _loadingInsts
                    ? const Center(child: LoadingState(label: 'Loading instances...'))
                    : ListView(
                        controller: scrollCtrl,
                        padding: const EdgeInsets.fromLTRB(20, 16, 20, 20),
                        children: [
                          // Instance selector
                          Text(
                            'SELECT INSTANCE',
                            style: AppTextStyles.nano.copyWith(
                                color: AppColors.textDim, letterSpacing: 1),
                          ),
                          const SizedBox(height: 10),
                          if (_linkedInstances.isNotEmpty) ...[
                            Text('Already linked to this strategy',
                                style: AppTextStyles.nano.copyWith(
                                    color: AppColors.success
                                        .withValues(alpha: 0.7),
                                    fontWeight: FontWeight.w600)),
                            const SizedBox(height: 8),
                            ..._linkedInstances
                                .map((inst) => _InstanceTile(
                                      id: inst['id'].toString(),
                                      name: (inst['name'] ??
                                              inst['id'])
                                          .toString(),
                                      selected: _selectedInstId ==
                                          inst['id'].toString(),
                                      linked: true,
                                      onTap: () => setState(() =>
                                          _selectedInstId =
                                              inst['id'].toString()),
                                    ))
                                ,
                            const SizedBox(height: 12),
                          ],
                          if (_freeInstances.isNotEmpty) ...[
                            Text('Available instances (no strategy)',
                                style: AppTextStyles.nano.copyWith(
                                    color: AppColors.textFaint,
                                    fontWeight: FontWeight.w600)),
                            const SizedBox(height: 8),
                            ..._freeInstances
                                .map((inst) => _InstanceTile(
                                      id: inst['id'].toString(),
                                      name: (inst['name'] ??
                                              inst['id'])
                                          .toString(),
                                      selected: _selectedInstId ==
                                          inst['id'].toString(),
                                      linked: false,
                                      onTap: () => setState(() =>
                                          _selectedInstId =
                                              inst['id'].toString()),
                                    ))
                                ,
                            const SizedBox(height: 8),
                          ],
                          // Create new
                          _InstanceTile(
                            id: '',
                            name: 'Create new instance',
                            selected: _selectedInstId == '',
                            linked: false,
                            isCreateNew: true,
                            onTap: () => setState(() => _selectedInstId = ''),
                          ),
                          if (_selectedInstId.isEmpty) ...[
                            const SizedBox(height: 10),
                            _FormField(
                              label: 'NEW INSTANCE NAME',
                              controller: _newInstCtrl,
                              placeholder: 'e.g. My Strategy Test',
                            ),
                          ],
                          const SizedBox(height: 20),
                          const Divider(height: 1, color: AppColors.border),
                          const SizedBox(height: 20),
                          // Backtest params
                          Text(
                            'BACKTEST PARAMETERS',
                            style: AppTextStyles.nano.copyWith(
                                color: AppColors.textDim, letterSpacing: 1),
                          ),
                          const SizedBox(height: 12),
                          _FormField(
                            label: 'STOCKS (comma-separated)',
                            controller: _stocksCtrl,
                            placeholder: 'AAPL, MSFT, NVDA',
                            mono: true,
                          ),
                          const SizedBox(height: 12),
                          Row(
                            children: [
                              Expanded(
                                child: _FormField(
                                  label: 'START DATE',
                                  controller: _startCtrl,
                                  placeholder: 'YYYY-MM-DD',
                                  onTap: () async {
                                    final d = await showDatePicker(
                                      context: context,
                                      initialDate: DateTime.now()
                                          .subtract(const Duration(days: 365)),
                                      firstDate: DateTime(2015),
                                      lastDate: DateTime.now(),
                                      builder: (c, child) => Theme(
                                        data: ThemeData.dark().copyWith(
                                          colorScheme: const ColorScheme.dark(
                                              primary: AppColors.primary),
                                        ),
                                        child: child!,
                                      ),
                                    );
                                    if (d != null) {
                                      _startCtrl.text = d
                                          .toIso8601String()
                                          .substring(0, 10);
                                    }
                                  },
                                ),
                              ),
                              const SizedBox(width: 10),
                              Expanded(
                                child: _FormField(
                                  label: 'END DATE',
                                  controller: _endCtrl,
                                  placeholder: 'YYYY-MM-DD',
                                  onTap: () async {
                                    final d = await showDatePicker(
                                      context: context,
                                      initialDate: DateTime.now(),
                                      firstDate: DateTime(2015),
                                      lastDate: DateTime.now(),
                                      builder: (c, child) => Theme(
                                        data: ThemeData.dark().copyWith(
                                          colorScheme: const ColorScheme.dark(
                                              primary: AppColors.primary),
                                        ),
                                        child: child!,
                                      ),
                                    );
                                    if (d != null) {
                                      _endCtrl.text = d
                                          .toIso8601String()
                                          .substring(0, 10);
                                    }
                                  },
                                ),
                              ),
                            ],
                          ),
                          const SizedBox(height: 12),
                          Row(
                            children: [
                              Expanded(
                                child: Column(
                                  crossAxisAlignment: CrossAxisAlignment.start,
                                  children: [
                                    Text('GRANULARITY',
                                        style: AppTextStyles.nano.copyWith(
                                            color: AppColors.textDim,
                                            letterSpacing: 0.8)),
                                    const SizedBox(height: 6),
                                    Container(
                                      padding: const EdgeInsets.symmetric(
                                          horizontal: 12, vertical: 4),
                                      decoration: BoxDecoration(
                                        color: AppColors.surface,
                                        borderRadius: BorderRadius.circular(10),
                                        border: Border.all(
                                            color: AppColors.border),
                                      ),
                                      child: DropdownButtonHideUnderline(
                                        child: DropdownButton<String>(
                                          value: _granularity,
                                          isExpanded: true,
                                          dropdownColor: AppColors.panel,
                                          style: AppTextStyles.body.copyWith(
                                              color: AppColors.textMd),
                                          items: _granularities
                                              .map((g) => DropdownMenuItem(
                                                    value: g.$1,
                                                    child: Text(g.$2),
                                                  ))
                                              .toList(),
                                          onChanged: (v) {
                                            if (v != null) {
                                              setState(
                                                  () => _granularity = v);
                                            }
                                          },
                                        ),
                                      ),
                                    ),
                                  ],
                                ),
                              ),
                              const SizedBox(width: 10),
                              Expanded(
                                child: _FormField(
                                  label: 'INITIAL CASH (\$)',
                                  controller: _cashCtrl,
                                  placeholder: '10000',
                                  keyboardType: TextInputType.number,
                                  mono: true,
                                ),
                              ),
                            ],
                          ),
                        ],
                      ),
              ),
              // Footer
              Container(
                padding: const EdgeInsets.fromLTRB(20, 12, 20, 20),
                decoration: const BoxDecoration(
                  border: Border(
                      top: BorderSide(color: AppColors.border)),
                ),
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.stretch,
                  children: [
                    if (_msg.isNotEmpty)
                      Padding(
                        padding: const EdgeInsets.only(bottom: 12),
                        child: Container(
                          padding: const EdgeInsets.all(10),
                          decoration: BoxDecoration(
                            color: (_msgOk ? AppColors.success : AppColors.danger)
                                .withValues(alpha: 0.1),
                            borderRadius: BorderRadius.circular(8),
                            border: Border.all(
                                color: (_msgOk
                                        ? AppColors.success
                                        : AppColors.danger)
                                    .withValues(alpha: 0.2)),
                          ),
                          child: Row(
                            children: [
                              Icon(
                                _msgOk
                                    ? symbol('check_circle')
                                    : symbol('error'),
                                size: 14,
                                color: _msgOk
                                    ? AppColors.success
                                    : AppColors.danger,
                              ),
                              const SizedBox(width: 8),
                              Expanded(
                                child: Text(
                                  _msg,
                                  style: AppTextStyles.meta.copyWith(
                                    color: _msgOk
                                        ? AppColors.success
                                        : AppColors.danger,
                                  ),
                                ),
                              ),
                            ],
                          ),
                        ),
                      ),
                    Row(
                      children: [
                        Expanded(
                          child: AppButton.ghost(
                            label: 'Cancel',
                            onPressed: _busy ? null : () => Navigator.pop(context),
                          ),
                        ),
                        const SizedBox(width: 10),
                        Expanded(
                          flex: 2,
                          child: AppButton.primary(
                            label: 'Run Backtest',
                            icon: symbol('play_circle'),
                            busy: _busy,
                            onPressed: _busy ? null : _submit,
                          ),
                        ),
                      ],
                    ),
                  ],
                ),
              ),
            ],
          ),
        );
      },
    );
  }
}

// ── Instance tile ─────────────────────────────────────────────────────────────

class _InstanceTile extends StatelessWidget {
  const _InstanceTile({
    required this.id,
    required this.name,
    required this.selected,
    required this.linked,
    this.isCreateNew = false,
    required this.onTap,
  });

  final String id;
  final String name;
  final bool selected;
  final bool linked;
  final bool isCreateNew;
  final VoidCallback onTap;

  @override
  Widget build(BuildContext context) {
    final accentColor = linked ? AppColors.success : AppColors.primary;
    return GestureDetector(
      onTap: onTap,
      child: Container(
        margin: const EdgeInsets.only(bottom: 8),
        padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 10),
        decoration: BoxDecoration(
          color: selected ? accentColor.withValues(alpha: 0.1) : Colors.transparent,
          borderRadius: BorderRadius.circular(12),
          border: Border.all(
              color: selected
                  ? accentColor.withValues(alpha: 0.4)
                  : linked
                      ? AppColors.success.withValues(alpha: 0.2)
                      : AppColors.border),
        ),
        child: Row(
          children: [
            if (isCreateNew)
              Icon(symbol('add_circle'), size: 18, color: AppColors.textMuted)
            else
              Icon(symbol('schema'), size: 16, color: AppColors.textFaint),
            const SizedBox(width: 10),
            Expanded(
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Text(name,
                      style: AppTextStyles.cardTitle.copyWith(
                          color: linked
                              ? AppColors.success.withValues(alpha: 0.9)
                              : AppColors.textHi)),
                  if (!isCreateNew)
                    Text(id,
                        style: AppTextStyles.mono(10,
                            color: AppColors.textFaint)),
                ],
              ),
            ),
            if (linked)
              Container(
                padding: const EdgeInsets.symmetric(horizontal: 6, vertical: 2),
                decoration: BoxDecoration(
                  color: AppColors.success.withValues(alpha: 0.15),
                  borderRadius: BorderRadius.circular(4),
                  border: Border.all(
                      color: AppColors.success.withValues(alpha: 0.2)),
                ),
                child: Text('LINKED',
                    style: AppTextStyles.nano.copyWith(
                        color: AppColors.success,
                        fontWeight: FontWeight.w700)),
              ),
            const SizedBox(width: 8),
            Container(
              width: 18,
              height: 18,
              decoration: BoxDecoration(
                shape: BoxShape.circle,
                border: Border.all(color: accentColor.withValues(alpha: 0.5)),
                color: selected
                    ? accentColor.withValues(alpha: 0.3)
                    : Colors.transparent,
              ),
              child: selected
                  ? Center(
                      child: Container(
                        width: 8,
                        height: 8,
                        decoration: BoxDecoration(
                            shape: BoxShape.circle, color: accentColor),
                      ),
                    )
                  : null,
            ),
          ],
        ),
      ),
    );
  }
}

// ── Form field ────────────────────────────────────────────────────────────────

class _FormField extends StatelessWidget {
  const _FormField({
    required this.label,
    required this.controller,
    this.placeholder = '',
    this.mono = false,
    this.keyboardType,
    this.onTap,
  });

  final String label;
  final TextEditingController controller;
  final String placeholder;
  final bool mono;
  final TextInputType? keyboardType;
  final VoidCallback? onTap;

  @override
  Widget build(BuildContext context) {
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Text(label,
            style: AppTextStyles.nano
                .copyWith(color: AppColors.textDim, letterSpacing: 0.8)),
        const SizedBox(height: 6),
        TextField(
          controller: controller,
          readOnly: onTap != null,
          onTap: onTap,
          keyboardType: keyboardType,
          style: mono
              ? AppTextStyles.mono(14, color: AppColors.textHi)
              : AppTextStyles.body.copyWith(color: AppColors.textHi),
          decoration: InputDecoration(
            hintText: placeholder,
            hintStyle: AppTextStyles.body.copyWith(color: AppColors.textFaint),
            filled: true,
            fillColor: AppColors.surface,
            contentPadding: const EdgeInsets.symmetric(
                horizontal: 12, vertical: 10),
            enabledBorder: OutlineInputBorder(
              borderRadius: BorderRadius.circular(10),
              borderSide: const BorderSide(color: AppColors.border),
            ),
            focusedBorder: OutlineInputBorder(
              borderRadius: BorderRadius.circular(10),
              borderSide: BorderSide(
                  color: AppColors.primary.withValues(alpha: 0.5)),
            ),
          ),
        ),
      ],
    );
  }
}

