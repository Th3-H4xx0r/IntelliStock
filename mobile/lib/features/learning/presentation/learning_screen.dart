import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import '../../../core/theme/app_colors.dart';
import '../../../core/theme/app_text_styles.dart';
import '../../../core/widgets/app_background.dart';
import '../../../core/widgets/app_button.dart';
import '../../../core/widgets/common_widgets.dart';
import '../../../core/widgets/glass_card.dart';
import '../../../core/widgets/material_symbols.dart';
import '../../../core/widgets/status_pill.dart';
import '../application/learning_controller.dart';
import '../data/learning_repository.dart';

/// The six rungs of the promotion ladder. Phase 1 populates only the detection
/// step; the rest render locked so the UI never implies an autonomy that is not
/// wired yet.
const _ladder = [
  ('Proposed', 'hypothesis pre-registered with a predicted direction'),
  ('Backtest', 'paired A/B across windows, must clear the measured noise floor'),
  ('Shadow', 'virtual portfolio on live quotes, no broker surface'),
  ('Paper', 'a real paper instance, control-relative'),
  ('Live (capped)', 'real money on a bounded book'),
  ('Live (full)', 'applied to the primary live document'),
];

Color _severityColor(String severity) {
  switch (severity.toLowerCase()) {
    case 'high':
      return AppColors.danger;
    case 'medium':
      return AppColors.warning;
    default:
      return AppColors.textMuted;
  }
}

class LearningScreen extends ConsumerWidget {
  const LearningScreen({super.key});

  Future<void> _decide(BuildContext context, WidgetRef ref,
      LearningApproval approval, String decision) async {
    try {
      await ref.read(learningRepositoryProvider).decide(approval.id, decision);
      ref.invalidate(learningStateProvider);
    } catch (err) {
      if (!context.mounted) return;
      ScaffoldMessenger.of(context).showSnackBar(
        SnackBar(content: Text('Could not record that decision: $err')),
      );
    }
  }

  Future<void> _setRunning(
      BuildContext context, WidgetRef ref, bool running) async {
    try {
      await ref.read(learningRepositoryProvider).setRunning(running);
      ref.invalidate(learningStateProvider);
    } catch (err) {
      if (!context.mounted) return;
      ScaffoldMessenger.of(context).showSnackBar(
        SnackBar(content: Text('Could not change the engine: $err')),
      );
    }
  }

  Future<void> _setMode(
      BuildContext context, WidgetRef ref, String mode) async {
    try {
      await ref.read(learningRepositoryProvider).setMode(mode);
      ref.invalidate(learningStateProvider);
    } catch (err) {
      if (!context.mounted) return;
      ScaffoldMessenger.of(context).showSnackBar(
        SnackBar(content: Text('Could not change the mode: $err')),
      );
    }
  }

