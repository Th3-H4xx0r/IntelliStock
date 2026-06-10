import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';
import '../../../core/formatters/formatters.dart';
import '../../../core/theme/app_colors.dart';
import '../../../core/theme/app_text_styles.dart';
import '../../../core/widgets/app_background.dart';
import '../../../core/widgets/app_button.dart';
import '../../../core/widgets/common_widgets.dart';
import '../../../core/widgets/confirm_dialog.dart';
import '../../../core/widgets/glass_card.dart';
import '../../../core/widgets/material_symbols.dart';
import '../../../core/widgets/status_pill.dart';
import '../../../core/widgets/typed_confirm_field.dart';
import '../application/instances_controller.dart';
import '../data/models/instance.dart';
import 'live_logs_panel.dart';

// ── Granularity pill labels ──────────────────────────────────────────────────

String _granLabel(int? secs) {
  if (secs == null) return '—';
  if (secs < 60) return '${secs}s';
  if (secs < 3600) return '${secs ~/ 60}m';
  if (secs < 86400) return '${secs ~/ 3600}h';
  return '${secs ~/ 86400}d';
}

String _fmtUptime(int secs) {
  if (secs <= 0) return '—';
  final h = secs ~/ 3600;
  final m = (secs % 3600) ~/ 60;
  final s = secs % 60;
  if (h > 0) return '${h}h ${m}m ${s}s';
  if (m > 0) return '${m}m ${s}s';
  return '${s}s';
}

// ── Entry point ──────────────────────────────────────────────────────────────

/// Pushed route; requires [instanceId] path param wired via go_router.
/// The orchestrator should wrap this in a ProviderScope override for
/// [selectedInstanceIdProvider].
class InstanceDetailScreen extends ConsumerWidget {
  const InstanceDetailScreen({super.key, required this.instanceId});

  final String instanceId;

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    // Override the id provider for this screen's subtree.
    return ProviderScope(
      overrides: [
        selectedInstanceIdProvider.overrideWithValue(instanceId),
      ],
      child: const _InstanceDetailBody(),
    );
  }
}

class _InstanceDetailBody extends ConsumerWidget {
  const _InstanceDetailBody();

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final async = ref.watch(instanceDetailControllerProvider);

    return Scaffold(
      backgroundColor: AppColors.canvas,
      body: AppBackground(
        child: SafeArea(
          child: async.when(
            loading: () => const Center(child: LoadingState()),
            error: (e, _) => Center(
              child: Padding(
                padding: const EdgeInsets.all(24),
                child: ErrorBanner(
                  message: e.toString(),
                  onRetry: () =>
                      ref.invalidate(instanceDetailControllerProvider),
                ),
              ),
            ),
            data: (state) => state.instance == null
                ? const Center(child: ErrorBanner(message: 'Instance not found'))
                : _DetailContent(state: state),
          ),
        ),
      ),
    );
  }
}

// ── Main content ─────────────────────────────────────────────────────────────

class _DetailContent extends ConsumerWidget {
  const _DetailContent({required this.state});

  final InstanceDetailState state;

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final inst = state.instance!;
    final ctrl = ref.read(instanceDetailControllerProvider.notifier);

    return RefreshIndicator(
      onRefresh: () async {
        await ctrl.refreshInstance();
      },
      child: CustomScrollView(
        slivers: [
          SliverToBoxAdapter(
            child: Padding(
              padding: const EdgeInsets.fromLTRB(16, 12, 16, 0),
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  // Breadcrumb
                  _Breadcrumb(instanceName: inst.name.isNotEmpty ? inst.name : inst.id),
                  const SizedBox(height: 16),
                  // Page header
                  _PageHeader(inst: inst, state: state),
                  const SizedBox(height: 20),
                  // Info cards
                  _InstanceInfoCard(inst: inst, state: state),
                  const SizedBox(height: 12),
                  _BrokerageCard(inst: inst),
                  const SizedBox(height: 12),
                  _StrategyCard(inst: inst),
                  const SizedBox(height: 12),
                  // Stocks
                  _StocksCard(inst: inst),
                  const SizedBox(height: 12),
                  // Live logs
                  LiveLogsPanel(instanceId: inst.id),
                  const SizedBox(height: 12),
                  // Backtests
                  _BacktestsSection(state: state),
                  const SizedBox(height: 80),
                ],
              ),
            ),
          ),
        ],
      ),
    );
  }
}

// ── Breadcrumb ────────────────────────────────────────────────────────────────

class _Breadcrumb extends StatelessWidget {
  const _Breadcrumb({required this.instanceName});
  final String instanceName;

  @override
  Widget build(BuildContext context) {
    return Row(
      children: [
        GestureDetector(
          onTap: () => context.pop(),
          child: Row(
            mainAxisSize: MainAxisSize.min,
            children: [
              Icon(symbol('arrow_back'), size: 14, color: AppColors.textDim),
              const SizedBox(width: 4),
              Text('Instances',
                  style: AppTextStyles.meta
                      .copyWith(color: AppColors.textDim)),
            ],
          ),
        ),
        Text(' / ', style: AppTextStyles.meta.copyWith(color: AppColors.textFaint)),
        Expanded(
          child: Text(
            instanceName,
            style: AppTextStyles.meta,
            overflow: TextOverflow.ellipsis,
          ),
        ),
      ],
    );
  }
}

// ── Page header ───────────────────────────────────────────────────────────────

