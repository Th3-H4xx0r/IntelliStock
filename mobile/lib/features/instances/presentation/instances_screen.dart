import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';
import '../../../core/theme/app_colors.dart';
import '../../../core/theme/app_text_styles.dart';
import '../../../core/widgets/app_button.dart';
import '../../../core/widgets/app_toggle.dart';
import '../../../core/widgets/common_widgets.dart';
import '../../../core/widgets/confirm_dialog.dart';
import '../../../core/widgets/glass_card.dart';
import '../../../core/widgets/material_symbols.dart';
import '../../../core/widgets/skeleton.dart';
import '../../../core/widgets/status_pill.dart';
import '../application/instances_controller.dart';
import '../application/pinned_instances_controller.dart';
import '../data/models/instance.dart';

// ── Granularity constants ────────────────────────────────────────────────────

const _kGranularities = [
  _Gran(label: '1 min', value: '60'),
  _Gran(label: '5 min', value: '300'),
  _Gran(label: '15 min', value: '900'),
  _Gran(label: '1 hr', value: '3600'),
  _Gran(label: '1 day', value: '86400'),
];

class _Gran {
  const _Gran({required this.label, required this.value});
  final String label;
  final String value;
}

// ── InstancesScreen ──────────────────────────────────────────────────────────

class InstancesScreen extends ConsumerWidget {
  const InstancesScreen({super.key});

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final async = ref.watch(instancesControllerProvider);

    return async.when(
      loading: () => const _InstancesScreenSkeleton(),
      error: (e, _) => Padding(
        padding: const EdgeInsets.all(24),
        child: ErrorBanner(
          message: e.toString(),
          onRetry: () =>
              ref.invalidate(instancesControllerProvider),
        ),
      ),
      data: (state) => _InstancesBody(state: state),
    );
  }
}

// ── Skeleton ──────────────────────────────────────────────────────────────────

class _InstancesScreenSkeleton extends StatelessWidget {
  const _InstancesScreenSkeleton();

  @override
  Widget build(BuildContext context) {
    return ListView(
      padding: const EdgeInsets.fromLTRB(16, 16, 16, 120),
      physics: const NeverScrollableScrollPhysics(),
      children: [
        // Header skeleton: eyebrow + title + subtitle
        Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Skeleton(width: 80, height: 10, radius: 5),
            const SizedBox(height: 8),
            Skeleton(width: 160, height: 28, radius: 8),
            const SizedBox(height: 8),
            Skeleton(height: 12, radius: 6),
          ],
        ),
        const SizedBox(height: 16),
        // Filter pills skeleton
        Row(
          children: [
            Skeleton(width: 60, height: 28, radius: 8),
            const SizedBox(width: 6),
            Skeleton(width: 100, height: 28, radius: 8),
            const SizedBox(width: 6),
            Skeleton(width: 84, height: 28, radius: 8),
          ],
        ),
        const SizedBox(height: 16),
        // 4 instance card skeletons
        for (var i = 0; i < 4; i++) ...[
          const _InstanceCardSkeleton(),
          const SizedBox(height: 12),
        ],
      ],
    );
  }
}

class _InstanceCardSkeleton extends StatelessWidget {
  const _InstanceCardSkeleton();