  Future<void> _openTargets(
      BuildContext context, WidgetRef ref, LearningTargets targets) async {
    await showModalBottomSheet<void>(
      context: context,
      backgroundColor: AppColors.panel,
      isScrollControlled: true,
      showDragHandle: true,
      builder: (_) => _TargetsSheet(targets: targets, ref: ref),
    );
    ref.invalidate(learningStateProvider);
  }

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final async = ref.watch(learningStateProvider);
    return Scaffold(
      backgroundColor: AppColors.canvas,
      appBar: AppBar(
        backgroundColor: Colors.transparent,
        elevation: 0,
        title: Text('Learning', style: AppTextStyles.h3),
      ),
      body: AppBackground(
        child: async.when(
          loading: () => const LoadingState(label: 'Loading learning data…'),
          error: (err, _) => Padding(
            padding: const EdgeInsets.all(16),
            child: ErrorBanner(
              message: err.toString(),
              onRetry: () => ref.invalidate(learningStateProvider),
            ),
          ),
          data: (state) => RefreshIndicator(
            color: AppColors.primary,
            backgroundColor: AppColors.surface,
            // `ref.invalidate` returns void, so an `async =>` wrapper settles on
            // the next microtask — the spinner snapped away over stale data
            // before the refetch had started. Await the new future instead.
            onRefresh: () => ref.refresh(learningStateProvider.future),
            child: ListView(
              // Phase 1 ships with zero findings, so the content is shorter
              // than the viewport. Default physics refuse a drag on a
              // non-scrollable list, which makes RefreshIndicator inert on
              // exactly the state this screen launches in.
              physics: const AlwaysScrollableScrollPhysics(),
              padding: const EdgeInsets.fromLTRB(16, 8, 16, 32),
              children: [
                _header(state),
                const SizedBox(height: 12),
                _Controls(
                  state: state,
                  onToggleEngine: () =>
                      _setRunning(context, ref, !state.engineRunning),
                  onModeChanged: (mode) => _setMode(context, ref, mode),
                  onPickTargets: state.targets == null
                      ? null
                      : () => _openTargets(context, ref, state.targets!),
                ),
                if (state.partialError != null) ...[
                  const SizedBox(height: 12),
                  ErrorBanner(message: state.partialError!),
                ],
                const SizedBox(height: 20),
                const SectionHeader(title: 'Pending approvals'),
                const SizedBox(height: 8),
                if (state.approvals.isEmpty)
                  _approvals(state)
                else
                  ...state.approvals.map((a) => Padding(
                        padding: const EdgeInsets.only(bottom: 8),
                        child: _ApprovalCard(
                          approval: a,
                          onDecide: (decision) =>
                              _decide(context, ref, a, decision),
                        ),
                      )),
                const SizedBox(height: 20),
                const SectionHeader(title: 'Measured noise floors'),
                const SizedBox(height: 8),
                if (state.floors.isEmpty)
                  GlassCard(
                    padding: const EdgeInsets.all(16),
                    child: Column(
                      crossAxisAlignment: CrossAxisAlignment.start,
                      children: [
                        Text('No floor measured yet',
                            style: AppTextStyles.cardTitle
                                .copyWith(color: AppColors.warning)),
                        const SizedBox(height: 4),
                        Text(
                          'Two runs of one window have differed by ~16pp here, '
                          'so nothing is promotable until a target has a '
                          'measured floor.',
                          style: AppTextStyles.meta
                              .copyWith(color: AppColors.textDim),
                        ),
                      ],
                    ),
                  )
                else
                  ...state.floors.map((f) => Padding(
                        padding: const EdgeInsets.only(bottom: 8),
                        child: GlassCard(
                          padding: const EdgeInsets.all(14),
                          child: Row(
                            children: [
                              Expanded(
                                child: Column(
                                  crossAxisAlignment: CrossAxisAlignment.start,
                                  children: [
                                    Text(f.target,
                                        style: AppTextStyles.cardTitle),
                                    Text(f.windowClass,
                                        style: AppTextStyles.nano.copyWith(
                                            color: AppColors.textDim)),
                                    if (!f.measured && f.reason.isNotEmpty)
                                      Text(f.reason,
                                          style: AppTextStyles.nano.copyWith(
                                              color: AppColors.warning)),
                                  ],
                                ),
                              ),
                              Text(
                                f.measured
                                    ? '${f.floorPp.toStringAsFixed(2)}pp'
                                    : '—',
                                style: AppTextStyles.value.copyWith(
                                    color: f.measured
                                        ? AppColors.textHi
                                        : AppColors.warning),
                              ),
                            ],
                          ),
                        ),
                      )),
                const SizedBox(height: 20),
                const SectionHeader(title: 'Findings & reports'),
                const SizedBox(height: 8),
                if (state.findings.isEmpty)
                  EmptyState(
                    icon: symbol('lightbulb'),
                    title: 'Nothing raised yet',
                    subtitle:
                        'Findings appear as completed runs are observed.',
                  )
                else
                  ...state.findings.map((f) => Padding(
                        padding: const EdgeInsets.only(bottom: 8),
                        child: _FindingCard(finding: f),
                      )),
                const SizedBox(height: 20),
                const SectionHeader(title: 'Observed runs'),
                const SizedBox(height: 8),
                if (state.funnels.isEmpty)
                  EmptyState(
                    icon: symbol('analytics'),
                    title: 'No runs observed yet',
                  )
                else
                  ...state.funnels.map((r) => Padding(
                        padding: const EdgeInsets.only(bottom: 8),
                        child: _FunnelRow(funnel: r),
                      )),
              ],
            ),
          ),
        ),
      ),
    );
  }

  Widget _header(LearningState state) {
    final ov = state.overview;
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Row(
          children: [
            StatusPill(
              label: state.observeOnly ? 'Observe only' : (ov?.mode ?? '—'),
              color: state.observeOnly ? AppColors.info : AppColors.success,
              pulsing: !state.observeOnly,
            ),
            const SizedBox(width: 8),
            StatusPill(
              label: (ov?.engineRunning ?? false) ? 'Engine on' : 'Engine off',
              color: (ov?.engineRunning ?? false)
                  ? AppColors.success
                  : AppColors.textDim,
              pulsing: ov?.engineRunning ?? false,
            ),
          ],
        ),
        const SizedBox(height: 12),
        Row(
          children: [
            Expanded(
                child: StatTile(
                    label: 'Open findings',
                    value: '${ov?.openFindings ?? 0}')),
            const SizedBox(width: 8),
            Expanded(
                child: StatTile(
                    label: 'Runs observed',
                    value: '${ov?.runsObserved ?? 0}')),
          ],
        ),
        const SizedBox(height: 8),
        Row(
          children: [
            Expanded(
                child: StatTile(
                    label: 'Decisions',
                    value: '${ov?.decisionsObserved ?? 0}')),
            const SizedBox(width: 8),
            Expanded(
                child: StatTile(
                    label: 'Refusals',
                    value: '${ov?.refusalsObserved ?? 0}')),
          ],
        ),
      ],
    );
  }

  Widget _approvals(LearningState state) {
    return GlassCard(
      padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 20),
      child: Column(
        children: [
          Text('No approvals waiting',
              style: AppTextStyles.cardTitle.copyWith(color: AppColors.textMd)),
          const SizedBox(height: 6),
          Text(
            'The subsystem is observe-only — it records and reports, and does '
            'not yet propose changes.',
            textAlign: TextAlign.center,
            style: AppTextStyles.meta.copyWith(color: AppColors.textDim),
          ),
        ],
      ),
    );
  }
}