class _PageHeader extends ConsumerWidget {
  const _PageHeader({required this.inst, required this.state});
  final Instance inst;
  final InstanceDetailState state;

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final ctrl = ref.read(instanceDetailControllerProvider.notifier);
    final isRunning = inst.runCommand;
    final isAi = inst.createdBy == 'ai';

    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Row(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            IconTile(icon: symbol('memory'), size: 44),
            const SizedBox(width: 12),
            Expanded(
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Row(
                    children: [
                      Expanded(
                        child: Text(
                          inst.name.isNotEmpty ? inst.name : inst.id,
                          style: AppTextStyles.h2,
                          overflow: TextOverflow.ellipsis,
                        ),
                      ),
                      AppBadge(
                        label: isAi ? 'AI' : 'User',
                        color: isAi ? AppColors.primary : AppColors.textDim,
                      ),
                    ],
                  ),
                  const SizedBox(height: 4),
                  Text(
                    inst.id,
                    style: AppTextStyles.mono(11, color: AppColors.textDim),
                    overflow: TextOverflow.ellipsis,
                  ),
                ],
              ),
            ),
          ],
        ),
        const SizedBox(height: 12),
        // Status + action row
        Row(
          children: [
            StatusPill(
              label: isRunning ? 'Running' : 'Stopped',
              color: isRunning ? AppColors.success : AppColors.textDim,
              pulsing: isRunning,
            ),
            const SizedBox(width: 8),
            AppButton.semantic(
              label: isRunning ? 'Stop' : 'Start',
              color: isRunning ? AppColors.warning : AppColors.success,
              icon: isRunning ? symbol('stop') : symbol('play_arrow'),
              dense: true,
              onPressed: () => ctrl.toggleRun(),
            ),
            const SizedBox(width: 8),
            AppButton.ghost(
              label: 'Live Trading',
              icon: symbol('monitoring'),
              dense: true,
              onPressed: () => context.push('/instances/${inst.id}/live'),
            ),
            const Spacer(),
            IconButton(
              icon: Icon(symbol('refresh'), color: AppColors.textMuted, size: 20),
              tooltip: 'Refresh',
              onPressed: () => ctrl.refreshInstance(),
            ),
          ],
        ),
      ],
    );
  }
}

// ── Instance info card ────────────────────────────────────────────────────────

class _InstanceInfoCard extends ConsumerWidget {
  const _InstanceInfoCard({required this.inst, required this.state});
  final Instance inst;
  final InstanceDetailState state;

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    return GlassCard(
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Row(
            children: [
              Text('Instance Info', style: AppTextStyles.cardTitle),
              const Spacer(),
              // Clear state button
              _TinyBtn(
                icon: symbol('delete_sweep'),
                label: 'Clear State',
                color: AppColors.danger,
                onTap: () => _showClearState(context, ref),
              ),
            ],
          ),
          const SizedBox(height: 12),
          Wrap(
            spacing: 8,
            runSpacing: 8,
            children: [
              StatTile(
                label: 'Uptime',
                value: _fmtUptime(state.liveUptimeSecs),
                valueColor: inst.runCommand ? AppColors.success : AppColors.textDim,
              ),
              StatTile(
                label: 'Granularity',
                value: _granLabel(inst.granularityTimeIncrement),
              ),
              if (inst.maxUsage != null)
                StatTile(
                  label: 'Max Usage',
                  value: fmtMoney(inst.maxUsage),
                ),
              StatTile(
                label: 'Created By',
                value: inst.createdBy == 'ai' ? 'AI' : 'User',
              ),
            ],
          ),
        ],
      ),
    );
  }

  Future<void> _showClearState(BuildContext context, WidgetRef ref) async {
    await showModalBottomSheet<void>(
      context: context,
      isScrollControlled: true,
      backgroundColor: Colors.transparent,
      builder: (_) => ProviderScope(
        child: _ClearStateSheet(instanceId: inst.id),
      ),
    );
  }
}

// ── Brokerage card ────────────────────────────────────────────────────────────

class _BrokerageCard extends ConsumerWidget {
  const _BrokerageCard({required this.inst});
  final Instance inst;

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final ctrl = ref.read(instanceDetailControllerProvider.notifier);

    String brokerageName = '—';
    String brokerageType = '';
    if (inst.brokerage != null) {
      brokerageName =
          inst.brokerage!['account_name']?.toString() ?? '—';
      brokerageType =
          inst.brokerage!['brokerage_type']?.toString() ?? '';
    } else if (inst.brokerageId != null) {
      brokerageName = inst.brokerageId!;
    }

    String dataSourceName = '—';
    if (inst.alpacaDataBrokerageId != null) {
      dataSourceName = inst.alpacaDataBrokerageId!;
    }

    return GlassCard(
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Row(
            children: [
              Text('Brokerage', style: AppTextStyles.cardTitle),
              const Spacer(),
              if (inst.brokerageId != null)
                _TinyBtn(
                  icon: symbol('link'),
                  label: 'Change',
                  color: AppColors.teal,
                  onTap: () => _showLinkBrokerage(context, ref),
                )
              else
                _TinyBtn(
                  icon: symbol('link'),
                  label: 'Link',
                  color: AppColors.teal,
                  onTap: () => _showLinkBrokerage(context, ref),
                ),
            ],
          ),
          const SizedBox(height: 12),
          _InfoRow(label: 'Trading Account', value: '$brokerageName${brokerageType.isNotEmpty ? ' ($brokerageType)' : ''}'),
          const SizedBox(height: 4),
          _InfoRow(label: 'Market Data Source', value: dataSourceName),
          const SizedBox(height: 8),
          if (inst.brokerageId != null)
            _TinyBtn(
              icon: symbol('close'),
              label: 'Unlink Brokerage',
              color: AppColors.danger,
              onTap: () async {
                await showConfirmDialog(
                  context,
                  title: 'Unlink Brokerage',
                  body: 'Remove the brokerage from this instance?',
                  confirmLabel: 'Unlink',
                  confirmColor: AppColors.danger,
                  icon: symbol('close'),
                  onConfirm: () async => ctrl.unlinkBrokerage(),
                );
              },
            ),
        ],
      ),
    );
  }

  Future<void> _showLinkBrokerage(BuildContext context, WidgetRef ref) async {
    await showModalBottomSheet<void>(
      context: context,
      isScrollControlled: true,
      backgroundColor: Colors.transparent,
      builder: (_) => ProviderScope(
        child: _LinkBrokerageSheet(currentId: inst.brokerageId),
      ),
    );
  }
}

// ── Strategy card ─────────────────────────────────────────────────────────────

