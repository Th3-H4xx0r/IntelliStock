import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import '../../../core/formatters/formatters.dart';
import '../../../core/theme/app_colors.dart';
import '../../../core/theme/app_text_styles.dart';
import '../../../core/widgets/app_background.dart';
import '../../../core/widgets/app_button.dart';
import '../../../core/widgets/common_widgets.dart';
import '../../../core/widgets/glass_card.dart';
import '../../../core/widgets/material_symbols.dart';
import '../../../core/widgets/status_pill.dart';
import '../../../core/widgets/typed_confirm_field.dart';
import '../application/nexus_controller.dart';
import '../data/nexus_repository.dart';
import 'nexus_logs_panel.dart';

// ── Helpers ────────────────────────────────────────────────────────────────────

Color _stageColor(String status) {
  switch (status.toLowerCase()) {
    case 'running':
      return AppColors.info;
    case 'completed':
    case 'skipped':
      return AppColors.success;
    case 'stopped':
      return AppColors.warning;
    case 'failed':
      return AppColors.danger;
    default:
      return AppColors.textFaint;
  }
}

IconData _stageIcon(String status) {
  switch (status.toLowerCase()) {
    case 'completed':
      return symbol('check_circle');
    case 'skipped':
      return symbol('remove_circle');
    case 'stopped':
      return symbol('pause_circle');
    case 'failed':
      return symbol('cancel');
    default:
      return symbol('radio_button_unchecked');
  }
}

bool _stageSpinning(String status) => status.toLowerCase() == 'running';

String _fmtDuration(double? sec) {
  if (sec == null) return '';
  if (sec < 60) return '${sec.toStringAsFixed(1)}s';
  if (sec >= 3600) {
    final h = (sec ~/ 3600);
    final m = ((sec % 3600) ~/ 60).round();
    return m > 0 ? '${h}h ${m}m' : '${h}h';
  }
  final m = (sec ~/ 60);
  final s = (sec % 60).round();
  return s > 0 ? '${m}m ${s}s' : '${m}m';
}

String _autoUpdateSummary(NexusControl c) {
  if (!c.autoUpdateEnabled) return 'Disabled';
  final hours = c.autoUpdateIntervalHours;
  if (hours % 24 == 0) {
    final d = hours ~/ 24;
    return 'Every $d day${d == 1 ? '' : 's'}';
  }
  return 'Every $hours hour${hours == 1 ? '' : 's'}';
}

// ── Screen ─────────────────────────────────────────────────────────────────────

class NexusScreen extends ConsumerWidget {
  const NexusScreen({super.key});

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final asyncState = ref.watch(nexusControllerProvider);

    return Scaffold(
      backgroundColor: AppColors.canvas,
      body: AppBackground(
        child: SafeArea(
          child: asyncState.when(
            loading: () =>
                const Center(child: LoadingState(label: 'Loading Nexus…')),
            error: (e, _) => _CannotLoad(
              onRetry: () =>
                  ref.read(nexusControllerProvider.notifier).refreshNow(),
            ),
            data: (state) => _NexusBody(state: state),
          ),
        ),
      ),
    );
  }
}

class _CannotLoad extends StatelessWidget {
  const _CannotLoad({required this.onRetry});
  final VoidCallback onRetry;

  @override
  Widget build(BuildContext context) {
    return Center(
      child: Column(
        mainAxisSize: MainAxisSize.min,
        children: [
          IconTile(icon: symbol('hub'), color: AppColors.primary, size: 64),
          const SizedBox(height: 16),
          Text('Unable to load Nexus status',
              style: AppTextStyles.cardTitle
                  .copyWith(color: AppColors.textMd)),
          const SizedBox(height: 8),
          Text('Check that the backend is reachable.',
              style: AppTextStyles.meta.copyWith(color: AppColors.textDim)),
          const SizedBox(height: 20),
          AppButton.ghost(label: 'Retry', onPressed: onRetry),
        ],
      ),
    );
  }
}

// ── Main body ──────────────────────────────────────────────────────────────────

class _NexusBody extends ConsumerWidget {
  const _NexusBody({required this.state});
  final NexusState state;

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final ctrl = ref.read(nexusControllerProvider.notifier);
    final status = state.status;

    return RefreshIndicator(
      onRefresh: ctrl.refreshNow,
      child: CustomScrollView(
        slivers: [
          // Header
          SliverToBoxAdapter(
            child: Padding(
              padding: const EdgeInsets.fromLTRB(16, 20, 16, 12),
              child: _NexusHeader(state: state),
            ),
          ),

          // Error banner
          if (state.errorMessage != null)
            SliverToBoxAdapter(
              child: Padding(
                padding: const EdgeInsets.symmetric(horizontal: 16),
                child: ErrorBanner(message: state.errorMessage!),
              ),
            ),

          // Auto-update summary card
          SliverToBoxAdapter(
            child: Padding(
              padding:
                  const EdgeInsets.symmetric(horizontal: 16, vertical: 6),
              child: _AutoUpdateCard(status: status, onConfigure: () {
                _showAutoUpdateModal(context, ctrl, status);
              }),
            ),
          ),

          // Historical Bootstrap card
          SliverToBoxAdapter(
            child: Padding(
              padding:
                  const EdgeInsets.symmetric(horizontal: 16, vertical: 6),
              child: _BootstrapCard(status: status),
            ),
          ),

          // Graph counts
          if ((status.graphSummary?.relationshipCounts.isNotEmpty ?? false) ||
              (status.graphSummary?.nodeCounts.isNotEmpty ?? false))
            SliverToBoxAdapter(
              child: Padding(
                padding:
                    const EdgeInsets.symmetric(horizontal: 16, vertical: 6),
                child: _GraphCountsCard(summary: status.graphSummary!),
              ),
            ),

          // BUILT state: hero + stage summary grid
          if (status.showBuilt)
            SliverToBoxAdapter(
              child: Padding(
                padding:
                    const EdgeInsets.symmetric(horizontal: 16, vertical: 6),
                child: _BuiltSection(
                    status: status,
                    onRebuild: () =>
                        _showRebuildModal(context, ctrl, status),
                    onDeleteEdges: () =>
                        _showDeleteModal(context, ctrl, status)),
              ),
            )
          // BUILDING / IDLE state
          else
            SliverToBoxAdapter(
              child: Padding(
                padding:
                    const EdgeInsets.symmetric(horizontal: 16, vertical: 6),
                child: _BuildingSection(status: status),
              ),
            ),

          // Live logs panel
          SliverToBoxAdapter(
            child: Padding(
              padding:
                  const EdgeInsets.symmetric(horizontal: 16, vertical: 6),
              child: const NexusLogsPanel(),
            ),
          ),

          const SliverToBoxAdapter(child: SizedBox(height: 24)),
        ],
      ),
    );
  }

  void _showAutoUpdateModal(
      BuildContext context, NexusController ctrl, NexusStatus status) {
    showDialog(
      context: context,
      builder: (_) => _AutoUpdateModal(status: status, ctrl: ctrl),
    );
  }

  void _showRebuildModal(
      BuildContext context, NexusController ctrl, NexusStatus status) {
    showDialog(
      context: context,
      builder: (_) => _RebuildModal(status: status, ctrl: ctrl),
    );
  }

  void _showDeleteModal(
      BuildContext context, NexusController ctrl, NexusStatus status) {
    showDialog(
      context: context,
      builder: (_) => _DeleteModal(status: status, ctrl: ctrl),
    );
  }
}

// ── Header ────────────────────────────────────────────────────────────────────

class _NexusHeader extends ConsumerWidget {
  const _NexusHeader({required this.state});
  final NexusState state;

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final ctrl = ref.read(nexusControllerProvider.notifier);
    final status = state.status;