  @override
  Widget build(BuildContext context) {
    return GlassCard(
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          // Header: icon circle + name/id lines + badge + pill
          Row(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Skeleton.circle(40),
              const SizedBox(width: 12),
              Expanded(
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Skeleton(width: 140, height: 14, radius: 6),
                    const SizedBox(height: 6),
                    Skeleton(width: 100, height: 10, radius: 5),
                  ],
                ),
              ),
              const SizedBox(width: 8),
              Column(
                crossAxisAlignment: CrossAxisAlignment.end,
                children: [
                  Skeleton(width: 36, height: 18, radius: 9),
                  const SizedBox(height: 6),
                  Skeleton(width: 56, height: 18, radius: 9),
                ],
              ),
            ],
          ),
          const SizedBox(height: 12),
          // Meta lines
          Skeleton(height: 12, radius: 6),
          const SizedBox(height: 6),
          Skeleton(width: 220, height: 12, radius: 6),
          const SizedBox(height: 12),
          // Chip blocks row
          Row(
            children: [
              Skeleton(width: 50, height: 22, radius: 6),
              const SizedBox(width: 6),
              Skeleton(width: 50, height: 22, radius: 6),
              const SizedBox(width: 6),
              Skeleton(width: 50, height: 22, radius: 6),
            ],
          ),
          const SizedBox(height: 12),
          // Action buttons row
          Row(
            children: [
              Skeleton(width: 60, height: 28, radius: 8),
              const SizedBox(width: 6),
              Skeleton(width: 80, height: 28, radius: 8),
              const SizedBox(width: 6),
              Skeleton(width: 64, height: 28, radius: 8),
            ],
          ),
        ],
      ),
    );
  }
}

// ── Body ─────────────────────────────────────────────────────────────────────

class _InstancesBody extends ConsumerWidget {
  const _InstancesBody({required this.state});

  final InstancesState state;

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final ctrl = ref.read(instancesControllerProvider.notifier);
    final pinned = ref.watch(pinnedInstancesProvider);
    final items = sortPinnedFirst(state.filtered, pinned);

    return RefreshIndicator(
      onRefresh: () => ctrl.refreshNow(),
      child: ListView(
        padding: const EdgeInsets.fromLTRB(16, 16, 16, 120),
        children: [
          // Header
          _Header(
            onRefresh: () => ctrl.refreshNow(),
            onNewInstance: () => _showCreateModal(context, ref),
          ),
          const SizedBox(height: 16),
          // Filter pills
          _FilterPills(state: state),
          const SizedBox(height: 16),
          // Instance list
          if (state.instances.isEmpty)
            EmptyState(
              icon: symbol('memory'),
              title: 'No instances yet',
              subtitle:
                  'Create an instance to run live trading or backtesting strategies.',
              actionLabel: 'Create your first instance',
              onAction: () => _showCreateModal(context, ref),
            )
          else if (state.filtered.isEmpty)
            EmptyState(
              icon: symbol('filter_alt_off'),
              title: state.filter == InstanceFilter.ai
                  ? 'No AI-created instances'
                  : 'No user-created instances',
            )
          else
            for (final inst in items) ...[
              _InstanceCard(
                inst: inst,
                busy: state.busyIds.contains(inst.id),
                pinned: pinned.contains(inst.id),
              ),
              const SizedBox(height: 12),
            ],
          if (state.errorMessage != null)
            ErrorBanner(message: state.errorMessage!),
        ],
      ),
    );
  }

  Future<void> _showCreateModal(BuildContext context, WidgetRef ref) async {
    await showModalBottomSheet<void>(
      context: context,
      isScrollControlled: true,
      backgroundColor: Colors.transparent,
      builder: (_) => ProviderScope(
        child: const _CreateInstanceSheet(),
      ),
    );
  }
}

// ── Header ───────────────────────────────────────────────────────────────────

class _Header extends StatelessWidget {
  const _Header({required this.onRefresh, required this.onNewInstance});
  final VoidCallback onRefresh;
  final VoidCallback onNewInstance;

  @override
  Widget build(BuildContext context) {
    return Row(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Expanded(
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Text('TRADING', style: AppTextStyles.eyebrow),
              const SizedBox(height: 4),
              Text('Instances', style: AppTextStyles.h1),
              const SizedBox(height: 4),
              Text(
                'Manage live trading and backtesting instances.',
                style: AppTextStyles.body.copyWith(color: AppColors.textDim),
              ),
            ],
          ),
        ),
        const SizedBox(width: 12),
        Row(
          children: [
            IconButton(
              icon: Icon(symbol('refresh'), color: AppColors.textMuted),
              tooltip: 'Refresh',
              onPressed: onRefresh,
            ),
            const SizedBox(width: 4),
            AppButton.primary(
              label: 'New Instance',
              icon: symbol('add'),
              onPressed: onNewInstance,
              dense: true,
            ),
          ],
        ),
      ],
    );
  }
}