/// A finding, tappable into its ladder stepper.
class _FindingCard extends StatefulWidget {
  const _FindingCard({required this.finding});

  final LearningFinding finding;

  @override
  State<_FindingCard> createState() => _FindingCardState();
}

class _FindingCardState extends State<_FindingCard> {
  bool _open = false;

  @override
  Widget build(BuildContext context) {
    final f = widget.finding;
    return GlassCard(
      padding: const EdgeInsets.all(14),
      onTap: () => setState(() => _open = !_open),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Row(
            children: [
              AppBadge(label: f.severity, color: _severityColor(f.severity)),
              const SizedBox(width: 8),
              Expanded(
                child: Text(f.target,
                    style: AppTextStyles.nano
                        .copyWith(color: AppColors.textDim)),
              ),
              Icon(symbol(_open ? 'expand_less' : 'expand_more'),
                  size: 18, color: AppColors.textFaint),
            ],
          ),
          const SizedBox(height: 6),
          Text(f.title, style: AppTextStyles.cardTitle),
          const SizedBox(height: 4),
          Text(f.detail, style: AppTextStyles.meta),
          if (_open) ...[
            const SizedBox(height: 12),
            Divider(color: AppColors.border, height: 1),
            const SizedBox(height: 12),
            _step(
              label: 'Detected',
              detail: '${f.kind} · run ${f.runId.isEmpty ? "—" : f.runId}',
              color: AppColors.danger,
              reached: true,
            ),
            if (f.evidence.isNotEmpty)
              Padding(
                padding: const EdgeInsets.only(left: 20, bottom: 12),
                child: Text(
                  f.evidence.entries
                      .map((e) => '${e.key}: ${e.value}')
                      .join('\n'),
                  style: AppTextStyles.nano
                      .copyWith(color: AppColors.textFaint),
                ),
              ),
            for (final rung in _ladder)
              _step(
                label: rung.$1,
                detail: rung.$2,
                color: AppColors.textFaint,
                reached: false,
              ),
          ],
        ],
      ),
    );
  }

  Widget _step({
    required String label,
    required String detail,
    required Color color,
    required bool reached,
  }) {
    return Padding(
      padding: const EdgeInsets.only(bottom: 12),
      child: Row(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Padding(
            padding: const EdgeInsets.only(top: 4, right: 10),
            child: Container(
              width: 10,
              height: 10,
              decoration: BoxDecoration(color: color, shape: BoxShape.circle),
            ),
          ),
          Expanded(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Text(label,
                    style: AppTextStyles.micro.copyWith(
                      color: reached ? AppColors.textMd : AppColors.textDim,
                      fontWeight: FontWeight.w600,
                    )),
                Text(detail,
                    style: AppTextStyles.nano
                        .copyWith(color: AppColors.textFaint)),
                if (!reached)
                  Text('not reached — the subsystem observes only',
                      style: AppTextStyles.nano
                          .copyWith(color: AppColors.textFaint)),
              ],
            ),
          ),
        ],
      ),
    );
  }
}