    String pillLabel;
    Color pillColor;
    if (status.isBuilding) {
      pillLabel = 'Building';
      pillColor = AppColors.info;
    } else if (status.showBuilt) {
      pillLabel = 'Ready';
      pillColor = AppColors.success;
    } else {
      pillLabel = 'Idle';
      pillColor = AppColors.textFaint;
    }

    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Row(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Expanded(
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Text('Nexus Graph', style: AppTextStyles.h1),
                  const SizedBox(height: 4),
                  Text(
                    'Knowledge graph builder — S&P 500 company relationships.',
                    style:
                        AppTextStyles.meta.copyWith(color: AppColors.textDim),
                  ),
                ],
              ),
            ),
            const SizedBox(width: 12),
            StatusPill(
              label: pillLabel,
              color: pillColor,
              pulsing: status.isBuilding,
            ),
          ],
        ),
        const SizedBox(height: 12),
        // Button row
        Wrap(
          spacing: 8,
          runSpacing: 8,
          children: [
            // Start / Re-run
            if (!status.isBuilding)
              AppButton.semantic(
                label: status.showBuilt && status.serviceRunning
                    ? 'Re-run'
                    : 'Start',
                icon: symbol('play_arrow'),
                color: AppColors.success,
                busy: state.busy,
                onPressed: () {
                  _showStartModal(context, ctrl, status);
                },
              ),
            // Stop
            if (status.serviceRunning)
              AppButton.semantic(
                label: 'Stop',
                icon: symbol('stop'),
                color: AppColors.danger,
                busy: state.busy,
                onPressed: () => ctrl.postControl({'running': false}),
              ),
            // Auto-update
            AppButton.ghost(
              label: 'Auto-update',
              icon: symbol('schedule'),
              onPressed: () {
                _showAutoUpdateModal(context, ctrl, status);
              },
            ),
            // Full Rebuild
            AppButton.ghost(
              label: 'Full Rebuild',
              icon: symbol('reset_wrench'),
              onPressed: state.busy
                  ? null
                  : () => _showRebuildModal(context, ctrl, status),
            ),
            // Delete edges
            AppButton.ghost(
              label: 'Delete edges',
              icon: symbol('delete_sweep'),
              onPressed:
                  (state.busy || status.serviceRunning || status.control.rebuildOperationActive)
                      ? null
                      : () => _showDeleteModal(context, ctrl, status),
            ),
          ],
        ),
      ],
    );
  }

  void _showStartModal(
      BuildContext context, NexusController ctrl, NexusStatus status) {
    showDialog(
      context: context,
      builder: (_) => _StartModal(status: status, ctrl: ctrl),
    );
  }

  void _showAutoUpdateModal(
      BuildContext context, NexusController ctrl, NexusStatus status) {
    showDialog(
      context: context,
      builder: (_) => _AutoUpdateModal(status: status, ctrl: ctrl),
    );
  }

  void _showRebuildModal(
      BuildContext context, NexusController ctrl, NexusStatus status) {
    showDialog(
      context: context,
      builder: (_) => _RebuildModal(status: status, ctrl: ctrl),
    );
  }

  void _showDeleteModal(
      BuildContext context, NexusController ctrl, NexusStatus status) {
    showDialog(
      context: context,
      builder: (_) => _DeleteModal(status: status, ctrl: ctrl),
    );
  }
}

// ── Auto-update summary card ──────────────────────────────────────────────────

class _AutoUpdateCard extends StatelessWidget {
  const _AutoUpdateCard({required this.status, required this.onConfigure});
  final NexusStatus status;
  final VoidCallback onConfigure;

  @override
  Widget build(BuildContext context) {
    final c = status.control;
    final summary = _autoUpdateSummary(c);

    final startLabel = c.autoUpdateStartPhaseLabel ??
        (c.phaseOptions.isNotEmpty
            ? c.phaseOptions
                .firstWhere((p) => p.value == c.autoUpdateStartPhase,
                    orElse: () => const NexusPhaseOption(
                        value: 3, label: 'Phase 2b: SEC sector/industry'))
                .label
            : 'Phase 2b: SEC sector/industry');
    final endLabel = c.autoUpdateEndPhaseLabel ??
        (c.phaseOptions.isNotEmpty
            ? c.phaseOptions
                .firstWhere((p) => p.value == c.autoUpdateEndPhase,
                    orElse: () => const NexusPhaseOption(
                        value: 14, label: 'Phase 12: ETF universe'))
                .label
            : 'Phase 12: ETF universe');

    return GlassCard(
      borderColor: AppColors.border,
      child: Row(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Expanded(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Text('Auto-update',
                    style: AppTextStyles.eyebrow
                        .copyWith(color: AppColors.textDim)),
                const SizedBox(height: 4),
                Text(summary,
                    style:
                        AppTextStyles.cardTitle.copyWith(color: AppColors.textMd)),
                const SizedBox(height: 4),
                Text(
                  'Next: ${c.nextAutoUpdateAt != null ? fmtDateTime(c.nextAutoUpdateAt) : 'not scheduled'}',
                  style:
                      AppTextStyles.meta.copyWith(color: AppColors.textDim),
                ),
                Text(
                  'Range: $startLabel → $endLabel',
                  style:
                      AppTextStyles.nano.copyWith(color: AppColors.textFaint),
                ),
                Text(
                  '13F history: ${c.phase7HistoryQuarters} quarter${c.phase7HistoryQuarters != 1 ? 's' : ''}',
                  style:
                      AppTextStyles.nano.copyWith(color: AppColors.textFaint),
                ),
              ],
            ),
          ),
          const SizedBox(width: 12),
          AppButton.ghost(
              label: 'Configure',
              icon: symbol('tune'),
              onPressed: onConfigure),
        ],
      ),
    );
  }
}

// ── Bootstrap card ────────────────────────────────────────────────────────────

class _BootstrapCard extends StatelessWidget {
  const _BootstrapCard({required this.status});
  final NexusStatus status;

  String get _coverageSummary {
    final b = status.bootstrap;
    final c = status.control;
    if (b == null) return 'Disabled';
    if (b.startDate != null && b.coverageEnd != null) {
      return '${b.startDate} → ${b.coverageEnd}';
    }
    if (c.historicalStartDate != null) return 'From ${c.historicalStartDate}';
    return 'Disabled';
  }

  Color get _cardBorderColor {
    final b = status.bootstrap;
    if (b == null) return AppColors.border;
    if (b.status == 'completed') return AppColors.warning.withValues(alpha: 0.15);
    if (b.status == 'running') return AppColors.info.withValues(alpha: 0.15);
    return AppColors.warning.withValues(alpha: 0.15);
  }

  @override
  Widget build(BuildContext context) {
    final b = status.bootstrap;
    final s = b?.status ?? 'disabled';

    return GlassCard(
      borderColor: _cardBorderColor,
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Row(
            children: [
              Icon(symbol('history'),
                  color: AppColors.warning, size: 18),
              const SizedBox(width: 8),
              Expanded(
                child: Text('Historical Bootstrap',
                    style: AppTextStyles.cardTitle
                        .copyWith(color: AppColors.textMd)),
              ),
              AppBadge(
                  label: _statusPillText(s),
                  color: _bootstrapColor(s)),
            ],
          ),
          const SizedBox(height: 6),
          Text(
            _coverageSummary,
            style: AppTextStyles.meta.copyWith(color: AppColors.textDim),
          ),
          if (b != null &&
              b.status == 'completed' &&
              b.completedPhases != null) ...[
            const SizedBox(height: 8),
            _BootstrapStats(bootstrap: b),
          ],
        ],
      ),
    );
  }

  static String _statusPillText(String s) {
    if (s == 'completed') return 'Bootstrap Ready';
    if (s == 'running') return 'Bootstrap Running';
    if (s == 'never_built') return 'Never Built';
    if (s == 'disabled') return 'Disabled';
    if (s == 'partial') return 'Bootstrap Partial';
    return 'Bootstrap Pending';
  }

  static Color _bootstrapColor(String s) {
    if (s == 'completed') return AppColors.success;
    if (s == 'running') return AppColors.info;
    if (s == 'partial') return AppColors.warning;
    return AppColors.textFaint;
  }
}

class _BootstrapStats extends StatelessWidget {
  const _BootstrapStats({required this.bootstrap});
  final NexusBootstrap bootstrap;

  @override
  Widget build(BuildContext context) {
    return GridView.count(
      crossAxisCount: 2,
      shrinkWrap: true,
      physics: const NeverScrollableScrollPhysics(),
      mainAxisSpacing: 8,
      crossAxisSpacing: 8,
      childAspectRatio: 3,
      children: [
        if (bootstrap.startDate != null && bootstrap.coverageEnd != null)
          StatTile(
              label: 'Coverage',
              value: '${bootstrap.startDate} → ${bootstrap.coverageEnd}'),
        if (bootstrap.durationSec != null)
          StatTile(
              label: 'Duration',
              value: _fmtDuration(bootstrap.durationSec)),
        if (bootstrap.completedPhases != null &&
            bootstrap.totalPhases != null)
          StatTile(
              label: 'Phases',
              value:
                  '${bootstrap.completedPhases}/${bootstrap.totalPhases}'),
        if (bootstrap.completedAt != null)
          StatTile(
              label: 'Completed',
              value: fmtRelative(bootstrap.completedAt)),
      ],
    );
  }
}