// ── Filter pills ─────────────────────────────────────────────────────────────

class _FilterPills extends ConsumerWidget {
  const _FilterPills({required this.state});
  final InstancesState state;

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final ctrl = ref.read(instancesControllerProvider.notifier);
    return SingleChildScrollView(
      scrollDirection: Axis.horizontal,
      child: Row(
        children: [
          _Pill(
            label: 'All',
            count: state.allCount,
            selected: state.filter == InstanceFilter.all,
            onTap: () => ctrl.setFilter(InstanceFilter.all),
          ),
          const SizedBox(width: 6),
          _Pill(
            label: 'User Created',
            count: state.userCount,
            selected: state.filter == InstanceFilter.user,
            onTap: () => ctrl.setFilter(InstanceFilter.user),
          ),
          const SizedBox(width: 6),
          _Pill(
            label: 'AI Created',
            count: state.aiCount,
            selected: state.filter == InstanceFilter.ai,
            onTap: () => ctrl.setFilter(InstanceFilter.ai),
          ),
        ],
      ),
    );
  }
}

class _Pill extends StatelessWidget {
  const _Pill({
    required this.label,
    required this.count,
    required this.selected,
    required this.onTap,
  });

  final String label;
  final int count;
  final bool selected;
  final VoidCallback onTap;

  @override
  Widget build(BuildContext context) {
    return GestureDetector(
      onTap: onTap,
      child: AnimatedContainer(
        duration: const Duration(milliseconds: 150),
        padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 6),
        decoration: BoxDecoration(
          color: selected
              ? AppColors.fill(AppColors.primary)
              : Colors.transparent,
          borderRadius: BorderRadius.circular(8),
          border: Border.all(
            color: selected
                ? AppColors.stroke(AppColors.primary)
                : AppColors.border,
          ),
        ),
        child: Row(
          mainAxisSize: MainAxisSize.min,
          children: [
            Text(
              label,
              style: AppTextStyles.micro.copyWith(
                color: selected ? AppColors.primary : AppColors.textMuted,
                fontWeight: FontWeight.w600,
              ),
            ),
            const SizedBox(width: 4),
            Text(
              '($count)',
              style: AppTextStyles.nano.copyWith(
                color: selected
                    ? AppColors.primary.withValues(alpha: 0.6)
                    : AppColors.textFaint,
              ),
            ),
          ],
        ),
      ),
    );
  }
}

// ── Instance card ─────────────────────────────────────────────────────────────

class _InstanceCard extends ConsumerWidget {
  const _InstanceCard({required this.inst, required this.busy, this.pinned = false});

  final Instance inst;
  final bool busy;
  final bool pinned;

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final ctrl = ref.read(instancesControllerProvider.notifier);

    // Resolve strategy/brokerage names from instance data or nested maps.
    final strategyName = (inst.strategy?['name'] ?? inst.strategyId ?? '').toString();
    final brokerageName = inst.brokerage != null
        ? '${inst.brokerage!['account_name'] ?? ''} (${inst.brokerage!['brokerage_type'] ?? ''})'.trim()
        : (inst.brokerageId ?? '');

    final isAi = inst.createdBy == 'ai';
    final isRunning = inst.runCommand;