class _StrategyCard extends ConsumerWidget {
  const _StrategyCard({required this.inst});
  final Instance inst;

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final ctrl = ref.read(instanceDetailControllerProvider.notifier);
    final stratName = (inst.strategy?['name'] ?? inst.strategyId ?? '—').toString();
    final subStrategies = inst.strategy?['strategies'] as List? ?? const [];

    return GlassCard(
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Row(
            children: [
              Text('Strategy', style: AppTextStyles.cardTitle),
              const Spacer(),
              if (inst.strategyId == null)
                _TinyBtn(
                  icon: symbol('link'),
                  label: 'Link',
                  color: AppColors.primary,
                  onTap: () => _showLinkStrategy(context, ref),
                )
              else
                _TinyBtn(
                  icon: symbol('close'),
                  label: 'Unlink',
                  color: AppColors.danger,
                  onTap: () async {
                    await showConfirmDialog(
                      context,
                      title: 'Unlink Strategy',
                      body: 'Remove the strategy from this instance?',
                      confirmLabel: 'Unlink',
                      confirmColor: AppColors.danger,
                      icon: symbol('close'),
                      onConfirm: () async => ctrl.unlinkStrategy(),
                    );
                  },
                ),
            ],
          ),
          const SizedBox(height: 12),
          if (inst.strategyId == null)
            Text('No strategy linked',
                style: AppTextStyles.meta.copyWith(
                    fontStyle: FontStyle.italic))
          else ...[
            _InfoRow(label: 'Name', value: stratName),
            _InfoRow(label: 'ID', value: inst.strategyId ?? '—'),
            if (subStrategies.isNotEmpty) ...[
              const SizedBox(height: 8),
              Text('Sub-strategies',
                  style: AppTextStyles.micro
                      .copyWith(color: AppColors.textDim, fontWeight: FontWeight.w600)),
              const SizedBox(height: 6),
              for (final sub in subStrategies.take(5))
                if (sub is Map)
                  Padding(
                    padding: const EdgeInsets.only(bottom: 4),
                    child: Row(
                      children: [
                        Container(
                          width: 4,
                          height: 4,
                          margin: const EdgeInsets.only(right: 6, top: 2),
                          decoration: BoxDecoration(
                              color: AppColors.primary,
                              shape: BoxShape.circle),
                        ),
                        Expanded(
                          child: Text(
                            sub['strategy']?.toString() ?? '?',
                            style: AppTextStyles.meta,
                            overflow: TextOverflow.ellipsis,
                          ),
                        ),
                      ],
                    ),
                  ),
              if (subStrategies.length > 5)
                Text('+${subStrategies.length - 5} more',
                    style: AppTextStyles.nano),
            ],
          ],
        ],
      ),
    );
  }

  Future<void> _showLinkStrategy(BuildContext context, WidgetRef ref) async {
    await showModalBottomSheet<void>(
      context: context,
      isScrollControlled: true,
      backgroundColor: Colors.transparent,
      builder: (_) => ProviderScope(
        child: _LinkStrategyDetailSheet(instanceId: inst.id),
      ),
    );
  }
}

// ── Stocks card ───────────────────────────────────────────────────────────────

class _StocksCard extends ConsumerWidget {
  const _StocksCard({required this.inst});
  final Instance inst;

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final ctrl = ref.read(instanceDetailControllerProvider.notifier);

    return GlassCard(
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Row(
            children: [
              Text('Stocks (${inst.stocks.length})',
                  style: AppTextStyles.cardTitle),
              const Spacer(),
              _TinyBtn(
                icon: symbol('add'),
                label: 'Add',
                color: AppColors.primary,
                onTap: () => _showAddStock(context, ref),
              ),
            ],
          ),
          const SizedBox(height: 10),
          if (inst.stocks.isEmpty)
            Text('No stocks added',
                style: AppTextStyles.meta
                    .copyWith(fontStyle: FontStyle.italic))
          else
            Wrap(
              spacing: 6,
              runSpacing: 6,
              children: [
                for (final sym in inst.stocks)
                  _StockChip(
                    sym: sym,
                    onRemove: () => ctrl.removeStock(sym),
                  ),
              ],
            ),
        ],
      ),
    );
  }

  Future<void> _showAddStock(BuildContext context, WidgetRef ref) async {
    await showModalBottomSheet<void>(
      context: context,
      isScrollControlled: true,
      backgroundColor: Colors.transparent,
      builder: (_) => ProviderScope(
        child: _AddStockDetailSheet(instanceId: inst.id),
      ),
    );
  }
}

class _StockChip extends StatelessWidget {
  const _StockChip({required this.sym, this.onRemove});
  final String sym;
  final VoidCallback? onRemove;

  @override
  Widget build(BuildContext context) {
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 3),
      decoration: BoxDecoration(
        color: AppColors.surface,
        borderRadius: BorderRadius.circular(6),
        border: Border.all(color: AppColors.border),
      ),
      child: Row(
        mainAxisSize: MainAxisSize.min,
        children: [
          Text(sym, style: AppTextStyles.mono(11, color: AppColors.textMd)),
          if (onRemove != null) ...[
            const SizedBox(width: 4),
            GestureDetector(
              onTap: onRemove,
              child: Icon(symbol('close'), size: 11, color: AppColors.textFaint),
            ),
          ],
        ],
      ),
    );
  }
}

// ── Backtests section ─────────────────────────────────────────────────────────

class _BacktestsSection extends ConsumerWidget {
  const _BacktestsSection({required this.state});
  final InstanceDetailState state;

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final ctrl = ref.read(instanceDetailControllerProvider.notifier);

    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Row(
          children: [
            Text('Backtests', style: AppTextStyles.h3),
            const Spacer(),
            _TinyBtn(
              icon: symbol('play_circle'),
              label: 'New Backtest',
              color: AppColors.info,
              onTap: () => _showCreateBacktest(context, ref),
            ),
          ],
        ),
        const SizedBox(height: 6),