// ── Graph counts card ──────────────────────────────────────────────────────────

class _GraphCountsCard extends StatelessWidget {
  const _GraphCountsCard({required this.summary});
  final NexusGraphSummary summary;

  @override
  Widget build(BuildContext context) {
    final companies = summary.nodeCounts['companies'];
    final intervals = summary.nodeCounts['edge_intervals'];

    return GlassCard(
      borderColor: AppColors.border,
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Row(
            children: [
              Expanded(
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Text('Graph Counts',
                        style: AppTextStyles.eyebrow
                            .copyWith(color: AppColors.textMuted)),
                    Text('Current Neo4j relationship totals.',
                        style:
                            AppTextStyles.nano.copyWith(color: AppColors.textFaint)),
                  ],
                ),
              ),
              if (companies != null)
                Padding(
                  padding: const EdgeInsets.only(right: 16),
                  child: Column(
                    crossAxisAlignment: CrossAxisAlignment.end,
                    children: [
                      Text('Companies',
                          style: AppTextStyles.nano
                              .copyWith(color: AppColors.textFaint)),
                      Text(
                        _fmtNum(companies),
                        style: AppTextStyles.valueXl
                            .copyWith(color: AppColors.textHi),
                      ),
                    ],
                  ),
                ),
              if (intervals != null)
                Column(
                  crossAxisAlignment: CrossAxisAlignment.end,
                  children: [
                    Text('Intervals',
                        style: AppTextStyles.nano
                            .copyWith(color: AppColors.textFaint)),
                    Text(
                      _fmtNum(intervals),
                      style: AppTextStyles.valueXl
                          .copyWith(color: AppColors.textHi),
                    ),
                  ],
                ),
            ],
          ),
          const SizedBox(height: 12),
          ...summary.relationshipCounts.map((rel) => Padding(
                padding: const EdgeInsets.only(bottom: 8),
                child: Container(
                  padding:
                      const EdgeInsets.symmetric(horizontal: 12, vertical: 10),
                  decoration: BoxDecoration(
                    color: AppColors.surface.withValues(alpha: 0.4),
                    borderRadius: BorderRadius.circular(10),
                    border: Border.all(color: AppColors.border),
                  ),
                  child: Row(
                    children: [
                      Expanded(
                        child: Column(
                          crossAxisAlignment: CrossAxisAlignment.start,
                          children: [
                            Text(rel.label,
                                style: AppTextStyles.meta
                                    .copyWith(color: AppColors.textMd)),
                            Text(rel.key,
                                style: AppTextStyles.nano
                                    .copyWith(color: AppColors.textFaint,
                                        fontFamily: 'monospace')),
                          ],
                        ),
                      ),
                      Column(
                        crossAxisAlignment: CrossAxisAlignment.end,
                        children: [
                          Text(
                            _fmtNum(rel.activeCount),
                            style: AppTextStyles.valueLg
                                .copyWith(color: AppColors.textHi),
                          ),
                          if (rel.totalCount != null &&
                              rel.totalCount != rel.activeCount)
                            Text('${_fmtNum(rel.totalCount)} total',
                                style: AppTextStyles.nano
                                    .copyWith(color: AppColors.textDim))
                          else
                            Text('active',
                                style: AppTextStyles.nano
                                    .copyWith(color: AppColors.textDim)),
                        ],
                      ),
                    ],
                  ),
                ),
              )),
        ],
      ),
    );
  }

  String _fmtNum(dynamic v) {
    if (v == null) return '—';
    final n = (v as num?)?.toInt();
    if (n == null) return '—';
    if (n >= 1000000) return '${(n / 1000000).toStringAsFixed(1)}M';
    if (n >= 1000) return '${(n / 1000).toStringAsFixed(1)}k';
    return n.toString();
  }
}

// ── Built section ──────────────────────────────────────────────────────────────

class _BuiltSection extends StatelessWidget {
  const _BuiltSection({
    required this.status,
    required this.onRebuild,
    required this.onDeleteEdges,
  });
  final NexusStatus status;
  final VoidCallback onRebuild;
  final VoidCallback onDeleteEdges;

  @override
  Widget build(BuildContext context) {
    final stages = status.graphBuild?.stages ?? [];
    final completedCount =
        stages.where((s) => s.status == 'completed' || s.status == 'skipped').length;
    final totalRuntime = stages.fold<double>(0, (sum, s) {
      final d = s.durationSec;
      return d != null ? sum + d : sum;
    });

    return Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: [
        // Success hero
        GlassCard(
          borderColor: AppColors.success.withValues(alpha: 0.2),
          child: Row(
            children: [
              IconTile(
                  icon: symbol('hub'),
                  color: AppColors.success,
                  size: 44),
              const SizedBox(width: 12),
              Expanded(
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Text('Knowledge Graph is Built',
                        style: AppTextStyles.cardTitle
                            .copyWith(color: AppColors.success)),
                    Text(
                      '$completedCount of ${stages.length} stages completed · Ready for strategy use.',
                      style: AppTextStyles.meta
                          .copyWith(color: AppColors.textDim),
                    ),
                  ],
                ),
              ),
              Column(
                children: [
                  AppButton.ghost(
                      label: 'Rebuild',
                      icon: symbol('reset_wrench'),
                      onPressed: onRebuild),
                  const SizedBox(height: 4),
                  AppButton.ghost(
                      label: 'Delete edges',
                      icon: symbol('delete_sweep'),
                      onPressed: onDeleteEdges),
                ],
              ),
            ],
          ),
        ),
        const SizedBox(height: 12),

        // Stat tiles
        Row(
          children: [
            Expanded(
              child: StatTile(
                  label: 'Stages Done',
                  value: '$completedCount/${stages.length}'),
            ),
            const SizedBox(width: 8),
            if (status.scraper?.index != null)
              Expanded(
                child: StatTile(
                    label: 'SEC Tickers',
                    value:
                        '${status.scraper!.index}${status.scraper?.totalTickers != null ? '/${status.scraper!.totalTickers}' : ''}'),
              ),
            const SizedBox(width: 8),
            if (status.scraper?.edgesCount != null)
              Expanded(
                child: StatTile(
                    label: 'SEC Edges',
                    value: status.scraper!.edgesCount!.toString()),
              ),
            const SizedBox(width: 8),
            if (status.graphBuild?.lastUpdated != null)
              Expanded(
                child: StatTile(
                    label: 'Last Updated',
                    value: fmtRelative(status.graphBuild!.lastUpdated)),
              ),
          ],
        ),
        const SizedBox(height: 12),

        // Stage summary grid
        if (stages.isNotEmpty)
          GlassCard(
            borderColor: AppColors.border,
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Row(
                  mainAxisAlignment: MainAxisAlignment.spaceBetween,
                  children: [
                    Column(
                      crossAxisAlignment: CrossAxisAlignment.start,
                      children: [
                        Text('Stage Summary',
                            style: AppTextStyles.eyebrow
                                .copyWith(color: AppColors.textMuted)),
                        Text('Per-stage runtime from the last completed build.',
                            style: AppTextStyles.nano
                                .copyWith(color: AppColors.textFaint)),
                      ],
                    ),
                    if (totalRuntime > 0)
                      Column(
                        crossAxisAlignment: CrossAxisAlignment.end,
                        children: [
                          Text('Total Runtime',
                              style: AppTextStyles.nano
                                  .copyWith(color: AppColors.textFaint)),
                          Text(
                            _fmtDuration(totalRuntime),
                            style: AppTextStyles.valueLg
                                .copyWith(color: AppColors.textHi),
                          ),
                        ],
                      ),
                  ],
                ),
                const SizedBox(height: 12),
                ...stages.map((stage) {
                  final stColor = _stageColor(stage.status);
                  final cardColor = stage.status == 'completed' ||
                          stage.status == 'skipped'
                      ? AppColors.fill(AppColors.success)
                      : stage.status == 'failed'
                          ? AppColors.fill(AppColors.danger)
                          : stage.status == 'stopped'
                              ? AppColors.fill(AppColors.warning)
                              : AppColors.surface.withValues(alpha: 0.4);
                  return Padding(
                    padding: const EdgeInsets.only(bottom: 6),
                    child: Container(
                      padding: const EdgeInsets.symmetric(
                          horizontal: 12, vertical: 10),
                      decoration: BoxDecoration(
                        color: cardColor,
                        borderRadius: BorderRadius.circular(10),
                        border: Border.all(
                            color: AppColors.stroke(stColor)),
                      ),
                      child: Row(
                        children: [
                          Icon(_stageIcon(stage.status),
                              size: 16, color: stColor),
                          const SizedBox(width: 10),
                          Expanded(
                            child: Column(
                              crossAxisAlignment: CrossAxisAlignment.start,
                              children: [
                                Text(stage.label,
                                    style: AppTextStyles.meta
                                        .copyWith(color: AppColors.textMd)),
                                if (_fmtDuration(stage.durationSec).isNotEmpty)
                                  Text(
                                    _fmtDuration(stage.durationSec),
                                    style: AppTextStyles.nano
                                        .copyWith(color: AppColors.textFaint),
                                  ),
                              ],
                            ),
                          ),
                        ],
                      ),
                    ),
                  );
                }),
              ],
            ),
          ),
      ],
    );
  }
}