    return GlassCard(
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          // Card header row
          Row(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              IconTile(icon: symbol('memory')),
              const SizedBox(width: 12),
              Expanded(
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Text(
                      inst.name.isNotEmpty ? inst.name : inst.id,
                      style: AppTextStyles.cardTitle,
                      overflow: TextOverflow.ellipsis,
                    ),
                    const SizedBox(height: 2),
                    Text(
                      inst.id,
                      style: AppTextStyles.mono(11, color: AppColors.textDim),
                      overflow: TextOverflow.ellipsis,
                    ),
                  ],
                ),
              ),
              const SizedBox(width: 8),
              Column(
                crossAxisAlignment: CrossAxisAlignment.end,
                children: [
                  AppBadge(
                    label: isAi ? 'AI' : 'User',
                    color: isAi ? AppColors.primary : AppColors.textDim,
                  ),
                  const SizedBox(height: 6),
                  StatusPill(
                    label: isRunning ? 'Running' : 'Stopped',
                    color: isRunning ? AppColors.success : AppColors.textDim,
                    pulsing: isRunning,
                  ),
                ],
              ),
            ],
          ),

          const SizedBox(height: 12),

          // Meta: strategy / brokerage
          if (inst.strategyId != null)
            _MetaRow(label: 'Strategy', value: strategyName)
          else
            Text(
              'No strategy linked',
              style: AppTextStyles.meta
                  .copyWith(fontStyle: FontStyle.italic),
            ),
          if (inst.brokerageId != null) ...[
            const SizedBox(height: 4),
            _MetaRow(label: 'Brokerage', value: brokerageName),
          ],

          const SizedBox(height: 12),

          // Stocks chips
          _StocksChips(inst: inst, busy: busy),

          const SizedBox(height: 12),

          // Action buttons
          Wrap(
            spacing: 6,
            runSpacing: 6,
            children: [
              _ActionBtn(
                icon: symbol(pinned ? 'push_pin' : 'push_pin_outlined'),
                label: pinned ? 'Pinned' : 'Pin',
                color: pinned ? AppColors.warning : AppColors.textDim,
                onTap: () =>
                    ref.read(pinnedInstancesProvider.notifier).toggle(inst.id),
              ),
              _ActionBtn(
                icon: symbol('open_in_new'),
                label: 'View',
                color: AppColors.primary,
                onTap: () => context.push('/instances/${inst.id}'),
              ),
              _ActionBtn(
                icon: symbol('show_chart'),
                label: 'Live',
                color: AppColors.info,
                onTap: () => context.push('/instances/${inst.id}/live'),
              ),
              if (isRunning)
                _ActionBtn(
                  icon: busy ? symbol('progress_activity') : symbol('stop'),
                  label: 'Stop',
                  color: AppColors.warning,
                  onTap: busy ? null : () => ctrl.stop(inst.id),
                )
              else
                _ActionBtn(
                  icon: busy ? symbol('progress_activity') : symbol('play_arrow'),
                  label: 'Start',
                  color: AppColors.success,
                  onTap: busy ? null : () => ctrl.start(inst.id),
                ),
              _ActionBtn(
                icon: symbol('delete'),
                label: 'Delete',
                color: AppColors.danger,
                onTap: busy
                    ? null
                    : () => _confirmDelete(context, ref, inst),
              ),
            ],
          ),
        ],
      ),
    );
  }

  Future<void> _confirmDelete(
      BuildContext context, WidgetRef ref, Instance inst) async {
    final ctrl = ref.read(instancesControllerProvider.notifier);
    await showConfirmDialog(
      context,
      title: 'Delete Instance',
      body: 'Delete "${inst.name.isNotEmpty ? inst.name : inst.id}"? This cannot be undone.',
      confirmLabel: 'Delete',
      confirmColor: AppColors.danger,
      icon: symbol('delete'),
      onConfirm: () async {
        await ctrl.delete(inst.id);
      },
    );
  }
}

class _MetaRow extends StatelessWidget {
  const _MetaRow({required this.label, required this.value});
  final String label;
  final String value;

  @override
  Widget build(BuildContext context) {
    return Row(
      children: [
        Text('$label: ',
            style: AppTextStyles.meta.copyWith(color: AppColors.textDim)),
        Expanded(
          child: Text(
            value,
            style: AppTextStyles.meta,
            overflow: TextOverflow.ellipsis,
          ),
        ),
      ],
    );
  }
}

// ── Stocks chips section ──────────────────────────────────────────────────────