        if (state.btLoading && state.backtests.isEmpty)
          const Padding(
            padding: EdgeInsets.all(16),
            child: LoadingState(label: 'Loading backtests…'),
          )
        else if (state.backtests.isEmpty)
          GlassCard(
            padding: const EdgeInsets.all(24),
            child: Center(
              child: Text(
                'No backtests yet',
                style: AppTextStyles.meta.copyWith(fontStyle: FontStyle.italic),
              ),
            ),
          )
        else ...[
          // Sort header
          Padding(
            padding: const EdgeInsets.only(bottom: 6),
            child: Row(
              children: [
                _SortBtn(
                    label: 'Date',
                    field: 'completed_at',
                    current: state.btSortBy,
                    order: state.btSortOrder,
                    onTap: () => ctrl.sortBacktests('completed_at')),
                const SizedBox(width: 8),
                _SortBtn(
                    label: 'PnL',
                    field: 'pnl',
                    current: state.btSortBy,
                    order: state.btSortOrder,
                    onTap: () => ctrl.sortBacktests('pnl')),
              ],
            ),
          ),
          for (final bt in state.backtests) ...[
            _BacktestCard(
              bt: bt,
              progress: state.btProgress[bt.id],
            ),
            const SizedBox(height: 8),
          ],
          // Pagination
          if (state.btTotalPages > 1)
            _Pagination(
              page: state.btPage,
              totalPages: state.btTotalPages,
              onPage: (p) => ctrl.goToBacktestPage(p),
            ),
        ],
      ],
    );
  }

  Future<void> _showCreateBacktest(BuildContext context, WidgetRef ref) async {
    await showModalBottomSheet<void>(
      context: context,
      isScrollControlled: true,
      backgroundColor: Colors.transparent,
      builder: (_) => ProviderScope(
        child: const _CreateBacktestDetailSheet(),
      ),
    );
  }
}

class _SortBtn extends StatelessWidget {
  const _SortBtn({
    required this.label,
    required this.field,
    required this.current,
    required this.order,
    required this.onTap,
  });

  final String label;
  final String field;
  final String current;
  final String order;
  final VoidCallback onTap;

  @override
  Widget build(BuildContext context) {
    final active = current == field;
    return GestureDetector(
      onTap: onTap,
      child: Row(
        mainAxisSize: MainAxisSize.min,
        children: [
          Text(
            label,
            style: AppTextStyles.micro.copyWith(
              color: active ? AppColors.primary : AppColors.textMuted,
              fontWeight: FontWeight.w600,
            ),
          ),
          if (active) ...[
            const SizedBox(width: 2),
            Icon(
              order == 'asc' ? symbol('arrow_upward') : symbol('arrow_downward'),
              size: 11,
              color: AppColors.primary,
            ),
          ] else
            Icon(symbol('unfold_more'), size: 11, color: AppColors.textFaint),
        ],
      ),
    );
  }
}

class _BacktestCard extends StatelessWidget {
  const _BacktestCard({required this.bt, this.progress});
  final BacktestRow bt;
  final int? progress;

  @override
  Widget build(BuildContext context) {
    final isRunning =
        ['running', 'queued', 'pending'].contains(bt.status.toLowerCase());
    final statusColor = StatusPill.colorForStatus(bt.status);

    return GlassCard(
      onTap: () => context.push('/backtests/${bt.id}'),
      padding: const EdgeInsets.all(14),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Row(
            children: [
              Expanded(
                child: Text(
                  bt.stocks.isEmpty ? '(no stocks)' : bt.stocks.join(', '),
                  style: AppTextStyles.meta,
                  overflow: TextOverflow.ellipsis,
                ),
              ),
              const SizedBox(width: 8),
              StatusPill(
                label: bt.status,
                color: statusColor,
                pulsing: isRunning,
              ),
            ],
          ),
          const SizedBox(height: 6),
          Row(
            children: [
              if (bt.startDate != null)
                Expanded(
                  child: Text(
                    '${bt.startDate} → ${bt.endDate ?? '?'}',
                    style: AppTextStyles.nano,
                    overflow: TextOverflow.ellipsis,
                  ),
                ),
              if (bt.pnl != null)
                Text(
                  fmtPnl(bt.pnl),
                  style: AppTextStyles.mono(11,
                      color: pnlColor(bt.pnl),
                      weight: FontWeight.w600),
                ),
            ],
          ),
          if (isRunning && progress != null) ...[
            const SizedBox(height: 6),
            LinearProgressIndicator(
              value: (progress! / 100.0).clamp(0.0, 1.0),
              backgroundColor: AppColors.border,
              color: AppColors.info,
              minHeight: 3,
            ),
            const SizedBox(height: 2),
            Text('$progress%', style: AppTextStyles.nano),
          ],
          if (bt.completedAt != null) ...[
            const SizedBox(height: 4),
            Text(
              'Completed: ${_fmtDate(bt.completedAt!)}',
              style: AppTextStyles.nano,
            ),
          ],
        ],
      ),
    );
  }

  String _fmtDate(String raw) {
    try {
      return fmtDateTime(raw);
    } catch (_) {
      return raw;
    }
  }
}

class _Pagination extends StatelessWidget {
  const _Pagination({
    required this.page,
    required this.totalPages,
    required this.onPage,
  });

  final int page;
  final int totalPages;
  final ValueChanged<int> onPage;

  @override
  Widget build(BuildContext context) {
    return Row(
      mainAxisAlignment: MainAxisAlignment.center,
      children: [
        IconButton(
          icon: Icon(symbol('arrow_back'), size: 18),
          color: page > 1 ? AppColors.textMuted : AppColors.textFaint,
          onPressed: page > 1 ? () => onPage(page - 1) : null,
        ),
        Text(
          'Page $page of $totalPages',
          style: AppTextStyles.meta,
        ),
        IconButton(
          icon: Icon(symbol('arrow_forward'), size: 18),
          color: page < totalPages ? AppColors.textMuted : AppColors.textFaint,
          onPressed: page < totalPages ? () => onPage(page + 1) : null,
        ),
      ],
    );
  }
}

// ── Clear State sheet ─────────────────────────────────────────────────────────