// ── Building section ──────────────────────────────────────────────────────────

class _BuildingSection extends StatelessWidget {
  const _BuildingSection({required this.status});
  final NexusStatus status;

  @override
  Widget build(BuildContext context) {
    final build = status.graphBuild;
    final stages = build?.stages ?? [];
    final pct = build?.progressPct ?? 0;

    return Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: [
        // Overall progress card
        GlassCard(
          borderColor: status.isBuilding
              ? AppColors.info.withValues(alpha: 0.2)
              : AppColors.border,
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
                        Text(
                          build?.currentPhaseLabel ??
                              (status.isBuilding
                                  ? 'Building graph…'
                                  : 'Graph not yet built'),
                          style: AppTextStyles.cardTitle
                              .copyWith(color: AppColors.textMd),
                        ),
                        if (build?.message != null) ...[
                          const SizedBox(height: 4),
                          Text(build!.message!,
                              style: AppTextStyles.meta
                                  .copyWith(color: AppColors.textDim)),
                        ],
                      ],
                    ),
                  ),
                  Column(
                    crossAxisAlignment: CrossAxisAlignment.end,
                    children: [
                      Text(
                        '${pct.round()}%',
                        style: AppTextStyles.valueXl
                            .copyWith(color: AppColors.textHi),
                      ),
                      if (build?.etaFormatted != null)
                        Text('~${build!.etaFormatted} remaining',
                            style: AppTextStyles.nano
                                .copyWith(color: AppColors.textDim)),
                    ],
                  ),
                ],
              ),
              const SizedBox(height: 12),
              ClipRRect(
                borderRadius: BorderRadius.circular(4),
                child: LinearProgressIndicator(
                  value: pct / 100,
                  minHeight: 8,
                  backgroundColor: AppColors.surface,
                  valueColor: AlwaysStoppedAnimation(
                    status.isBuilding ? AppColors.info : AppColors.textFaint,
                  ),
                ),
              ),
            ],
          ),
        ),
        const SizedBox(height: 12),

        // Vertical stepper of build stages
        GlassCard(
          borderColor: AppColors.border,
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Text('Build Stages',
                  style: AppTextStyles.eyebrow
                      .copyWith(color: AppColors.textMuted)),
              const SizedBox(height: 16),
              if (stages.isEmpty)
                Center(
                  child: Column(
                    children: [
                      Icon(symbol('hub'),
                          size: 40, color: AppColors.textFaint),
                      const SizedBox(height: 12),
                      Text('No stage data yet.',
                          style: AppTextStyles.meta
                              .copyWith(color: AppColors.textDim)),
                      Text('Start the Nexus engine to begin building.',
                          style: AppTextStyles.nano
                              .copyWith(color: AppColors.textFaint)),
                    ],
                  ),
                )
              else
                ...List.generate(stages.length, (i) {
                  final stage = stages[i];
                  final isLast = i == stages.length - 1;
                  return _BuildStageRow(
                      stage: stage, isLast: isLast);
                }),
            ],
          ),
        ),
      ],
    );
  }
}

class _BuildStageRow extends StatelessWidget {
  const _BuildStageRow({required this.stage, required this.isLast});
  final NexusStage stage;
  final bool isLast;

  Color get _lineColor {
    if (stage.status == 'completed' || stage.status == 'skipped') {
      return AppColors.success.withValues(alpha: 0.4);
    }
    if (stage.status == 'running') return AppColors.info.withValues(alpha: 0.5);
    if (stage.status == 'stopped') return AppColors.warning.withValues(alpha: 0.4);
    return AppColors.border;
  }

  Color get _labelColor {
    switch (stage.status.toLowerCase()) {
      case 'running':
        return AppColors.info;
      case 'stopped':
        return AppColors.warning;
      case 'completed':
      case 'skipped':
        return AppColors.textMd;
      case 'failed':
        return AppColors.danger;
      default:
        return AppColors.textFaint;
    }
  }

  @override
  Widget build(BuildContext context) {
    final spinning = _stageSpinning(stage.status);
    final iconColor = _stageColor(stage.status);

    return Row(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        SizedBox(
          width: 32,
          child: Column(
            children: [
              Container(
                width: 32,
                height: 32,
                decoration: BoxDecoration(
                  color: AppColors.fill(iconColor),
                  shape: BoxShape.circle,
                  border: Border.all(color: AppColors.stroke(iconColor)),
                ),
                child: spinning
                    ? _SpinningIcon(color: iconColor)
                    : Icon(_stageIcon(stage.status),
                        size: 15, color: iconColor),
              ),
              if (!isLast)
                Container(
                  width: 1,
                  height: 40,
                  color: _lineColor,
                ),
            ],
          ),
        ),
        const SizedBox(width: 12),
        Expanded(
          child: Padding(
            padding: EdgeInsets.only(bottom: isLast ? 0 : 12),
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Row(
                  children: [
                    Expanded(
                      child: Text(stage.label,
                          style: AppTextStyles.bodyHi
                              .copyWith(color: _labelColor)),
                    ),
                    if (_fmtDuration(stage.durationSec).isNotEmpty)
                      Text(
                        _fmtDuration(stage.durationSec),
                        style: AppTextStyles.nano.copyWith(
                            color: AppColors.textFaint,
                            fontFamily: 'monospace'),
                      )
                    else if (stage.status == 'running')
                      Text('Running…',
                          style: AppTextStyles.nano
                              .copyWith(
                                  color: AppColors.info,
                                  fontStyle: FontStyle.italic))
                    else if (stage.status == 'pending')
                      Text('pending',
                          style: AppTextStyles.nano
                              .copyWith(color: AppColors.textFaint)),
                  ],
                ),
                if (stage.message != null) ...[
                  const SizedBox(height: 2),
                  Text(stage.message!,
                      style: AppTextStyles.nano
                          .copyWith(color: AppColors.textDim)),
                ],
                // Substep bar
                if (stage.status == 'running' &&
                    (stage.totalSubsteps ?? 0) > 1) ...[
                  const SizedBox(height: 4),
                  Row(
                    children: [
                      SizedBox(
                        width: 80,
                        height: 4,
                        child: ClipRRect(
                          borderRadius: BorderRadius.circular(2),
                          child: LinearProgressIndicator(
                            value: stage.substepFraction,
                            backgroundColor:
                                AppColors.surface,
                            valueColor: const AlwaysStoppedAnimation(
                                AppColors.info),
                          ),
                        ),
                      ),
                      const SizedBox(width: 6),
                      Text(
                        '${stage.substepsCompleted ?? 0}/${stage.totalSubsteps} substeps',
                        style: AppTextStyles.nano.copyWith(
                            color: AppColors.info.withValues(alpha: 0.8),
                            fontFamily: 'monospace'),
                      ),
                    ],
                  ),
                ],
              ],
            ),
          ),
        ),
      ],
    );
  }
}

class _SpinningIcon extends StatefulWidget {
  const _SpinningIcon({required this.color});
  final Color color;

  @override
  State<_SpinningIcon> createState() => _SpinningIconState();
}

class _SpinningIconState extends State<_SpinningIcon>
    with SingleTickerProviderStateMixin {
  late final AnimationController _ctrl = AnimationController(
    vsync: this,
    duration: const Duration(milliseconds: 1000),
  )..repeat();

  @override
  void dispose() {
    _ctrl.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    return RotationTransition(
      turns: _ctrl,
      child: Icon(symbol('progress_activity'),
          size: 15, color: widget.color),
    );
  }
}

// ── Start Nexus modal ──────────────────────────────────────────────────────────