class _StocksChips extends ConsumerWidget {
  const _StocksChips({required this.inst, required this.busy});
  final Instance inst;
  final bool busy;

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final ctrl = ref.read(instancesControllerProvider.notifier);

    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Row(
          children: [
            Text(
              'STOCKS (${inst.stocks.length})',
              style: AppTextStyles.nano.copyWith(
                color: AppColors.textDim,
                fontWeight: FontWeight.w600,
                letterSpacing: 0.8,
              ),
            ),
            const Spacer(),
            GestureDetector(
              onTap: busy
                  ? null
                  : () => _showAddStockSheet(context, ref, inst.id),
              child: Row(
                mainAxisSize: MainAxisSize.min,
                children: [
                  Icon(symbol('add'), size: 13, color: AppColors.textFaint),
                  Text('Add',
                      style: AppTextStyles.nano
                          .copyWith(color: AppColors.textFaint)),
                ],
              ),
            ),
          ],
        ),
        const SizedBox(height: 6),
        if (inst.stocks.isEmpty)
          Text(
            'No stocks added',
            style: AppTextStyles.meta.copyWith(fontStyle: FontStyle.italic),
          )
        else
          Wrap(
            spacing: 6,
            runSpacing: 6,
            children: [
              for (final sym in inst.stocks)
                _StockChip(
                  sym: sym,
                  onRemove: busy
                      ? null
                      : () => ctrl.removeStock(inst.id, sym),
                ),
            ],
          ),
      ],
    );
  }

  Future<void> _showAddStockSheet(
      BuildContext context, WidgetRef ref, String instanceId) async {
    await showModalBottomSheet<void>(
      context: context,
      isScrollControlled: true,
      backgroundColor: Colors.transparent,
      builder: (_) => ProviderScope(
        child: _AddStockSheet(instanceId: instanceId),
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
              child: Icon(symbol('close'), size: 11,
                  color: AppColors.textFaint),
            ),
          ],
        ],
      ),
    );
  }
}

// ── Action button helper ──────────────────────────────────────────────────────

class _ActionBtn extends StatelessWidget {
  const _ActionBtn({
    required this.icon,
    required this.label,
    required this.color,
    this.onTap,
  });

  final IconData icon;
  final String label;
  final Color color;
  final VoidCallback? onTap;

  @override
  Widget build(BuildContext context) {
    final disabled = onTap == null;
    return Opacity(
      opacity: disabled ? 0.4 : 1,
      child: GestureDetector(
        onTap: onTap,
        child: Container(
          padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 6),
          decoration: BoxDecoration(
            color: AppColors.fill(color),
            borderRadius: BorderRadius.circular(8),
            border: Border.all(color: AppColors.stroke(color)),
          ),
          child: Row(
            mainAxisSize: MainAxisSize.min,
            children: [
              Icon(icon, size: 13, color: color),
              const SizedBox(width: 4),
              Text(
                label,
                style: AppTextStyles.nano.copyWith(
                  color: color,
                  fontWeight: FontWeight.w600,
                ),
              ),
            ],
          ),
        ),
      ),
    );
  }
}

// ── Create Instance bottom sheet ─────────────────────────────────────────────

class _CreateInstanceSheet extends ConsumerStatefulWidget {
  const _CreateInstanceSheet();

  @override
  ConsumerState<_CreateInstanceSheet> createState() =>
      _CreateInstanceSheetState();
}