const _kClearScopes = [
  _ClearScopeOpt(
    value: 'lookback_only',
    label: 'Lookback only',
    blurb:
        'GraphNexusTradeContexts + GraphNexusOutcomes. Forces lookback re-build on next run; other instance state untouched.',
  ),
  _ClearScopeOpt(
    value: 'strategy_cache_only',
    label: 'DB strategy cache only',
    blurb:
        'NexusStrategyCache rows for this instance (non-backtest origin). Backtest snapshots preserved.',
  ),
  _ClearScopeOpt(
    value: 'full_instance',
    label: 'Full instance reset',
    blurb:
        'Every per-instance table — lookback, runtime state, discovery, market trends, rotation cooldown, learning cache, trade outcomes, analyst panel, strategy cache (non-backtest). Shared caches and backtest snapshots preserved.',
  ),
];

class _ClearScopeOpt {
  const _ClearScopeOpt(
      {required this.value, required this.label, required this.blurb});
  final String value;
  final String label;
  final String blurb;
}

class _ClearStateSheet extends ConsumerStatefulWidget {
  const _ClearStateSheet({required this.instanceId});
  final String instanceId;

  @override
  ConsumerState<_ClearStateSheet> createState() => _ClearStateSheetState();
}

class _ClearStateSheetState extends ConsumerState<_ClearStateSheet> {
  String _scope = 'lookback_only';
  Map<String, dynamic>? _preview;
  bool _previewing = false;
  bool _applying = false;
  bool _confirmed = false;
  String? _error;
  String? _successMsg;

  Future<void> _runPreview() async {
    setState(() {
      _previewing = true;
      _error = null;
      _preview = null;
      _successMsg = null;
    });
    try {
      final result = await ref
          .read(instanceDetailControllerProvider.notifier)
          .previewClearState(_scope);
      setState(() {
        _preview = result;
        _previewing = false;
      });
    } catch (e) {
      setState(() {
        _error = 'Preview failed: $e';
        _previewing = false;
      });
    }
  }

  Future<void> _apply() async {
    if (!_confirmed) return;
    setState(() {
      _applying = true;
      _error = null;
      _successMsg = null;
    });
    try {
      final result = await ref
          .read(instanceDetailControllerProvider.notifier)
          .applyClearState(_scope);
      final deleted = result['total_deleted'] ?? 0;
      final tables = (result['tables'] as List?)?.length ?? 0;
      if (mounted) {
        setState(() {
          _successMsg = 'Deleted $deleted row(s) across $tables table(s).';
          _applying = false;
        });
      }
    } catch (e) {
      setState(() {
        _error = 'Clear failed: $e';
        _applying = false;
      });
    }
  }

  @override
  Widget build(BuildContext context) {
    return Padding(
      padding: EdgeInsets.only(bottom: MediaQuery.viewInsetsOf(context).bottom),
      child: GlassCard(
        padding: EdgeInsets.zero,
        borderRadius: 20,
        child: Column(
          mainAxisSize: MainAxisSize.min,
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            // Header
            Container(
              padding: const EdgeInsets.fromLTRB(20, 16, 16, 14),
              decoration: BoxDecoration(
                border: const Border(
                    bottom: BorderSide(color: AppColors.border)),
                borderRadius: const BorderRadius.vertical(
                    top: Radius.circular(20)),
              ),
              child: Row(
                children: [
                  Container(
                    width: 36,
                    height: 36,
                    decoration: BoxDecoration(
                      color: AppColors.fill(AppColors.danger),
                      borderRadius: BorderRadius.circular(10),
                      border: Border.all(
                          color: AppColors.stroke(AppColors.danger)),
                    ),
                    child:
                        Icon(symbol('delete_sweep'), size: 18, color: AppColors.danger),
                  ),
                  const SizedBox(width: 12),
                  Expanded(
                      child:
                          Text('Clear Instance State', style: AppTextStyles.h3)),
                  IconButton(
                    icon: Icon(symbol('close'), color: AppColors.textMuted),
                    onPressed: (_applying || _previewing)
                        ? null
                        : () => Navigator.pop(context),
                  ),
                ],
              ),
            ),
            Flexible(
              child: SingleChildScrollView(
                padding: const EdgeInsets.all(20),
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    // Scope radio buttons
                    for (final opt in _kClearScopes) ...[
                      GestureDetector(
                        onTap: (_applying || _previewing)
                            ? null
                            : () => setState(() => _scope = opt.value),
                        child: Row(
                          crossAxisAlignment: CrossAxisAlignment.start,
                          children: [
                            Radio<String>(
                              value: opt.value,
                              groupValue: _scope,
                              onChanged: (_applying || _previewing)
                                  ? null
                                  : (v) =>
                                      setState(() => _scope = v ?? opt.value),
                              activeThumbColor: AppColors.primary,
                            ),
                            Expanded(
                              child: Padding(
                                padding: const EdgeInsets.only(top: 10),
                                child: Column(
                                  crossAxisAlignment:
                                      CrossAxisAlignment.start,
                                  children: [
                                    Text(opt.label,
                                        style:
                                            AppTextStyles.cardTitle),
                                    const SizedBox(height: 2),
                                    Text(opt.blurb,
                                        style: AppTextStyles.nano),
                                  ],
                                ),
                              ),
                            ),
                          ],
                        ),
                      ),
                      const SizedBox(height: 4),
                    ],
                    const SizedBox(height: 8),
                    // Preview button
                    AppButton.ghost(
                      label: 'Preview Dry Run',
                      busy: _previewing,
                      onPressed:
                          (_applying || _previewing) ? null : _runPreview,
                    ),
                    // Preview result
                    if (_preview != null) ...[
                      const SizedBox(height: 12),
                      Container(
                        padding: const EdgeInsets.all(12),
                        decoration: BoxDecoration(
                          color: AppColors.surface.withValues(alpha: 0.5),
                          borderRadius: BorderRadius.circular(8),
                          border: Border.all(color: AppColors.border),
                        ),
                        child: Column(
                          crossAxisAlignment: CrossAxisAlignment.start,
                          children: [
                            Text('Preview Results',
                                style: AppTextStyles.micro.copyWith(
                                    color: AppColors.textDim,
                                    fontWeight: FontWeight.w600)),
                            const SizedBox(height: 6),
                            Text(
                                'Total rows to delete: ${_preview!['total_deleted'] ?? 0}',
                                style: AppTextStyles.meta),
                            if (_preview!['tables'] is List)
                              for (final t
                                  in (_preview!['tables'] as List).take(10))
                                Padding(
                                  padding: const EdgeInsets.only(top: 2),
                                  child: Text('• $t',
                                      style: AppTextStyles.nano),
                                ),
                          ],
                        ),
                      ),
                      const SizedBox(height: 12),
                      // Type-to-confirm
                      TypedConfirmField(
                        phrase: widget.instanceId,
                        label: 'Type "${widget.instanceId}" to enable destructive confirm',
                        onMatchChanged: (m) =>
                            setState(() => _confirmed = m),
                      ),
                    ],
                    if (_error != null) ...[
                      const SizedBox(height: 12),
                      ErrorBanner(message: _error!),
                    ],
                    if (_successMsg != null) ...[
                      const SizedBox(height: 12),
                      Container(
                        padding: const EdgeInsets.all(12),
                        decoration: BoxDecoration(
                          color: AppColors.fill(AppColors.success),
                          borderRadius: BorderRadius.circular(8),
                          border: Border.all(
                              color: AppColors.stroke(AppColors.success)),
                        ),
                        child: Text(_successMsg!,
                            style: AppTextStyles.meta
                                .copyWith(color: AppColors.success)),
                      ),
                    ],
                  ],
                ),
              ),
            ),
            // Footer
            if (_preview != null)
              Padding(
                padding: const EdgeInsets.fromLTRB(20, 0, 20, 20),
                child: AppButton.semantic(
                  label: 'Confirm and Clear',
                  color: AppColors.danger,
                  icon: symbol('delete'),
                  busy: _applying,
                  onPressed: (_confirmed && !_applying) ? _apply : null,
                ),
              ),
          ],
        ),
      ),
    );
  }
}