class _StartModal extends StatefulWidget {
  const _StartModal({required this.status, required this.ctrl});
  final NexusStatus status;
  final NexusController ctrl;

  @override
  State<_StartModal> createState() => _StartModalState();
}

class _StartModalState extends State<_StartModal> {
  late List<int> _selectedPhases;
  int _historyQuarters = 1;
  bool _historicalMode = false;
  String _historicalStartDate = '';
  bool _forceBootstrapRebuild = false;
  bool _busy = false;

  List<NexusPhaseOption> get _phaseOptions =>
      widget.status.control.phaseOptions.isNotEmpty
          ? widget.status.control.phaseOptions
          : kFallbackPhaseOptions;

  @override
  void initState() {
    super.initState();
    final ctrl = widget.status.control;
    _selectedPhases = ctrl.selectedPhases.isNotEmpty
        ? [...ctrl.selectedPhases]
        : _phaseOptions.map((p) => p.value).toList();
    _historyQuarters = ctrl.phase7HistoryQuarters;
    _historicalMode = ctrl.historicalModeEnabled;
    _historicalStartDate = ctrl.historicalStartDate ?? '';
  }

  Future<void> _submit() async {
    if (_selectedPhases.isEmpty) return;
    if (_historicalMode && _historicalStartDate.isEmpty) return;
    setState(() => _busy = true);
    Navigator.pop(context);
    final sorted = [..._selectedPhases]..sort();
    await widget.ctrl.postControl({
      'running': true,
      'phase7_history_quarters': _historyQuarters,
      'historical_mode_enabled': _historicalMode,
      if (_historicalMode && _historicalStartDate.isNotEmpty)
        'historical_start_date': _historicalStartDate,
      if (_forceBootstrapRebuild) 'force_bootstrap_rebuild': true,
      'selected_phases': sorted,
    });
  }

  @override
  Widget build(BuildContext context) {
    final title = widget.status.showBuilt &&
            widget.status.serviceRunning
        ? 'Re-run Nexus'
        : 'Start Nexus';

    return Dialog(
      backgroundColor: AppColors.panel,
      shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(20)),
      insetPadding: const EdgeInsets.all(16),
      child: ConstrainedBox(
        constraints:
            BoxConstraints(maxHeight: MediaQuery.of(context).size.height * 0.85),
        child: Column(
          mainAxisSize: MainAxisSize.min,
          children: [
            // Header
            Padding(
              padding: const EdgeInsets.fromLTRB(20, 20, 20, 0),
              child: Row(
                children: [
                  IconTile(
                      icon: symbol('play_arrow'),
                      color: AppColors.success),
                  const SizedBox(width: 12),
                  Expanded(
                    child: Column(
                      crossAxisAlignment: CrossAxisAlignment.start,
                      children: [
                        Text(title, style: AppTextStyles.cardTitle),
                        Text('Choose phases for this manual execution.',
                            style: AppTextStyles.nano
                                .copyWith(color: AppColors.textDim)),
                      ],
                    ),
                  ),
                ],
              ),
            ),
            const Divider(height: 20, color: AppColors.border),
            // Scrollable body
            Flexible(
              child: SingleChildScrollView(
                padding: const EdgeInsets.symmetric(horizontal: 20),
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    // Phase list
                    Row(
                      mainAxisAlignment: MainAxisAlignment.spaceBetween,
                      children: [
                        Text('Phase Selection',
                            style: AppTextStyles.eyebrow
                                .copyWith(color: AppColors.textDim)),
                        Row(
                          children: [
                            TextButton(
                              onPressed: () => setState(() =>
                                  _selectedPhases =
                                      _phaseOptions.map((p) => p.value).toList()),
                              child: Text('All',
                                  style: AppTextStyles.meta
                                      .copyWith(color: AppColors.textMuted)),
                            ),
                            TextButton(
                              onPressed: () =>
                                  setState(() => _selectedPhases = []),
                              child: Text('None',
                                  style: AppTextStyles.meta
                                      .copyWith(color: AppColors.textMuted)),
                            ),
                          ],
                        ),
                      ],
                    ),
                    ..._phaseOptions.map((opt) => CheckboxListTile(
                          value: _selectedPhases.contains(opt.value),
                          onChanged: (v) {
                            setState(() {
                              if (v == true) {
                                _selectedPhases.add(opt.value);
                              } else {
                                _selectedPhases.remove(opt.value);
                              }
                            });
                          },
                          title: Text(opt.label,
                              style: AppTextStyles.meta
                                  .copyWith(color: AppColors.textMd)),
                          activeThumbColor: AppColors.success,
                          checkColor: AppColors.onPrimary,
                          dense: true,
                          contentPadding: EdgeInsets.zero,
                        )),
                    if (_selectedPhases.isEmpty)
                      Text('Select at least one phase.',
                          style: AppTextStyles.nano
                              .copyWith(color: AppColors.danger)),
                    const SizedBox(height: 12),
                    // 13F quarters
                    Text('13F History (quarters)',
                        style: AppTextStyles.eyebrow
                            .copyWith(color: AppColors.textDim)),
                    const SizedBox(height: 6),
                    TextFormField(
                      initialValue: _historyQuarters.toString(),
                      keyboardType: TextInputType.number,
                      style: AppTextStyles.body.copyWith(color: AppColors.textHi),
                      decoration: _inputDecoration('quarters'),
                      onChanged: (v) =>
                          _historyQuarters = int.tryParse(v) ?? 1,
                    ),
                    const SizedBox(height: 12),
                    // Historical bootstrap toggle
                    GlassCard(
                      borderColor: AppColors.success.withValues(alpha: 0.15),
                      child: Column(
                        children: [
                          Row(
                            children: [
                              Expanded(
                                child: Column(
                                  crossAxisAlignment: CrossAxisAlignment.start,
                                  children: [
                                    Text('Historical bootstrap',
                                        style: AppTextStyles.bodyHi
                                            .copyWith(color: AppColors.textMd)),
                                    Text(
                                        'Backfill temporal phases from a start date.',
                                        style: AppTextStyles.nano
                                            .copyWith(color: AppColors.textDim)),
                                  ],
                                ),
                              ),
                              Switch(
                                value: _historicalMode,
                                onChanged: (v) =>
                                    setState(() => _historicalMode = v),
                                activeThumbColor: AppColors.success,
                              ),
                            ],
                          ),
                          if (_historicalMode) ...[
                            const SizedBox(height: 8),
                            TextFormField(
                              initialValue: _historicalStartDate,
                              style: AppTextStyles.body
                                  .copyWith(color: AppColors.textHi),
                              decoration:
                                  _inputDecoration('YYYY-MM-DD', hint: 'Start date'),
                              onChanged: (v) => _historicalStartDate = v,
                            ),
                          ],
                          const SizedBox(height: 8),
                          Row(
                            children: [
                              Expanded(
                                child: Column(
                                  crossAxisAlignment: CrossAxisAlignment.start,
                                  children: [
                                    Text('Force bootstrap rebuild',
                                        style: AppTextStyles.bodyHi
                                            .copyWith(color: AppColors.textMd)),
                                    Text(
                                        'Reset historical bootstrap and rebuild from start date.',
                                        style: AppTextStyles.nano
                                            .copyWith(color: AppColors.textDim)),
                                  ],
                                ),
                              ),
                              Switch(
                                value: _forceBootstrapRebuild,
                                onChanged: (v) =>
                                    setState(() => _forceBootstrapRebuild = v),
                                activeThumbColor: AppColors.success,
                              ),
                            ],
                          ),
                        ],
                      ),
                    ),
                    const SizedBox(height: 16),
                  ],
                ),
              ),
            ),
            const Divider(height: 1, color: AppColors.border),
            Padding(
              padding: const EdgeInsets.all(16),
              child: Row(
                mainAxisAlignment: MainAxisAlignment.end,
                children: [
                  AppButton.ghost(
                      label: 'Cancel',
                      onPressed: () => Navigator.pop(context)),
                  const SizedBox(width: 8),
                  AppButton.semantic(
                    label: title,
                    icon: symbol('play_arrow'),
                    color: AppColors.success,
                    busy: _busy,
                    onPressed: _selectedPhases.isNotEmpty ? _submit : null,
                  ),
                ],
              ),
            ),
          ],
        ),
      ),
    );
  }

  InputDecoration _inputDecoration(String label, {String? hint}) {
    return InputDecoration(
      labelText: label,
      hintText: hint,
      labelStyle: AppTextStyles.meta.copyWith(color: AppColors.textMuted),
      hintStyle: AppTextStyles.body.copyWith(color: AppColors.textFaint),
      filled: true,
      fillColor: AppColors.surface,
      isDense: true,
      border: OutlineInputBorder(
          borderRadius: BorderRadius.circular(8),
          borderSide: BorderSide(color: AppColors.border)),
      enabledBorder: OutlineInputBorder(
          borderRadius: BorderRadius.circular(8),
          borderSide: BorderSide(color: AppColors.border)),
      focusedBorder: OutlineInputBorder(
          borderRadius: BorderRadius.circular(8),
          borderSide:
              BorderSide(color: AppColors.success.withValues(alpha: 0.4))),
    );
  }
}