class _FunnelRow extends StatelessWidget {
  const _FunnelRow({required this.funnel});

  final LearningFunnel funnel;

  @override
  Widget build(BuildContext context) {
    final pct = funnel.buyConversionPct;
    return GlassCard(
      padding: const EdgeInsets.all(14),
      child: Row(
        children: [
          Expanded(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Text('Run ${funnel.runId}', style: AppTextStyles.cardTitle),
                Text(funnel.target,
                    style: AppTextStyles.nano
                        .copyWith(color: AppColors.textDim)),
                const SizedBox(height: 4),
                Text(
                  '${funnel.decided} decided · ${funnel.executed} executed · '
                  '${funnel.refused} refused',
                  style: AppTextStyles.meta,
                ),
              ],
            ),
          ),
          Column(
            crossAxisAlignment: CrossAxisAlignment.end,
            children: [
              Text(
                pct == null ? '—' : '${pct.toStringAsFixed(1)}%',
                style: AppTextStyles.value.copyWith(
                  color: (pct != null && pct < 25)
                      ? AppColors.danger
                      : AppColors.textHi,
                ),
              ),
              Text('buy conv.',
                  style:
                      AppTextStyles.nano.copyWith(color: AppColors.textDim)),
            ],
          ),
        ],
      ),
    );
  }
}


/// A proposal awaiting an answer. Live-rung cards are visually distinct because
/// they wait indefinitely — silence is never consent for real money.
class _ApprovalCard extends StatelessWidget {
  const _ApprovalCard({required this.approval, required this.onDecide});

  final LearningApproval approval;
  final void Function(String decision) onDecide;

  @override
  Widget build(BuildContext context) {
    final live = approval.holdsForever;
    return GlassCard(
      padding: const EdgeInsets.all(14),
      borderColor: live ? AppColors.danger : AppColors.border,
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Row(
            children: [
              AppBadge(
                label: approval.rung,
                color: live ? AppColors.danger : AppColors.info,
              ),
              const SizedBox(width: 8),
              Expanded(
                child: Text(approval.actionClass,
                    style: AppTextStyles.nano
                        .copyWith(color: AppColors.textDim)),
              ),
              Text('doc ${approval.documentId}',
                  style: AppTextStyles.nano
                      .copyWith(color: AppColors.textFaint)),
            ],
          ),
          const SizedBox(height: 6),
          Text(approval.summary, style: AppTextStyles.body),
          const SizedBox(height: 4),
          Text(
            live
                ? '${approval.target} · this one waits until you answer'
                : approval.target,
            style: AppTextStyles.nano.copyWith(
                color: live ? AppColors.danger : AppColors.textFaint),
          ),
          const SizedBox(height: 10),
          Row(
            children: [
              Expanded(
                child: AppButton.primary(
                  label: 'Approve',
                  onPressed: () => onDecide('approved'),
                ),
              ),
              const SizedBox(width: 8),
              Expanded(
                child: AppButton.ghost(
                  label: 'Reject',
                  onPressed: () => onDecide('rejected'),
                ),
              ),
            ],
          ),
        ],
      ),
    );
  }
}