class _CreateInstanceSheetState
    extends ConsumerState<_CreateInstanceSheet> {
  final _idCtrl = TextEditingController();
  final _nameCtrl = TextEditingController();
  final _maxUsageCtrl = TextEditingController();

  String _granularity = '60';
  bool _runCommand = false;
  String _brokerageId = '';
  String _strategyId = '';

  bool _busy = false;
  String? _error;

  @override
  void dispose() {
    _idCtrl.dispose();
    _nameCtrl.dispose();
    _maxUsageCtrl.dispose();
    super.dispose();
  }

  Future<void> _submit() async {
    final id = _idCtrl.text.trim();
    if (id.isEmpty) {
      setState(() => _error = 'Instance ID is required');
      return;
    }
    setState(() {
      _busy = true;
      _error = null;
    });
    try {
      await ref.read(instancesControllerProvider.notifier).createInstance(
            id: id,
            name: _nameCtrl.text.trim(),
            granularity: _granularity,
            runCommand: _runCommand,
            brokerageId: _brokerageId.isNotEmpty ? _brokerageId : null,
            maxUsage: _maxUsageCtrl.text.isNotEmpty
                ? double.tryParse(_maxUsageCtrl.text)
                : null,
            strategyId: _strategyId.isNotEmpty ? _strategyId : null,
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
    final brokeragesAsync = ref.watch(brokeragesProvider);
    final strategiesAsync = ref.watch(strategiesProvider);

    return _ModalSheet(
      title: 'New Instance',
      icon: symbol('add'),
      busy: _busy,
      onSubmit: _submit,
      submitLabel: 'Create',
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          _Field(label: 'Instance ID *', ctrl: _idCtrl, hint: 'my-instance'),
          const SizedBox(height: 12),
          _Field(label: 'Name', ctrl: _nameCtrl, hint: 'Optional display name'),
          const SizedBox(height: 12),
          // Granularity pills
          _SectionLabel('Granularity'),
          const SizedBox(height: 6),
          _GranularityPills(
            selected: _granularity,
            onSelect: (v) => setState(() => _granularity = v),
          ),
          const SizedBox(height: 12),
          // Start toggle
          Row(
            children: [
              Expanded(
                child: Text('Start after creation',
                    style: AppTextStyles.body),
              ),
              AppToggle(
                value: _runCommand,
                onChanged: (v) => setState(() => _runCommand = v),
              ),
            ],
          ),
          const SizedBox(height: 12),
          // Brokerage selector
          _SectionLabel('Brokerage (optional)'),
          const SizedBox(height: 6),
          brokeragesAsync.when(
            loading: () => const SizedBox(height: 40, child: LoadingState()),
            error: (e, _) => Text('Failed to load brokerages',
                style: AppTextStyles.meta.copyWith(color: AppColors.danger)),
            data: (list) {
              // Check if selected brokerage is Robinhood
              final selBrok = list.firstWhere(
                (b) => b['id'].toString() == _brokerageId,
                orElse: () => const {},
              );
              final isRH =
                  selBrok['brokerage_type']?.toString().toLowerCase() ==
                      'robinhood';
              return Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  _DropdownField(
                    value: _brokerageId.isEmpty ? null : _brokerageId,
                    hint: '— None —',
                    items: [
                      const DropdownMenuItem(
                          value: '', child: Text('— None —')),
                      for (final b in list)
                        DropdownMenuItem(
                          value: b['id'].toString(),
                          child: Text(
                            '${b['account_name'] ?? ''} (${b['brokerage_type'] ?? ''})'.trim(),
                            overflow: TextOverflow.ellipsis,
                          ),
                        ),
                    ],
                    onChanged: (v) =>
                        setState(() => _brokerageId = v ?? ''),
                  ),
                  if (isRH) ...[
                    const SizedBox(height: 6),
                    Container(
                      padding: const EdgeInsets.all(10),
                      decoration: BoxDecoration(
                        color: AppColors.fill(AppColors.warning),
                        borderRadius: BorderRadius.circular(8),
                        border: Border.all(
                            color: AppColors.stroke(AppColors.warning)),
                      ),
                      child: Row(
                        children: [
                          Icon(symbol('warning'),
                              size: 14, color: AppColors.warning),
                          const SizedBox(width: 8),
                          Expanded(
                            child: Text(
                              'Robinhood accounts use real money. Ensure you understand the risks before enabling live trading.',
                              style: AppTextStyles.nano
                                  .copyWith(color: AppColors.warning),
                            ),
                          ),
                        ],
                      ),
                    ),
                  ],
                ],
              );
            },
          ),
          const SizedBox(height: 12),
          _Field(label: 'Max Usage (\$)', ctrl: _maxUsageCtrl, hint: 'e.g. 1000', keyboardType: TextInputType.number),
          const SizedBox(height: 12),
          // Strategy selector
          _SectionLabel('Strategy (optional)'),
          const SizedBox(height: 6),
          strategiesAsync.when(
            loading: () => const SizedBox(height: 40, child: LoadingState()),
            error: (e, _) => Text('Failed to load strategies',
                style: AppTextStyles.meta.copyWith(color: AppColors.danger)),
            data: (list) => _DropdownField(
              value: _strategyId.isEmpty ? null : _strategyId,
              hint: '— None —',
              items: [
                const DropdownMenuItem(value: '', child: Text('— None —')),
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

// ── Add Stock sheet ───────────────────────────────────────────────────────────

class _AddStockSheet extends ConsumerStatefulWidget {
  const _AddStockSheet({required this.instanceId});
  final String instanceId;

  @override
  ConsumerState<_AddStockSheet> createState() => _AddStockSheetState();
}

class _AddStockSheetState extends ConsumerState<_AddStockSheet> {
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
      await ref
          .read(instancesControllerProvider.notifier)
          .addStock(widget.instanceId, sym);
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
    return _ModalSheet(
      title: 'Add Stock',
      icon: symbol('add_shopping_cart'),
      busy: _busy,
      onSubmit: _submit,
      submitLabel: 'Add',
      child: Column(
        children: [
          _Field(
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

// ── Shared sheet shell ────────────────────────────────────────────────────────

class _ModalSheet extends StatelessWidget {
  const _ModalSheet({
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
            // Header
            Container(
              padding: const EdgeInsets.fromLTRB(20, 16, 16, 14),
              decoration: const BoxDecoration(
                border: Border(bottom: BorderSide(color: AppColors.border)),
              ),
              child: Row(
                children: [
                  IconTile(icon: icon, size: 36),
                  const SizedBox(width: 12),
                  Expanded(
                      child: Text(title, style: AppTextStyles.h3)),
                  IconButton(
                    icon: Icon(symbol('close'), color: AppColors.textMuted),
                    onPressed:
                        busy ? null : () => Navigator.pop(context),
                  ),
                ],
              ),
            ),
            // Body
            Flexible(
              child: SingleChildScrollView(
                padding: const EdgeInsets.all(20),
                child: child,
              ),
            ),
            // Footer
            Padding(
              padding: const EdgeInsets.fromLTRB(20, 0, 20, 20),
              child: Row(
                children: [
                  Expanded(
                    child: AppButton.ghost(
                      label: 'Cancel',
                      onPressed:
                          busy ? null : () => Navigator.pop(context),
                    ),
                  ),
                  const SizedBox(width: 12),
                  Expanded(
                    child: AppButton.primary(
                      label: submitLabel,
                      busy: busy,
                      onPressed: busy ? null : onSubmit,
                    ),
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

// ── Granularity pills ─────────────────────────────────────────────────────────

class _GranularityPills extends StatelessWidget {
  const _GranularityPills({
    required this.selected,
    required this.onSelect,
  });

  final String selected;
  final ValueChanged<String> onSelect;

  @override
  Widget build(BuildContext context) {
    return Wrap(
      spacing: 6,
      children: [
        for (final g in _kGranularities)
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

// ── Small form helpers ────────────────────────────────────────────────────────

class _SectionLabel extends StatelessWidget {
  const _SectionLabel(this.text);
  final String text;

  @override
  Widget build(BuildContext context) {
    return Text(
      text,
      style: AppTextStyles.micro.copyWith(
        color: AppColors.textDim,
        fontWeight: FontWeight.w600,
      ),
    );
  }
}

class _Field extends StatelessWidget {
  const _Field({
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

class _DropdownField extends StatelessWidget {
  const _DropdownField({
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