// ── Auto-update modal ─────────────────────────────────────────────────────────

class _AutoUpdateModal extends StatefulWidget {
  const _AutoUpdateModal({required this.status, required this.ctrl});
  final NexusStatus status;
  final NexusController ctrl;

  @override
  State<_AutoUpdateModal> createState() => _AutoUpdateModalState();
}

class _AutoUpdateModalState extends State<_AutoUpdateModal> {
  late bool _enabled;
  late int _intervalHours;
  late int _startPhase;
  late int _endPhase;
  bool _busy = false;

  List<NexusPhaseOption> get _phaseOptions =>
      widget.status.control.phaseOptions.isNotEmpty
          ? widget.status.control.phaseOptions
          : kFallbackPhaseOptions;

  @override
  void initState() {
    super.initState();
    final c = widget.status.control;
    _enabled = c.autoUpdateEnabled;
    _intervalHours = c.autoUpdateIntervalHours;
    _startPhase = c.autoUpdateStartPhase;
    _endPhase = c.autoUpdateEndPhase;
  }

  Future<void> _save() async {
    setState(() => _busy = true);
    Navigator.pop(context);
    await widget.ctrl.postControl({
      'auto_update_enabled': _enabled,
      'auto_update_interval_hours': _intervalHours,
      'auto_update_start_phase': _startPhase,
      'auto_update_end_phase': _endPhase,
      if (_enabled) 'running': true,
    });
  }

  @override
  Widget build(BuildContext context) {
    return Dialog(
      backgroundColor: AppColors.panel,
      shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(20)),
      insetPadding: const EdgeInsets.all(16),
      child: Padding(
        padding: const EdgeInsets.all(24),
        child: Column(
          mainAxisSize: MainAxisSize.min,
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Row(children: [
              IconTile(icon: symbol('schedule'), color: AppColors.info),
              const SizedBox(width: 12),
              Expanded(
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Text('Nexus Auto-update',
                        style: AppTextStyles.cardTitle),
                    Text(
                        'Keep Nexus online and rerun on a schedule.',
                        style: AppTextStyles.nano
                            .copyWith(color: AppColors.textDim)),
                  ],
                ),
              ),
            ]),
            const SizedBox(height: 20),
            Row(
              mainAxisAlignment: MainAxisAlignment.spaceBetween,
              children: [
                Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Text('Enable auto-update',
                        style: AppTextStyles.bodyHi
                            .copyWith(color: AppColors.textMd)),
                    Text('Nexus will queue the next refresh automatically.',
                        style: AppTextStyles.nano
                            .copyWith(color: AppColors.textDim)),
                  ],
                ),
                Switch(
                  value: _enabled,
                  onChanged: (v) => setState(() => _enabled = v),
                  activeThumbColor: AppColors.info,
                ),
              ],
            ),
            const SizedBox(height: 12),
            Text('Interval (hours)',
                style: AppTextStyles.eyebrow
                    .copyWith(color: AppColors.textDim)),
            const SizedBox(height: 6),
            TextFormField(
              initialValue: _intervalHours.toString(),
              keyboardType: TextInputType.number,
              style: AppTextStyles.body.copyWith(color: AppColors.textHi),
              decoration: _inputDecoration('hours'),
              onChanged: (v) =>
                  _intervalHours = int.tryParse(v) ?? 168,
            ),
            const SizedBox(height: 12),
            Text('From phase',
                style: AppTextStyles.eyebrow
                    .copyWith(color: AppColors.textDim)),
            const SizedBox(height: 6),
            _PhaseDropdown(
              value: _startPhase,
              options: _phaseOptions,
              onChanged: (v) => setState(() => _startPhase = v),
            ),
            const SizedBox(height: 12),
            Text('To phase',
                style: AppTextStyles.eyebrow
                    .copyWith(color: AppColors.textDim)),
            const SizedBox(height: 6),
            _PhaseDropdown(
              value: _endPhase,
              options: _phaseOptions,
              onChanged: (v) => setState(() => _endPhase = v),
            ),
            const SizedBox(height: 20),
            Row(
              mainAxisAlignment: MainAxisAlignment.end,
              children: [
                AppButton.ghost(
                    label: 'Cancel',
                    onPressed: () => Navigator.pop(context)),
                const SizedBox(width: 8),
                AppButton.semantic(
                  label: 'Save Schedule',
                  icon: symbol('save'),
                  color: AppColors.info,
                  busy: _busy,
                  onPressed: _save,
                ),
              ],
            ),
          ],
        ),
      ),
    );
  }

  InputDecoration _inputDecoration(String label) {
    return InputDecoration(
      labelText: label,
      labelStyle: AppTextStyles.meta.copyWith(color: AppColors.textMuted),
      filled: true,
      fillColor: AppColors.surface,
      isDense: true,
      border: OutlineInputBorder(
          borderRadius: BorderRadius.circular(8),
          borderSide: BorderSide(color: AppColors.border)),
      enabledBorder: OutlineInputBorder(
          borderRadius: BorderRadius.circular(8),
          borderSide: BorderSide(color: AppColors.border)),
      focusedBorder: OutlineInputBorder(
          borderRadius: BorderRadius.circular(8),
          borderSide:
              BorderSide(color: AppColors.info.withValues(alpha: 0.4))),
    );
  }
}

class _PhaseDropdown extends StatelessWidget {
  const _PhaseDropdown({
    required this.value,
    required this.options,
    required this.onChanged,
  });
  final int value;
  final List<NexusPhaseOption> options;
  final void Function(int) onChanged;

