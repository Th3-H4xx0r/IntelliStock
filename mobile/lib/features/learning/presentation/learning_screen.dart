import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import '../../../core/theme/app_colors.dart';
import '../../../core/theme/app_text_styles.dart';
import '../../../core/widgets/app_background.dart';
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
                if (state.partialError != null) ...[
                  const SizedBox(height: 12),
                  ErrorBanner(message: state.partialError!),
                ],
                const SizedBox(height: 20),
                const SectionHeader(title: 'Pending approvals'),
                const SizedBox(height: 8),
                _approvals(state),
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