String _targetsLabel(LearningState state) {
  final t = state.targets;
  if (t == null) return 'Documents & instances';
  final armed = t.documentAllowlist.length;
  final watching = t.watchingAll ? 'all' : '${t.watchedInstances.length}';
  return 'Documents ($armed armed) · watching $watching';
}

/// Engine start/stop and the mode selector. The full settings surface stays on
/// the web — it is a long form, and a phone is where you ANSWER a proposal, not
/// where you configure a permission matrix.
class _Controls extends StatelessWidget {
  const _Controls({
    required this.state,
    required this.onToggleEngine,
    required this.onModeChanged,
    this.onPickTargets,
  });

  final LearningState state;
  final VoidCallback onToggleEngine;
  final void Function(String mode) onModeChanged;
  final VoidCallback? onPickTargets;

  @override
  Widget build(BuildContext context) {
    final running = state.engineRunning;
    return GlassCard(
      padding: const EdgeInsets.all(14),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Row(
            children: [
              Expanded(
                child: Text(running ? 'Engine running' : 'Engine stopped',
                    style: AppTextStyles.cardTitle.copyWith(
                        color: running ? AppColors.success : AppColors.textMd)),
              ),
              running
                  ? AppButton.ghost(label: 'Stop', onPressed: onToggleEngine)
                  : AppButton.primary(label: 'Start', onPressed: onToggleEngine),
            ],
          ),
          const SizedBox(height: 12),
          Text('Mode', style: AppTextStyles.nano.copyWith(color: AppColors.textDim)),
          const SizedBox(height: 6),
          Wrap(
            spacing: 8,
            children: [
              for (final mode in const ['observe', 'propose', 'act'])
                ChoiceChip(
                  label: Text(mode),
                  selected: state.mode == mode,
                  onSelected: (_) => onModeChanged(mode),
                  labelStyle: AppTextStyles.micro.copyWith(
                      color: state.mode == mode
                          ? AppColors.onPrimary
                          : AppColors.textMd),
                  selectedColor: AppColors.primary,
                  backgroundColor: AppColors.surface,
                  side: const BorderSide(color: AppColors.border),
                ),
            ],
          ),
          const SizedBox(height: 12),
          SizedBox(
            width: double.infinity,
            child: AppButton.ghost(
              label: _targetsLabel(state),
              onPressed: onPickTargets,
            ),
          ),
          const SizedBox(height: 8),
          Text(
            'Budgets and the permission matrix are on the web tab — a phone is '
            'where you answer a proposal, not where you tune a matrix.',
            style: AppTextStyles.nano.copyWith(color: AppColors.textFaint),
          ),
        ],
      ),
    );
  }
}


/// Pick which strategy documents may be written to, and which instances the
/// engine watches. The two lists mean OPPOSITE things when empty, so each says
/// so rather than leaving the operator to infer it.
class _TargetsSheet extends StatefulWidget {
  const _TargetsSheet({required this.targets, required this.ref});

  final LearningTargets targets;
  final WidgetRef ref;

  @override
  State<_TargetsSheet> createState() => _TargetsSheetState();
}

class _TargetsSheetState extends State<_TargetsSheet> {
  late final Set<String> _armed = {...widget.targets.documentAllowlist};
  late final Set<String> _watched = {...widget.targets.watchedInstances};
  bool _saving = false;
  String? _error;