  @override
  Widget build(BuildContext context) {
    // Ensure value is in list; fallback to first
    final safeValue =
        options.any((o) => o.value == value) ? value : options.first.value;

    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 4),
      decoration: BoxDecoration(
        color: AppColors.surface,
        borderRadius: BorderRadius.circular(8),
        border: Border.all(color: AppColors.border),
      ),
      child: DropdownButtonHideUnderline(
        child: DropdownButton<int>(
          value: safeValue,
          isDense: true,
          isExpanded: true,
          dropdownColor: AppColors.surface,
          style: AppTextStyles.meta.copyWith(color: AppColors.textMd),
          items: options
              .map((o) => DropdownMenuItem(
                    value: o.value,
                    child: Text(o.label,
                        style: AppTextStyles.meta
                            .copyWith(color: AppColors.textMd)),
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

// ── Full Rebuild modal ─────────────────────────────────────────────────────────

class _RebuildModal extends StatefulWidget {
  const _RebuildModal({required this.status, required this.ctrl});
  final NexusStatus status;
  final NexusController ctrl;

  @override
  State<_RebuildModal> createState() => _RebuildModalState();
}

class _RebuildModalState extends State<_RebuildModal> {
  bool _destructive = false;
  bool _forceBootstrap = false;
  bool _confirmMatch = false;
  NexusCacheInfo? _cacheInfo;
  bool _cacheLoading = false;
  List<String> _selectedCachePaths = [];
  bool _busy = false;

  @override
  void initState() {
    super.initState();
    _loadCache();
  }

  Future<void> _loadCache() async {
    setState(() => _cacheLoading = true);
    _cacheInfo = await widget.ctrl.fetchCache();
    if (mounted) setState(() => _cacheLoading = false);
  }

  Future<void> _submit() async {
    if (!_confirmMatch) return;
    setState(() => _busy = true);
    Navigator.pop(context);
    await widget.ctrl.rebuild({
      'confirm': true,
      'destructive': _destructive,
      'force_bootstrap_rebuild': _forceBootstrap,
      'delete_cache_paths': _selectedCachePaths,
    });
  }

  @override
  Widget build(BuildContext context) {
    return Dialog(
      backgroundColor: AppColors.panel,
      shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(20)),
      insetPadding: const EdgeInsets.all(16),
      child: ConstrainedBox(
        constraints:
            BoxConstraints(maxHeight: MediaQuery.of(context).size.height * 0.85),
        child: Column(
          mainAxisSize: MainAxisSize.min,
          children: [
            Padding(
              padding: const EdgeInsets.fromLTRB(20, 20, 20, 0),
              child: Row(
                children: [
                  IconTile(icon: symbol('reset_wrench'), color: AppColors.danger),
                  const SizedBox(width: 12),
                  Expanded(
                    child: Column(
                      crossAxisAlignment: CrossAxisAlignment.start,
                      children: [
                        Text('Full Rebuild', style: AppTextStyles.cardTitle),
                        Text(
                          _destructive
                              ? 'Clears Neo4j graph data and resets Nexus progress.'
                              : 'Reruns from phase 1, keeps existing graph online.',
                          style: AppTextStyles.nano
                              .copyWith(color: AppColors.danger.withValues(alpha: 0.8)),
                        ),
                      ],
                    ),
                  ),
                ],
              ),
            ),
            const Divider(height: 20, color: AppColors.border),
            Flexible(
              child: SingleChildScrollView(
                padding: const EdgeInsets.symmetric(horizontal: 20),
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    // Rebuild mode radio
                    Text('Rebuild mode',
                        style: AppTextStyles.eyebrow
                            .copyWith(color: AppColors.textDim)),
                    const SizedBox(height: 8),
                    _ModeCard(
                      selected: !_destructive,
                      color: AppColors.success,
                      title: 'In-place rebuild',
                      description:
                          'Recommended. Keeps current graph data online while Nexus reruns.',
                      onTap: () => setState(() => _destructive = false),
                    ),
                    const SizedBox(height: 8),
                    _ModeCard(
                      selected: _destructive,
                      color: AppColors.danger,
                      title: 'Destructive rebuild',
                      description:
                          'Clears Neo4j graph data and resets Nexus progress before phase 1.',
                      onTap: () => setState(() => _destructive = true),
                    ),
                    const SizedBox(height: 12),
                    // Force bootstrap rebuild
                    Row(
                      children: [
                        Expanded(
                          child: Column(
                            crossAxisAlignment: CrossAxisAlignment.start,
                            children: [
                              Text('Force bootstrap rebuild',
                                  style: AppTextStyles.bodyHi
                                      .copyWith(color: AppColors.textMd)),
                              Text(
                                  'Clear historical bootstrap checkpoints so temporal phases rebuild from scratch.',
                                  style: AppTextStyles.nano
                                      .copyWith(color: AppColors.textDim)),
                            ],
                          ),
                        ),
                        Switch(
                          value: _forceBootstrap,
                          onChanged: (v) =>
                              setState(() => _forceBootstrap = v),
                          activeThumbColor: AppColors.danger,
                        ),
                      ],
                    ),
                    const SizedBox(height: 12),
                    // Cache cleanup
                    Text('Cache cleanup (optional)',
                        style: AppTextStyles.eyebrow
                            .copyWith(color: AppColors.textDim)),
                    const SizedBox(height: 6),
                    if (_cacheLoading)
                      const Padding(
                        padding: EdgeInsets.symmetric(vertical: 8),
                        child: LoadingState(label: 'Loading cache…'),
                      )
                    else if (_cacheInfo?.available == true &&
                        _cacheInfo!.entries.isNotEmpty) ...[
                      Row(
                        mainAxisAlignment: MainAxisAlignment.end,
                        children: [
                          TextButton(
                            onPressed: () => setState(() =>
                                _selectedCachePaths =
                                    _cacheInfo!.entries.map((e) => e.path).toList()),
                            child: Text('Select all',
                                style: AppTextStyles.meta
                                    .copyWith(color: AppColors.textMuted)),
                          ),
                          TextButton(
                            onPressed: () =>
                                setState(() => _selectedCachePaths = []),
                            child: Text('Clear',
                                style: AppTextStyles.meta
                                    .copyWith(color: AppColors.textMuted)),
                          ),
                        ],
                      ),
                      ..._cacheInfo!.entries.map((entry) => CheckboxListTile(
                            value: _selectedCachePaths.contains(entry.path),
                            onChanged: (v) {
                              setState(() {
                                if (v == true) {
                                  _selectedCachePaths.add(entry.path);
                                } else {
                                  _selectedCachePaths.remove(entry.path);
                                }
                              });
                            },
                            title: Text(entry.path,
                                style: AppTextStyles.meta
                                    .copyWith(
                                        color: AppColors.textMd,
                                        fontFamily: 'monospace')),
                            subtitle: entry.sizeBytes != null
                                ? Text('${entry.sizeBytes} bytes',
                                    style: AppTextStyles.nano
                                        .copyWith(color: AppColors.textFaint))
                                : null,
                            secondary: Icon(
                                entry.isDir
                                    ? symbol('folder')
                                    : symbol('description'),
                                color: entry.isDir
                                    ? AppColors.warning
                                    : AppColors.info,
                                size: 18),
                            activeThumbColor: AppColors.danger,
                            dense: true,
                            contentPadding: EdgeInsets.zero,
                          )),
                    ] else
                      Text(
                        _cacheInfo == null
                            ? 'Cache not accessible from this context.'
                            : 'No cache entries found.',
                        style: AppTextStyles.meta
                            .copyWith(color: AppColors.textDim),
                      ),
                    const SizedBox(height: 12),
                    // Typed confirm
                    Text('Type confirm to proceed',
                        style: AppTextStyles.eyebrow
                            .copyWith(color: AppColors.textDim)),
                    const SizedBox(height: 6),
                    TypedConfirmField(
                      phrase: 'confirm',
                      label: 'confirm',
                      onMatchChanged: (v) =>
                          setState(() => _confirmMatch = v),
                    ),
                    const SizedBox(height: 16),
                  ],
                ),
              ),
            ),
            const Divider(height: 1, color: AppColors.border),
            Padding(
              padding: const EdgeInsets.all(16),
              child: Row(
                mainAxisAlignment: MainAxisAlignment.end,
                children: [
                  AppButton.ghost(
                      label: 'Cancel',
                      onPressed: () => Navigator.pop(context)),
                  const SizedBox(width: 8),
                  AppButton.semantic(
                    label: _destructive
                        ? 'Confirm Destructive Rebuild'
                        : 'Confirm Rebuild',
                    icon: symbol('restart_alt'),
                    color: _destructive ? AppColors.danger : AppColors.warning,
                    busy: _busy,
                    onPressed: _confirmMatch ? _submit : null,
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

class _ModeCard extends StatelessWidget {
  const _ModeCard({
    required this.selected,
    required this.color,
    required this.title,
    required this.description,
    required this.onTap,
  });
  final bool selected;
  final Color color;
  final String title;
  final String description;
  final VoidCallback onTap;

  @override
  Widget build(BuildContext context) {
    return GestureDetector(
      onTap: onTap,
      child: Container(
        padding: const EdgeInsets.all(14),
        decoration: BoxDecoration(
          color: selected ? AppColors.fill(color) : AppColors.surface,
          borderRadius: BorderRadius.circular(12),
          border: Border.all(
              color: selected ? AppColors.stroke(color) : AppColors.border),
        ),
        child: Row(
          children: [
            Radio<bool>(
              value: true,
              groupValue: selected,
              onChanged: (_) => onTap(),
              activeThumbColor: color,
            ),
            const SizedBox(width: 8),
            Expanded(
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Text(title,
                      style:
                          AppTextStyles.bodyHi.copyWith(color: AppColors.textMd)),
                  Text(description,
                      style: AppTextStyles.nano
                          .copyWith(color: AppColors.textDim)),
                ],
              ),
            ),
          ],
        ),
      ),
    );
  }
}

// ── Delete edges modal ────────────────────────────────────────────────────────

class _DeleteModal extends StatefulWidget {
  const _DeleteModal({required this.status, required this.ctrl});
  final NexusStatus status;
  final NexusController ctrl;

  @override
  State<_DeleteModal> createState() => _DeleteModalState();
}

class _DeleteModalState extends State<_DeleteModal> {
  late List<int> _selectedPhases;
  bool _busy = false;

  List<NexusPhaseOption> get _deletePhaseOptions =>
      widget.status.control.deletePhaseOptions.isNotEmpty
          ? widget.status.control.deletePhaseOptions
          : kFallbackDeletePhaseOptions;

  @override
  void initState() {
    super.initState();
    final active = widget.status.control.deleteOperationSelectedPhases;
    _selectedPhases = active.isNotEmpty
        ? [...active]
        : _deletePhaseOptions.map((p) => p.value).toList();
  }

  Future<void> _submit() async {
    if (_selectedPhases.isEmpty) return;
    setState(() => _busy = true);
    Navigator.pop(context);
    final sorted = [..._selectedPhases]..sort();
    await widget.ctrl.deleteEdges({'selected_phases': sorted});
  }

  @override
  Widget build(BuildContext context) {
    final op = widget.status.control;
    final hasProgress = op.deleteOperationActive;

    return Dialog(
      backgroundColor: AppColors.panel,
      shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(20)),
      insetPadding: const EdgeInsets.all(16),
      child: ConstrainedBox(
        constraints:
            BoxConstraints(maxHeight: MediaQuery.of(context).size.height * 0.85),
        child: Column(
          mainAxisSize: MainAxisSize.min,
          children: [
            Padding(
              padding: const EdgeInsets.fromLTRB(20, 20, 20, 0),
              child: Row(
                children: [
                  IconTile(
                      icon: hasProgress
                          ? symbol('progress_activity')
                          : symbol('delete_sweep'),
                      color: AppColors.warning),
                  const SizedBox(width: 12),
                  Expanded(
                    child: Text(
                      hasProgress
                          ? 'Deleting Nexus Edges'
                          : 'Delete Nexus Edges',
                      style: AppTextStyles.cardTitle,
                    ),
                  ),
                  if (!hasProgress && !_busy)
                    GestureDetector(
                      onTap: () => Navigator.pop(context),
                      child: Icon(symbol('close'),
                          size: 18, color: AppColors.textMuted),
                    ),
                ],
              ),
            ),
            const Divider(height: 20, color: AppColors.border),
            Flexible(
              child: SingleChildScrollView(
                padding: const EdgeInsets.symmetric(horizontal: 20),
                child: hasProgress
                    ? _DeleteProgress(ctrl: op)
                    : _DeletePhaseSelection(
                        phases: _deletePhaseOptions,
                        selected: _selectedPhases,
                        onToggle: (v, on) {
                          setState(() {
                            if (on) {
                              _selectedPhases.add(v);
                            } else {
                              _selectedPhases.remove(v);
                            }
                          });
                        },
                        onSelectAll: () => setState(() => _selectedPhases =
                            _deletePhaseOptions.map((p) => p.value).toList()),
                        onClear: () =>
                            setState(() => _selectedPhases = []),
                      ),
              ),
            ),
            const Divider(height: 1, color: AppColors.border),
            Padding(
              padding: const EdgeInsets.all(16),
              child: hasProgress
                  ? AppButton.ghost(
                      label: 'Close',
                      onPressed: () => Navigator.pop(context))
                  : Row(
                      mainAxisAlignment: MainAxisAlignment.end,
                      children: [
                        AppButton.ghost(
                            label: 'Cancel',
                            onPressed: () => Navigator.pop(context)),
                        const SizedBox(width: 8),
                        AppButton.semantic(
                          label: 'Delete selected edges',
                          icon: symbol('delete'),
                          color: AppColors.warning,
                          busy: _busy,
                          onPressed: _selectedPhases.isNotEmpty ? _submit : null,
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

class _DeletePhaseSelection extends StatelessWidget {
  const _DeletePhaseSelection({
    required this.phases,
    required this.selected,
    required this.onToggle,
    required this.onSelectAll,
    required this.onClear,
  });
  final List<NexusPhaseOption> phases;
  final List<int> selected;
  final void Function(int, bool) onToggle;
  final VoidCallback onSelectAll;
  final VoidCallback onClear;

  @override
  Widget build(BuildContext context) {
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Row(
          mainAxisAlignment: MainAxisAlignment.spaceBetween,
          children: [
            Text('Phase Selection',
                style: AppTextStyles.eyebrow
                    .copyWith(color: AppColors.textDim)),
            Row(
              children: [
                TextButton(
                    onPressed: onSelectAll,
                    child: Text('All',
                        style: AppTextStyles.meta
                            .copyWith(color: AppColors.textMuted))),
                TextButton(
                    onPressed: onClear,
                    child: Text('None',
                        style: AppTextStyles.meta
                            .copyWith(color: AppColors.textMuted))),
              ],
            ),
          ],
        ),
        ...phases.map((p) => CheckboxListTile(
              value: selected.contains(p.value),
              onChanged: (v) => onToggle(p.value, v == true),
              title: Text(p.label,
                  style: AppTextStyles.meta.copyWith(color: AppColors.textMd)),
              activeThumbColor: AppColors.warning,
              dense: true,
              contentPadding: EdgeInsets.zero,
            )),
        if (selected.isEmpty)
          Text('Select at least one phase.',
              style: AppTextStyles.nano.copyWith(color: AppColors.danger)),
        const SizedBox(height: 8),
      ],
    );
  }
}

class _DeleteProgress extends StatelessWidget {
  const _DeleteProgress({required this.ctrl});
  final NexusControl ctrl;

  @override
  Widget build(BuildContext context) {
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        GlassCard(
          borderColor: AppColors.border,
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Text('Overall Progress',
                  style: AppTextStyles.eyebrow
                      .copyWith(color: AppColors.textMuted)),
              const SizedBox(height: 4),
              Text(
                '${ctrl.deleteOperationCurrent?.toInt() ?? 0} / ${ctrl.deleteOperationTotal?.toInt() ?? 0} ${ctrl.deleteOperationUnit ?? 'phases'}',
                style: AppTextStyles.valueXl.copyWith(color: AppColors.textHi),
              ),
              if (ctrl.deleteOperationStep != null) ...[
                const SizedBox(height: 4),
                Text(ctrl.deleteOperationStep!,
                    style: AppTextStyles.meta
                        .copyWith(color: AppColors.textDim)),
              ],
              if (ctrl.deleteOperationError != null) ...[
                const SizedBox(height: 4),
                Text(ctrl.deleteOperationError!,
                    style: AppTextStyles.meta
                        .copyWith(color: AppColors.danger)),
              ],
            ],
          ),
        ),
        const SizedBox(height: 12),
        ...ctrl.deleteOperationPhaseRows.map((row) {
          final rowStatus = (row['status'] ?? 'pending').toString();
          final statusColor = rowStatus == 'completed'
              ? AppColors.success
              : rowStatus == 'running'
                  ? AppColors.warning
                  : rowStatus == 'failed'
                      ? AppColors.danger
                      : AppColors.textFaint;
          final pct = (row['progress_pct'] as num?)?.toDouble() ?? 0;
          final current = (row['current'] as num?)?.toInt() ?? 0;
          final total = (row['total'] as num?)?.toInt() ?? 0;
          final deleted = (row['deleted_count'] as num?)?.toInt() ?? 0;
          final unit = (row['unit'] ?? 'records').toString();

          return Padding(
            padding: const EdgeInsets.only(bottom: 8),
            child: GlassCard(
              borderColor: AppColors.border,
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Row(
                    children: [
                      Expanded(
                        child: Column(
                          crossAxisAlignment: CrossAxisAlignment.start,
                          children: [
                            Text(
                              (row['label'] ?? '').toString(),
                              style: AppTextStyles.bodyHi
                                  .copyWith(color: AppColors.textMd),
                            ),
                            Text(
                              (row['message'] ?? 'Queued').toString(),
                              style: AppTextStyles.meta
                                  .copyWith(color: AppColors.textDim),
                            ),
                          ],
                        ),
                      ),
                      AppBadge(
                          label: rowStatus,
                          color: statusColor),
                    ],
                  ),
                  const SizedBox(height: 8),
                  ClipRRect(
                    borderRadius: BorderRadius.circular(3),
                    child: LinearProgressIndicator(
                      value: pct / 100,
                      minHeight: 6,
                      backgroundColor: AppColors.surface,
                      valueColor: AlwaysStoppedAnimation(statusColor),
                    ),
                  ),
                  const SizedBox(height: 4),
                  Row(
                    mainAxisAlignment: MainAxisAlignment.spaceBetween,
                    children: [
                      Text('$current / $total $unit',
                          style: AppTextStyles.nano
                              .copyWith(color: AppColors.textDim)),
                      Text('$deleted deleted',
                          style: AppTextStyles.nano
                              .copyWith(color: AppColors.textDim)),
                    ],
                  ),
                ],
              ),
            ),
          );
        }),
        const SizedBox(height: 8),
      ],
    );
  }
}