// ── Detail-specific modal sheets ──────────────────────────────────────────────

class _AddStockDetailSheet extends ConsumerStatefulWidget {
  const _AddStockDetailSheet({required this.instanceId});
  final String instanceId;

  @override
  ConsumerState<_AddStockDetailSheet> createState() =>
      _AddStockDetailSheetState();
}

class _AddStockDetailSheetState extends ConsumerState<_AddStockDetailSheet> {
  final _ctrl = TextEditingController();
  bool _busy = false;
  String? _error;

  @override
  void dispose() {
    _ctrl.dispose();
    super.dispose();
  }

  Future<void> _submit() async {
    final sym = _ctrl.text.trim().toUpperCase();
    if (sym.isEmpty) {
      setState(() => _error = 'Symbol is required');
      return;
    }
    setState(() {
      _busy = true;
      _error = null;
    });
    try {
      await ref.read(instanceDetailControllerProvider.notifier).addStock(sym);
      if (mounted) Navigator.pop(context);
    } catch (e) {
      setState(() {
        _busy = false;
        _error = e.toString();
      });
    }
  }

  @override
  Widget build(BuildContext context) {
    return _SimpleSheet(
      title: 'Add Stock',
      icon: symbol('add'),
      busy: _busy,
      onSubmit: _submit,
      submitLabel: 'Add',
      child: Column(
        children: [
          _FieldSm(
            label: 'Symbol *',
            ctrl: _ctrl,
            hint: 'e.g. AAPL',
            textCapitalization: TextCapitalization.characters,
          ),
          if (_error != null) ...[
            const SizedBox(height: 12),
            ErrorBanner(message: _error!),
          ],
        ],
      ),
    );
  }
}

class _LinkBrokerageSheet extends ConsumerStatefulWidget {
  const _LinkBrokerageSheet({this.currentId});
  final String? currentId;

  @override
  ConsumerState<_LinkBrokerageSheet> createState() =>
      _LinkBrokerageSheetState();
}

class _LinkBrokerageSheetState extends ConsumerState<_LinkBrokerageSheet> {
  String _brokerageId = '';
  bool _busy = false;
  String? _error;

  @override
  void initState() {
    super.initState();
    _brokerageId = widget.currentId ?? '';
  }

  Future<void> _submit() async {
    if (_brokerageId.isEmpty) {
      setState(() => _error = 'Select a brokerage');
      return;
    }
    setState(() {
      _busy = true;
      _error = null;
    });
    try {
      await ref
          .read(instanceDetailControllerProvider.notifier)
          .linkBrokerage(_brokerageId);
      if (mounted) Navigator.pop(context);
    } catch (e) {
      setState(() {
        _busy = false;
        _error = e.toString();
      });
    }
  }

  @override
  Widget build(BuildContext context) {
    final brokeragesAsync = ref.watch(brokeragesProvider);

    return _SimpleSheet(
      title: 'Link Brokerage',
      icon: symbol('account_balance'),
      busy: _busy,
      onSubmit: _submit,
      submitLabel: 'Link',
      child: Column(
        children: [
          brokeragesAsync.when(
            loading: () => const LoadingState(),
            error: (e, _) => ErrorBanner(message: e.toString()),
            data: (list) => _DropdownSm(
              value: _brokerageId.isEmpty ? null : _brokerageId,
              hint: 'Select a brokerage',
              items: [
                for (final b in list)
                  DropdownMenuItem(
                    value: b['id'].toString(),
                    child: Text(
                      '${b['account_name'] ?? ''} (${b['brokerage_type'] ?? ''})'.trim(),
                      overflow: TextOverflow.ellipsis,
                    ),
                  ),
              ],
              onChanged: (v) => setState(() => _brokerageId = v ?? ''),
            ),
          ),
          if (_error != null) ...[
            const SizedBox(height: 12),
            ErrorBanner(message: _error!),
          ],
        ],
      ),
    );
  }
}