  Future<void> _save() async {
    setState(() {
      _saving = true;
      _error = null;
    });
    try {
      final repo = widget.ref.read(learningRepositoryProvider);
      await repo.setDocumentAllowlist(_armed.toList());
      await repo.setWatchedInstances(_watched.toList());
      if (mounted) Navigator.pop(context);
    } catch (err) {
      if (mounted) {
        setState(() {
          _error = err.toString();
          _saving = false;
        });
      }
    }
  }

  @override
  Widget build(BuildContext context) {
    final t = widget.targets;
    return SafeArea(
      child: Padding(
        padding: const EdgeInsets.fromLTRB(16, 0, 16, 16),
        child: Column(
          mainAxisSize: MainAxisSize.min,
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Text('Documents & instances', style: AppTextStyles.h3),
            const SizedBox(height: 12),
            Flexible(
              child: ListView(
                shrinkWrap: true,
                children: [
                  Text('Documents the subsystem may write to',
                      style: AppTextStyles.cardTitle),
                  Text(
                    'Empty means it writes nowhere.',
                    style: AppTextStyles.nano
                        .copyWith(color: AppColors.textDim),
                  ),
                  const SizedBox(height: 8),
                  for (final doc in t.strategies)
                    CheckboxListTile(
                      dense: true,
                      contentPadding: EdgeInsets.zero,
                      value: _armed.contains(doc.id),
                      onChanged: (on) => setState(() =>
                          on == true ? _armed.add(doc.id) : _armed.remove(doc.id)),
                      title: Row(
                        children: [
                          Expanded(
                            child: Text(doc.name,
                                style: AppTextStyles.body,
                                overflow: TextOverflow.ellipsis),
                          ),
                          if (doc.isLive)
                            AppBadge(label: 'real money', color: AppColors.danger),
                        ],
                      ),
                      subtitle: Text(
                        doc.instanceNames.isEmpty
                            ? '#${doc.id} · not attached to an instance'
                            : '#${doc.id} · ${doc.instanceNames.join(', ')}',
                        style: AppTextStyles.nano
                            .copyWith(color: AppColors.textFaint),
                      ),
                    ),
                  const SizedBox(height: 16),
                  Text('Instances to watch', style: AppTextStyles.cardTitle),
                  Text(
                    _watched.isEmpty
                        ? 'None selected — watching every instance.'
                        : 'Only the selected instances are observed.',
                    style: AppTextStyles.nano.copyWith(
                        color: _watched.isEmpty
                            ? AppColors.info
                            : AppColors.textDim),
                  ),
                  const SizedBox(height: 8),
                  for (final inst in t.instances)
                    CheckboxListTile(
                      dense: true,
                      contentPadding: EdgeInsets.zero,
                      value: _watched.contains(inst.id),
                      onChanged: (on) => setState(() => on == true
                          ? _watched.add(inst.id)
                          : _watched.remove(inst.id)),
                      title: Row(
                        children: [
                          Expanded(
                            child: Text(inst.name,
                                style: AppTextStyles.body,
                                overflow: TextOverflow.ellipsis),
                          ),
                          if (inst.isLive)
                            AppBadge(label: 'live', color: AppColors.danger)
                          else if (inst.running)
                            AppBadge(label: 'running', color: AppColors.success),
                        ],
                      ),
                      subtitle: Text('${inst.kind} · doc #${inst.strategyId ?? "—"}',
                          style: AppTextStyles.nano
                              .copyWith(color: AppColors.textFaint)),
                    ),
                ],
              ),
            ),
            if (_error != null) ...[
              const SizedBox(height: 8),
              ErrorBanner(message: _error!),
            ],
            const SizedBox(height: 12),
            SizedBox(
              width: double.infinity,
              child: AppButton.primary(
                label: _saving ? 'Saving…' : 'Save',
                onPressed: _saving ? null : _save,
              ),
            ),
          ],
        ),
      ),
    );
  }
}