class _LinkStrategyDetailSheet extends ConsumerStatefulWidget {
  const _LinkStrategyDetailSheet({required this.instanceId});
  final String instanceId;

  @override
  ConsumerState<_LinkStrategyDetailSheet> createState() =>
      _LinkStrategyDetailSheetState();
}

class _LinkStrategyDetailSheetState
    extends ConsumerState<_LinkStrategyDetailSheet> {
  String _strategyId = '';
  bool _busy = false;
  String? _error;

  Future<void> _submit() async {
    if (_strategyId.isEmpty) {
      setState(() => _error = 'Select a strategy');
      return;
    }
    setState(() {
      _busy = true;
      _error = null;
    });
    try {
      await ref
          .read(instanceDetailControllerProvider.notifier)
          .linkStrategy(_strategyId);
      if (mounted) Navigator.pop(context);
    } catch (e) {
      setState(() {
        _busy = false;
        _error = e.toString();
      });
    }
  }

  @override
  Widget build(BuildContext context) {
    final strategiesAsync = ref.watch(strategiesProvider);

    return _SimpleSheet(
      title: 'Link Strategy',
      icon: symbol('link'),
      busy: _busy,
      onSubmit: _submit,
      submitLabel: 'Link',
      child: Column(
        children: [
          strategiesAsync.when(
            loading: () => const LoadingState(),
            error: (e, _) => ErrorBanner(message: e.toString()),
            data: (list) => _DropdownSm(
              value: _strategyId.isEmpty ? null : _strategyId,
              hint: 'Select a strategy',
              items: [
                for (final s in list)
                  DropdownMenuItem(
                    value: s['id'].toString(),
                    child: Text(
                      s['name']?.toString() ?? s['id'].toString(),
                      overflow: TextOverflow.ellipsis,
                    ),
                  ),
              ],
              onChanged: (v) => setState(() => _strategyId = v ?? ''),
            ),
          ),
          if (_error != null) ...[
            const SizedBox(height: 12),
            ErrorBanner(message: _error!),
          ],
        ],
      ),
    );
  }
}

class _CreateBacktestDetailSheet extends ConsumerStatefulWidget {
  const _CreateBacktestDetailSheet();

  @override
  ConsumerState<_CreateBacktestDetailSheet> createState() =>
      _CreateBacktestDetailSheetState();
}

class _CreateBacktestDetailSheetState
    extends ConsumerState<_CreateBacktestDetailSheet> {
  final _stocksCtrl = TextEditingController();
  final _startCtrl = TextEditingController();
  final _endCtrl = TextEditingController();
  final _cashCtrl = TextEditingController(text: '100000');
  String _granularity = '60';
  bool _busy = false;
  String? _error;

  @override
  void dispose() {
    _stocksCtrl.dispose();
    _startCtrl.dispose();
    _endCtrl.dispose();
    _cashCtrl.dispose();
    super.dispose();
  }

  Future<void> _submit() async {
    if (_startCtrl.text.isEmpty) {
      setState(() => _error = 'Start date is required');
      return;
    }
    if (_endCtrl.text.isEmpty) {
      setState(() => _error = 'End date is required');
      return;
    }
    if (_startCtrl.text.compareTo(_endCtrl.text) >= 0) {
      setState(() => _error = 'End date must be after start date');
      return;
    }
    setState(() {
      _busy = true;
      _error = null;
    });
    final stocks = _stocksCtrl.text
        .split(',')
        .map((s) => s.trim().toUpperCase())
        .where((s) => s.isNotEmpty)
        .toList();
    try {
      await ref.read(instanceDetailControllerProvider.notifier).createBacktest(
            stocks: stocks,
            startDate: _startCtrl.text,
            endDate: _endCtrl.text,
            granularity: _granularity,
            initialCash: double.tryParse(_cashCtrl.text) ?? 100000,
          );
      if (mounted) Navigator.pop(context);
    } catch (e) {
      setState(() {
        _busy = false;
        _error = e.toString();
      });
    }
  }

  @override
  Widget build(BuildContext context) {
    return _SimpleSheet(
      title: 'Create Backtest',
      icon: symbol('play_circle'),
      busy: _busy,
      onSubmit: _submit,
      submitLabel: 'Create',
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          _FieldSm(
            label: 'Stocks (comma-separated)',
            ctrl: _stocksCtrl,
            hint: 'AAPL, TSLA',
            textCapitalization: TextCapitalization.characters,
          ),
          const SizedBox(height: 10),
          Row(
            children: [
              Expanded(
                  child: _FieldSm(
                      label: 'Start *',
                      ctrl: _startCtrl,
                      hint: 'YYYY-MM-DD')),
              const SizedBox(width: 10),
              Expanded(
                  child: _FieldSm(
                      label: 'End *',
                      ctrl: _endCtrl,
                      hint: 'YYYY-MM-DD')),
            ],
          ),
          const SizedBox(height: 10),
          Text('Granularity', style: AppTextStyles.micro),
          const SizedBox(height: 4),
          _GranPills(
              selected: _granularity,
              onSelect: (v) => setState(() => _granularity = v)),
          const SizedBox(height: 10),
          _FieldSm(
            label: 'Initial Cash (\$)',
            ctrl: _cashCtrl,
            hint: '100000',
            keyboardType: TextInputType.number,
          ),
          if (_error != null) ...[
            const SizedBox(height: 10),
            ErrorBanner(message: _error!),
          ],
        ],
      ),
    );
  }
}

// ── Tiny button used in card headers ─────────────────────────────────────────

class _TinyBtn extends StatelessWidget {
  const _TinyBtn({
    required this.icon,
    required this.label,
    required this.color,
    required this.onTap,
  });

  final IconData icon;
  final String label;
  final Color color;
  final VoidCallback? onTap;

  @override
  Widget build(BuildContext context) {
    return GestureDetector(
      onTap: onTap,
      child: Row(
        mainAxisSize: MainAxisSize.min,
        children: [
          Icon(icon, size: 13, color: color),
          const SizedBox(width: 3),
          Text(label,
              style: AppTextStyles.nano.copyWith(
                  color: color, fontWeight: FontWeight.w600)),
        ],
      ),
    );
  }
}

// ── Info row ─────────────────────────────────────────────────────────────────

class _InfoRow extends StatelessWidget {
  const _InfoRow({required this.label, required this.value});
  final String label;
  final String value;

  @override
  Widget build(BuildContext context) {
    return Padding(
      padding: const EdgeInsets.only(bottom: 4),
      child: Row(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          SizedBox(
            width: 120,
            child: Text('$label:',
                style: AppTextStyles.meta.copyWith(color: AppColors.textDim)),
          ),
          Expanded(
            child: Text(value, style: AppTextStyles.meta,
                overflow: TextOverflow.ellipsis),
          ),
        ],
      ),
    );
  }
}

// ── Simpler sheet shell for detail modals ─────────────────────────────────────

class _SimpleSheet extends StatelessWidget {
  const _SimpleSheet({
    required this.title,
    required this.icon,
    required this.child,
    required this.onSubmit,
    required this.submitLabel,
    this.busy = false,
  });

  final String title;
  final IconData icon;
  final Widget child;
  final VoidCallback onSubmit;
  final String submitLabel;
  final bool busy;

  @override
  Widget build(BuildContext context) {
    return Padding(
      padding: EdgeInsets.only(bottom: MediaQuery.viewInsetsOf(context).bottom),
      child: GlassCard(
        padding: EdgeInsets.zero,
        borderRadius: 20,
        child: Column(
          mainAxisSize: MainAxisSize.min,
          children: [
            Container(
              padding: const EdgeInsets.fromLTRB(20, 16, 16, 14),
              decoration: const BoxDecoration(
                border:
                    Border(bottom: BorderSide(color: AppColors.border)),
              ),
              child: Row(
                children: [
                  IconTile(icon: icon, size: 32),
                  const SizedBox(width: 12),
                  Expanded(child: Text(title, style: AppTextStyles.h3)),
                  IconButton(
                    icon: Icon(symbol('close'), color: AppColors.textMuted),
                    onPressed: busy ? null : () => Navigator.pop(context),
                  ),
                ],
              ),
            ),
            Flexible(
              child: SingleChildScrollView(
                padding: const EdgeInsets.all(20),
                child: child,
              ),
            ),
            Padding(
              padding: const EdgeInsets.fromLTRB(20, 0, 20, 20),
              child: Row(
                children: [
                  Expanded(
                    child: AppButton.ghost(
                        label: 'Cancel',
                        onPressed:
                            busy ? null : () => Navigator.pop(context)),
                  ),
                  const SizedBox(width: 12),
                  Expanded(
                    child: AppButton.primary(
                        label: submitLabel,
                        busy: busy,
                        onPressed: busy ? null : onSubmit),
                  ),
                ],
              ),
            ),
          ],
        ),
      ),
    );
  }
}

// ── Smaller field / dropdown helpers ─────────────────────────────────────────

class _FieldSm extends StatelessWidget {
  const _FieldSm({
    required this.label,
    required this.ctrl,
    this.hint,
    this.keyboardType,
    this.textCapitalization = TextCapitalization.none,
  });

  final String label;
  final TextEditingController ctrl;
  final String? hint;
  final TextInputType? keyboardType;
  final TextCapitalization textCapitalization;

  @override
  Widget build(BuildContext context) {
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Text(label, style: AppTextStyles.micro),
        const SizedBox(height: 4),
        TextField(
          controller: ctrl,
          keyboardType: keyboardType,
          textCapitalization: textCapitalization,
          style: AppTextStyles.body,
          decoration: InputDecoration(hintText: hint),
        ),
      ],
    );
  }
}

class _DropdownSm extends StatelessWidget {
  const _DropdownSm({
    required this.items,
    required this.onChanged,
    this.value,
    this.hint,
  });

  final List<DropdownMenuItem<String>> items;
  final ValueChanged<String?> onChanged;
  final String? value;
  final String? hint;

  @override
  Widget build(BuildContext context) {
    return DropdownButtonFormField<String>(
      initialValue: value,
      hint: hint != null
          ? Text(hint!,
              style: AppTextStyles.body.copyWith(color: AppColors.textFaint))
          : null,
      items: items,
      onChanged: onChanged,
      dropdownColor: AppColors.panel,
      style: AppTextStyles.body,
      decoration: const InputDecoration(),
      isExpanded: true,
    );
  }
}

// ── Granularity pills (detail version) ───────────────────────────────────────

const _kGrans = [
  _GranOpt(label: '1 min', value: '60'),
  _GranOpt(label: '5 min', value: '300'),
  _GranOpt(label: '15 min', value: '900'),
  _GranOpt(label: '1 hr', value: '3600'),
  _GranOpt(label: '1 day', value: '86400'),
];

class _GranOpt {
  const _GranOpt({required this.label, required this.value});
  final String label;
  final String value;
}

class _GranPills extends StatelessWidget {
  const _GranPills({required this.selected, required this.onSelect});
  final String selected;
  final ValueChanged<String> onSelect;

  @override
  Widget build(BuildContext context) {
    return Wrap(
      spacing: 6,
      children: [
        for (final g in _kGrans)
          GestureDetector(
            onTap: () => onSelect(g.value),
            child: AnimatedContainer(
              duration: const Duration(milliseconds: 120),
              padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 5),
              decoration: BoxDecoration(
                color: selected == g.value
                    ? AppColors.fill(AppColors.primary)
                    : Colors.transparent,
                borderRadius: BorderRadius.circular(6),
                border: Border.all(
                  color: selected == g.value
                      ? AppColors.stroke(AppColors.primary)
                      : AppColors.border,
                ),
              ),
              child: Text(
                g.label,
                style: AppTextStyles.micro.copyWith(
                  color: selected == g.value
                      ? AppColors.primary
                      : AppColors.textMuted,
                  fontWeight: FontWeight.w600,
                ),
              ),
            ),
          ),
      ],
    );
  }
}
