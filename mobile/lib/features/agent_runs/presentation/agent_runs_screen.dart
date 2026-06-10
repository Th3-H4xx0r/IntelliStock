import 'dart:math' as math;
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
import '../../../core/widgets/skeleton.dart';
import '../../../core/widgets/status_pill.dart';
import '../application/agent_runs_controller.dart';
import '../data/agent_repository.dart';

// ── Helpers ────────────────────────────────────────────────────────────────────

const _perPageOptions = [10, 20, 50, 100];

Color _statusColor(String status) {
  switch (status.toLowerCase()) {
    case 'running':
      return AppColors.info;
    case 'passed':
      return AppColors.success;
    case 'failed':
    case 'error':
      return AppColors.danger;
    case 'tossed':
      return AppColors.warning;
    default:
      return AppColors.textFaint;
  }
}

IconData _statusIcon(String status) {
  switch (status.toLowerCase()) {
    case 'passed':
      return symbol('check_circle');
    case 'failed':
    case 'error':
      return symbol('cancel');
    case 'tossed':
      return symbol('do_not_disturb_on');
    case 'duplicate':
      return symbol('content_copy');
    case 'stopped':
      return symbol('stop_circle');
    default:
      return symbol('radio_button_unchecked');
  }
}

bool _isSpinning(String status) => status.toLowerCase() == 'running';

String _fmtCountdown(int secs) {
  final m = (secs ~/ 60).toString().padLeft(2, '0');
  final s = (secs % 60).toString().padLeft(2, '0');
  return '$m:$s';
}

// ── Cycle grouping ─────────────────────────────────────────────────────────────

class _Cycle {
  _Cycle({required this.cycleId, required this.startedAt, required this.runs});
  final String cycleId;
  final DateTime? startedAt;
  final List<AgentRun> runs;
}

List<_Cycle> _groupByCycle(List<AgentRun> runs) {
  final map = <String, _Cycle>{};
  for (final r in runs) {
    final cid = r.cycleId ?? r.id;
    if (!map.containsKey(cid)) {
      map[cid] = _Cycle(cycleId: cid, startedAt: r.createdAt, runs: []);
    }
    map[cid]!.runs.add(r);
  }
  return map.values.toList();
}

// ── Screen ─────────────────────────────────────────────────────────────────────

class AgentRunsScreen extends ConsumerWidget {
  const AgentRunsScreen({super.key});

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final asyncState = ref.watch(agentRunsControllerProvider);

    return Scaffold(
      backgroundColor: AppColors.canvas,
      body: AppBackground(
        child: SafeArea(
          child: asyncState.when(
            loading: () => const _AgentRunsSkeleton(),
            error: (e, _) => Center(
              child: ErrorBanner(
                message: e.toString(),
                onRetry: () =>
                    ref.read(agentRunsControllerProvider.notifier).refreshNow(),
              ),
            ),
            data: (state) => _Body(state: state),
          ),
        ),
      ),
    );
  }
}

class _Body extends ConsumerWidget {
  const _Body({required this.state});
  final AgentRunsState state;

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final ctrl = ref.read(agentRunsControllerProvider.notifier);
    final cycles = _groupByCycle(state.runs);

    return RefreshIndicator(
      onRefresh: ctrl.refreshNow,
      child: CustomScrollView(
        slivers: [
          SliverToBoxAdapter(
            child: Padding(
              padding: const EdgeInsets.fromLTRB(16, 20, 16, 0),
              child: _Header(state: state),
            ),
          ),
          const SliverToBoxAdapter(child: SizedBox(height: 8)),
          SliverToBoxAdapter(
            child: Padding(
              padding: const EdgeInsets.symmetric(horizontal: 16),
              child: _ControlsRow(state: state),
            ),
          ),
          const SliverToBoxAdapter(child: SizedBox(height: 16)),

          // Error banner
          if (state.errorMessage != null)
            SliverToBoxAdapter(
              child: Padding(
                padding: const EdgeInsets.symmetric(horizontal: 16),
                child: ErrorBanner(message: state.errorMessage!),
              ),
            ),

          // Empty / loading skeleton
          if (state.busy && state.runs.isEmpty)
            SliverList(
              delegate: SliverChildBuilderDelegate(
                (_, _) => Padding(
                  padding:
                      const EdgeInsets.symmetric(horizontal: 16, vertical: 6),
                  child: _RunCardSkeleton(),
                ),
                childCount: 3,
              ),
            )
          else if (state.runs.isEmpty)
            SliverToBoxAdapter(
              child: Padding(
                padding: const EdgeInsets.all(24),
                child: EmptyState(
                  icon: symbol('smart_toy'),
                  title: 'No agent runs yet',
                  subtitle:
                      'Start the AI Backtest Agent to see strategy attempts here.',
                ),
              ),
            )
          else
            SliverList(
              delegate: SliverChildBuilderDelegate(
                (_, i) => Padding(
                  padding:
                      const EdgeInsets.symmetric(horizontal: 16, vertical: 6),
                  child: _CycleBlock(
                      cycle: cycles[i], agentControl: state.control),
                ),
                childCount: cycles.length,
              ),
            ),

          // Pagination
          if (state.totalPages > 1)
            SliverToBoxAdapter(
              child: Padding(
                padding: const EdgeInsets.fromLTRB(16, 8, 16, 24),
                child: _Pagination(state: state),
              ),
            )
          else
            const SliverToBoxAdapter(child: SizedBox(height: 24)),
        ],
      ),
    );
  }
}

// ── Header ────────────────────────────────────────────────────────────────────

class _Header extends StatelessWidget {
  const _Header({required this.state});
  final AgentRunsState state;

  String get _statusLabel {
    if (state.control.isRunning) return 'Running';
    if (state.control.isPaused) return 'Paused';
    return 'Stopped';
  }

  Color get _statusColor {
    if (state.control.isRunning) return AppColors.success;
    if (state.control.isPaused) return AppColors.warning;
    return AppColors.textFaint;
  }

  @override
  Widget build(BuildContext context) {
    return Row(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Expanded(
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Text('AI Agent Runs', style: AppTextStyles.h1),
              const SizedBox(height: 4),
              Text(
                'Strategy attempts by the AI Backtest Agent.',
                style: AppTextStyles.meta.copyWith(color: AppColors.textDim),
              ),
            ],
          ),
        ),
        const SizedBox(width: 12),
        StatusPill(
          label: _statusLabel,
          color: _statusColor,
          pulsing: state.control.isRunning,
        ),
      ],
    );
  }
}

// ── Controls row ───────────────────────────────────────────────────────────────

class _ControlsRow extends ConsumerWidget {
  const _ControlsRow({required this.state});
  final AgentRunsState state;

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final ctrl = ref.read(agentRunsControllerProvider.notifier);
    final hasCountdown = state.scheduledResumeAt != null;

    return Wrap(
      spacing: 8,
      runSpacing: 8,
      crossAxisAlignment: WrapCrossAlignment.center,
      children: [
        // Start (when stopped)
        if (state.control.isStopped)
          AppButton.semantic(
            label: 'Start',
            icon: symbol('play_arrow'),
            color: AppColors.success,
            busy: state.busy,
            onPressed: () => _showStartModal(context, ctrl),
          ),

        // Pause (when running)
        if (state.control.isRunning)
          AppButton.semantic(
            label: 'Pause',
            icon: symbol('pause'),
            color: AppColors.warning,
            busy: state.busy,
            onPressed: ctrl.pauseAgent,
          ),

        // Unpause (when paused and no countdown)
        if (state.control.isPaused && !hasCountdown)
          AppButton.semantic(
            label: 'Unpause',
            icon: symbol('play_arrow'),
            color: AppColors.info,
            busy: state.busy,
            onPressed: () => _showResumeModal(context, ctrl),
          ),

        // Countdown ring
        if (hasCountdown)
          _CountdownRing(
            fraction: state.countdownFraction,
            secsRemaining: state.countdownSecsRemaining,
            onCancel: ctrl.cancelCountdown,
          ),

        // Stop (when running or paused)
        if (!state.control.isStopped)
          AppButton.semantic(
            label: 'Stop',
            icon: symbol('stop'),
            color: AppColors.danger,
            busy: state.busy,
            onPressed: ctrl.stopAgent,
          ),

        // Spacer
        const SizedBox(width: 8),

        // Per-page dropdown
        _PerPageSelector(
          value: state.perPage,
          onChanged: (v) => ctrl.setPerPage(v),
        ),

        // Refresh
        IconButton(
          icon: state.busy
              ? const SizedBox(
                  width: 16,
                  height: 16,
                  child: CircularProgressIndicator(strokeWidth: 1.5),
                )
              : Icon(symbol('refresh'),
                  size: 18, color: AppColors.textMuted),
          onPressed: ctrl.refreshNow,
          tooltip: 'Refresh',
        ),
      ],
    );
  }

  void _showStartModal(BuildContext context, AgentRunsController ctrl) {
    showDialog(
      context: context,
      builder: (_) => _StartAgentModal(onConfirm: ctrl.startAgent),
    );
  }

  void _showResumeModal(BuildContext context, AgentRunsController ctrl) {
    showDialog(
      context: context,
      builder: (_) =>
          _ResumeModal(onConfirm: (mins) => ctrl.scheduleResume(mins)),
    );
  }
}

// ── Countdown ring ────────────────────────────────────────────────────────────

class _CountdownRing extends StatelessWidget {
  const _CountdownRing({
    required this.fraction,
    required this.secsRemaining,
    required this.onCancel,
  });

  final double fraction;
  final int secsRemaining;
  final VoidCallback onCancel;

  @override
  Widget build(BuildContext context) {
    return Row(
      mainAxisSize: MainAxisSize.min,
      children: [
        SizedBox(
          width: 44,
          height: 44,
          child: Stack(
            alignment: Alignment.center,
            children: [
              CustomPaint(
                size: const Size(44, 44),
                painter: _RingPainter(fraction: fraction),
              ),
              GestureDetector(
                onTap: onCancel,
                child: Container(
                  width: 22,
                  height: 22,
                  decoration: BoxDecoration(
                    color: AppColors.fill(AppColors.danger),
                    borderRadius: BorderRadius.circular(4),
                    border: Border.all(
                        color: AppColors.stroke(AppColors.danger)),
                  ),
                  child: Icon(symbol('stop'),
                      size: 12, color: AppColors.danger),
                ),
              ),
            ],
          ),
        ),
        const SizedBox(width: 6),
        Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          mainAxisSize: MainAxisSize.min,
          children: [
            Text(
              _fmtCountdown(secsRemaining),
              style: AppTextStyles.mono(10,
                  color: AppColors.info,
                  weight: FontWeight.w600),
            ),
            Text('resuming',
                style: AppTextStyles.nano.copyWith(color: AppColors.textFaint)),
          ],
        ),
      ],
    );
  }
}

class _RingPainter extends CustomPainter {
  _RingPainter({required this.fraction});
  final double fraction;

  static const double _r = 18;
  static const double _strokeW = 3.5;
  static const double _ringC = 2 * math.pi * _r;

  @override
  void paint(Canvas canvas, Size size) {
    final cx = size.width / 2;
    final cy = size.height / 2;
    final rect = Rect.fromCircle(center: Offset(cx, cy), radius: _r);

    // Track
    canvas.drawArc(
      rect,
      0,
      2 * math.pi,
      false,
      Paint()
        ..color = Colors.white.withValues(alpha: 0.1)
        ..style = PaintingStyle.stroke
        ..strokeWidth = _strokeW,
    );

    // Arc (depletes as time passes)
    final sweepAngle = _ringC * (1.0 - fraction) / _r;
    canvas.drawArc(
      rect,
      -math.pi / 2,
      sweepAngle,
      false,
      Paint()
        ..color = AppColors.info
        ..style = PaintingStyle.stroke
        ..strokeWidth = _strokeW
        ..strokeCap = StrokeCap.round,
    );
  }

  @override
  bool shouldRepaint(_RingPainter old) => old.fraction != fraction;
}

// ── Per-page selector ─────────────────────────────────────────────────────────

class _PerPageSelector extends StatelessWidget {
  const _PerPageSelector({required this.value, required this.onChanged});
  final int value;
  final void Function(int) onChanged;

  @override
  Widget build(BuildContext context) {
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 6),
      decoration: BoxDecoration(
        color: AppColors.surface,
        borderRadius: BorderRadius.circular(8),
        border: Border.all(color: AppColors.border),
      ),
      child: DropdownButtonHideUnderline(
        child: DropdownButton<int>(
          value: value,
          isDense: true,
          style: AppTextStyles.meta.copyWith(color: AppColors.textMd),
          dropdownColor: AppColors.surface,
          items: _perPageOptions
              .map((n) => DropdownMenuItem(
                    value: n,
                    child: Text('$n/page',
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

// ── Cycle block ────────────────────────────────────────────────────────────────

class _CycleBlock extends StatelessWidget {
  const _CycleBlock({required this.cycle, required this.agentControl});
  final _Cycle cycle;
  final AgentControl agentControl;

  @override
  Widget build(BuildContext context) {
    return Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: [
        // Centered timestamp divider
        Row(
          children: [
            const Expanded(
                child: Divider(color: AppColors.border, height: 1)),
            Padding(
              padding: const EdgeInsets.symmetric(horizontal: 8),
              child: Text(
                cycle.startedAt != null
                    ? fmtDateTime(cycle.startedAt)
                    : '—',
                style: AppTextStyles.nano
                    .copyWith(color: AppColors.textFaint,
                        fontFamily: 'monospace',
                        letterSpacing: 0.5),
              ),
            ),
            const Expanded(
                child: Divider(color: AppColors.border, height: 1)),
          ],
        ),
        const SizedBox(height: 12),
        ...cycle.runs.map((r) => Padding(
              padding: const EdgeInsets.only(bottom: 12),
              child: _RunCard(run: r, agentControl: agentControl),
            )),
      ],
    );
  }
}

// ── Run card ──────────────────────────────────────────────────────────────────

class _RunCard extends ConsumerWidget {
  const _RunCard({required this.run, required this.agentControl});
  final AgentRun run;
  final AgentControl agentControl;

  Color get _borderColor {
    switch (run.status.toLowerCase()) {
      case 'running':
        return AppColors.info.withValues(alpha: 0.2);
      case 'passed':
        return AppColors.success.withValues(alpha: 0.2);
      case 'failed':
      case 'error':
        return AppColors.danger.withValues(alpha: 0.2);
      case 'tossed':
        return AppColors.warning.withValues(alpha: 0.2);
      default:
        return AppColors.border;
    }
  }

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    return GlassCard(
      borderColor: _borderColor,
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          // Card header
          Row(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              IconTile(
                  icon: symbol('smart_toy'), color: AppColors.warning),
              const SizedBox(width: 10),
              Expanded(
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Text(
                      run.name ?? 'Unnamed Strategy',
                      style: AppTextStyles.cardTitle,
                      overflow: TextOverflow.ellipsis,
                    ),
                    Text(
                      fmtDateTime(run.createdAt),
                      style: AppTextStyles.nano
                          .copyWith(color: AppColors.textFaint,
                              fontFamily: 'monospace'),
                    ),
                  ],
                ),
              ),
              AppBadge(
                  label: run.status,
                  color: _statusColor(run.status)),
            ],
          ),
          const SizedBox(height: 12),

          // Stepper
          if (run.stages.isEmpty)
            _QueuedRow()
          else
            _StagesStepper(stages: run.stages, parentRunning: agentControl.isRunning),

          // Footer
          if (run.finalResult != null ||
              (run.status.toLowerCase() == 'running' &&
                  agentControl.isStopped)) ...[
            const Divider(height: 16, color: AppColors.border),
            _RunFooter(run: run, agentRunning: agentControl.isRunning),
          ],
        ],
      ),
    );
  }
}

// ── Queued placeholder ────────────────────────────────────────────────────────

class _QueuedRow extends StatelessWidget {
  @override
  Widget build(BuildContext context) {
    return Row(
      children: [
        Container(
          width: 28,
          height: 28,
          decoration: BoxDecoration(
            color: AppColors.fill(AppColors.textFaint),
            shape: BoxShape.circle,
            border: Border.all(color: AppColors.border),
          ),
          child: Icon(symbol('hourglass_empty'),
              size: 14, color: AppColors.textFaint),
        ),
        const SizedBox(width: 10),
        Text('Queued…',
            style: AppTextStyles.meta.copyWith(
                color: AppColors.textDim,
                fontStyle: FontStyle.italic)),
      ],
    );
  }
}

// ── Stages stepper ────────────────────────────────────────────────────────────

class _StagesStepper extends StatelessWidget {
  const _StagesStepper(
      {required this.stages, required this.parentRunning});
  final List<AgentStage> stages;
  final bool parentRunning;

  @override
  Widget build(BuildContext context) {
    return Column(
      children: List.generate(stages.length, (i) {
        final stage = stages[i];
        final isLast = i == stages.length - 1;
        return _StageRow(
            stage: stage,
            isLast: isLast,
            parentRunning: parentRunning);
      }),
    );
  }
}

class _StageRow extends StatelessWidget {
  const _StageRow({
    required this.stage,
    required this.isLast,
    required this.parentRunning,
  });

  final AgentStage stage;
  final bool isLast;
  final bool parentRunning;

  Color get _lineColor {
    switch (stage.status.toLowerCase()) {
      case 'running':
        return AppColors.info.withValues(alpha: 0.4);
      case 'passed':
        return AppColors.success.withValues(alpha: 0.4);
      default:
        return AppColors.border;
    }
  }

  @override
  Widget build(BuildContext context) {
    final spinning = _isSpinning(stage.status) && parentRunning;
    final iconColor = _statusColor(stage.status);

    return Row(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        // Icon column + connector
        SizedBox(
          width: 28,
          child: Column(
            children: [
              Container(
                width: 28,
                height: 28,
                decoration: BoxDecoration(
                  color: AppColors.fill(iconColor),
                  shape: BoxShape.circle,
                  border: Border.all(color: AppColors.stroke(iconColor)),
                ),
                child: spinning
                    ? _SpinningIcon(color: iconColor)
                    : Icon(_statusIcon(stage.status),
                        size: 13, color: iconColor),
              ),
              if (!isLast)
                Container(
                  width: 1,
                  height: 32,
                  color: _lineColor,
                ),
            ],
          ),
        ),
        const SizedBox(width: 10),
        // Stage content
        Expanded(
          child: Padding(
            padding: EdgeInsets.only(bottom: isLast ? 0 : 8),
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Text(stage.label,
                    style: AppTextStyles.bodyHi.copyWith(
                        color: AppColors.textMd)),
                if (stage.stocks.isNotEmpty) ...[
                  const SizedBox(height: 2),
                  Text(stage.stocks.join(', '),
                      style: AppTextStyles.nano
                          .copyWith(color: AppColors.textFaint),
                      overflow: TextOverflow.ellipsis),
                ],
                if (stage.pnl != null) ...[
                  const SizedBox(height: 4),
                  Row(
                    children: [
                      Text(
                        fmtPnl(stage.pnl!.toDouble()),
                        style: AppTextStyles.mono(11,
                            color: pnlColor(stage.pnl!.toDouble()),
                            weight: FontWeight.w600),
                      ),
                      if (stage.pnlPct != null) ...[
                        const SizedBox(width: 8),
                        Text(
                          fmtPct(stage.pnlPct!.toDouble()),
                          style: AppTextStyles.nano
                              .copyWith(color: AppColors.textDim,
                                  fontFamily: 'monospace'),
                        ),
                      ],
                    ],
                  ),
                ],
                if (stage.details != null) ...[
                  const SizedBox(height: 2),
                  Text(stage.details!,
                      style: AppTextStyles.nano
                          .copyWith(color: AppColors.textDim),
                      overflow: TextOverflow.ellipsis),
                ],
                if (stage.details == null &&
                    stage.status.toLowerCase() == 'running' &&
                    parentRunning) ...[
                  const SizedBox(height: 2),
                  Text('In progress…',
                      style: AppTextStyles.nano.copyWith(
                          color: AppColors.info,
                          fontStyle: FontStyle.italic)),
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
          size: 13, color: widget.color),
    );
  }
}

// ── Run footer ────────────────────────────────────────────────────────────────

class _RunFooter extends ConsumerWidget {
  const _RunFooter({required this.run, required this.agentRunning});
  final AgentRun run;
  final bool agentRunning;

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final ctrl = ref.read(agentRunsControllerProvider.notifier);
    final stale = run.status.toLowerCase() == 'running' && !agentRunning;

    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        if (run.finalResult != null) ...[
          Row(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Text(
                run.status == 'passed'
                    ? '✓'
                    : run.status == 'failed'
                        ? '✗'
                        : '○',
                style: AppTextStyles.bodyHi.copyWith(
                    color: _statusColor(run.status)),
              ),
              const SizedBox(width: 6),
              Expanded(
                child: Text(run.finalResult!,
                    style: AppTextStyles.meta
                        .copyWith(color: AppColors.textDim)),
              ),
            ],
          ),
        ],
        if (stale) ...[
          const SizedBox(height: 6),
          Row(
            children: [
              Expanded(
                child: Text('Agent stopped — run may be stale',
                    style: AppTextStyles.nano.copyWith(
                        color: AppColors.warning.withValues(alpha: 0.7),
                        fontStyle: FontStyle.italic)),
              ),
              const SizedBox(width: 8),
              GestureDetector(
                onTap: () => ctrl.forceStop(run.id),
                child: Container(
                  padding:
                      const EdgeInsets.symmetric(horizontal: 8, vertical: 4),
                  decoration: BoxDecoration(
                    color: AppColors.fill(AppColors.danger),
                    borderRadius: BorderRadius.circular(6),
                    border:
                        Border.all(color: AppColors.stroke(AppColors.danger)),
                  ),
                  child: Row(
                    mainAxisSize: MainAxisSize.min,
                    children: [
                      Icon(Icons.close, size: 11, color: AppColors.danger),
                      const SizedBox(width: 4),
                      Text('Mark Stopped',
                          style: AppTextStyles.nano.copyWith(
                              color: AppColors.danger,
                              fontWeight: FontWeight.w600)),
                    ],
                  ),
                ),
              ),
            ],
          ),
        ],
      ],
    );
  }
}

// ── Pagination ────────────────────────────────────────────────────────────────

class _Pagination extends ConsumerWidget {
  const _Pagination({required this.state});
  final AgentRunsState state;

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final ctrl = ref.read(agentRunsControllerProvider.notifier);
    final page = state.page;
    final total = state.totalPages;

    // Build page numbers with ellipsis
    final pages = <Object>[];
    final Set<int> pageSet = {1, total, page};
    for (int d = -2; d <= 2; d++) {
      final v = page + d;
      if (v >= 1 && v <= total) pageSet.add(v);
    }
    final sorted = pageSet.toList()..sort();
    int? prev;
    for (final p in sorted) {
      if (prev != null && p - prev > 1) pages.add('…');
      pages.add(p);
      prev = p;
    }

    return Column(
      children: [
        Text(
          '${state.total} runs · page $page of $total',
          style: AppTextStyles.nano.copyWith(color: AppColors.textDim),
        ),
        const SizedBox(height: 8),
        Wrap(
          spacing: 4,
          runSpacing: 4,
          alignment: WrapAlignment.center,
          children: [
            // Prev
            _PageBtn(
              label: '‹',
              enabled: page > 1,
              active: false,
              onTap: () => ctrl.goToPage(page - 1),
            ),
            ...pages.map((p) {
              if (p == '…') {
                return Padding(
                  padding: const EdgeInsets.symmetric(horizontal: 4),
                  child: Text('…',
                      style:
                          AppTextStyles.meta.copyWith(color: AppColors.textDim)),
                );
              }
              final n = p as int;
              return _PageBtn(
                label: '$n',
                enabled: true,
                active: n == page,
                onTap: () => ctrl.goToPage(n),
              );
            }),
            // Next
            _PageBtn(
              label: '›',
              enabled: page < total,
              active: false,
              onTap: () => ctrl.goToPage(page + 1),
            ),
          ],
        ),
      ],
    );
  }
}

class _PageBtn extends StatelessWidget {
  const _PageBtn({
    required this.label,
    required this.enabled,
    required this.active,
    required this.onTap,
  });

  final String label;
  final bool enabled;
  final bool active;
  final VoidCallback onTap;

  @override
  Widget build(BuildContext context) {
    final color = active ? AppColors.primary : AppColors.textMuted;
    return GestureDetector(
      onTap: enabled ? onTap : null,
      child: Container(
        constraints: const BoxConstraints(minWidth: 32),
        padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 6),
        decoration: BoxDecoration(
          color: active ? AppColors.fill(AppColors.primary) : Colors.transparent,
          borderRadius: BorderRadius.circular(8),
          border: Border.all(
              color: active
                  ? AppColors.stroke(AppColors.primary)
                  : AppColors.border),
        ),
        child: Text(
          label,
          textAlign: TextAlign.center,
          style: AppTextStyles.meta.copyWith(
            color: enabled ? color : AppColors.textFaint,
            fontWeight: active ? FontWeight.w600 : FontWeight.normal,
          ),
        ),
      ),
    );
  }
}

// ── Loading skeletons ─────────────────────────────────────────────────────────

/// Full-screen skeleton shown on the initial load before any data arrives.
class _AgentRunsSkeleton extends StatelessWidget {
  const _AgentRunsSkeleton();

  @override
  Widget build(BuildContext context) {
    return CustomScrollView(
      slivers: [
        SliverToBoxAdapter(
          child: Padding(
            padding: const EdgeInsets.fromLTRB(16, 20, 16, 0),
            child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
              // Header
              Row(crossAxisAlignment: CrossAxisAlignment.start, children: [
                Expanded(
                  child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
                    Skeleton(width: 180, height: 22),
                    const SizedBox(height: 6),
                    Skeleton(width: 260, height: 11),
                  ]),
                ),
                const SizedBox(width: 12),
                Skeleton(width: 70, height: 24, radius: 12),
              ]),
              const SizedBox(height: 12),
              // Controls row skeleton
              Row(children: [
                Skeleton(width: 72, height: 32, radius: 8),
                const SizedBox(width: 8),
                Skeleton(width: 60, height: 32, radius: 8),
                const SizedBox(width: 8),
                Skeleton(width: 80, height: 32, radius: 8),
              ]),
              const SizedBox(height: 20),
            ]),
          ),
        ),
        // 2 cycle-group skeletons, 2 run cards each
        for (int g = 0; g < 2; g++) ...[
          SliverToBoxAdapter(
            child: Padding(
              padding: const EdgeInsets.symmetric(horizontal: 16),
              child: Row(children: [
                const Expanded(child: Divider(color: Color(0xFF2A2142), height: 1)),
                Padding(
                  padding: const EdgeInsets.symmetric(horizontal: 8),
                  child: Skeleton(width: 100, height: 9),
                ),
                const Expanded(child: Divider(color: Color(0xFF2A2142), height: 1)),
              ]),
            ),
          ),
          const SliverToBoxAdapter(child: SizedBox(height: 12)),
          SliverList(
            delegate: SliverChildBuilderDelegate(
              (_, _) => Padding(
                padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 6),
                child: _RunCardSkeleton(),
              ),
              childCount: 2,
            ),
          ),
        ],
        const SliverToBoxAdapter(child: SizedBox(height: 24)),
      ],
    );
  }
}

class _RunCardSkeleton extends StatelessWidget {
  @override
  Widget build(BuildContext context) {
    return GlassCard(
      child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
        // Card header: icon + name + badge
        Row(crossAxisAlignment: CrossAxisAlignment.start, children: [
          Skeleton.circle(36),
          const SizedBox(width: 10),
          Expanded(
            child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
              Skeleton(width: 160, height: 13),
              const SizedBox(height: 4),
              Skeleton(width: 90, height: 9),
            ]),
          ),
          Skeleton(width: 56, height: 18, radius: 9),
        ]),
        const SizedBox(height: 12),
        // Stage rows skeleton (3 rows)
        for (int i = 0; i < 3; i++) ...[
          Row(crossAxisAlignment: CrossAxisAlignment.start, children: [
            Skeleton.circle(28),
            const SizedBox(width: 10),
            Expanded(
              child: Padding(
                padding: const EdgeInsets.only(bottom: 8),
                child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
                  Skeleton(width: double.infinity, height: 11),
                  const SizedBox(height: 4),
                  Skeleton(width: 120, height: 9),
                ]),
              ),
            ),
          ]),
        ],
      ]),
    );
  }
}

// ── Start Agent modal ─────────────────────────────────────────────────────────

class _StartAgentModal extends StatefulWidget {
  const _StartAgentModal({required this.onConfirm});
  final Future<void> Function({String? specialRequest}) onConfirm;

  @override
  State<_StartAgentModal> createState() => _StartAgentModalState();
}

class _StartAgentModalState extends State<_StartAgentModal> {
  final _ctrl = TextEditingController();
  bool _busy = false;

  @override
  void dispose() {
    _ctrl.dispose();
    super.dispose();
  }

  Future<void> _submit() async {
    setState(() => _busy = true);
    Navigator.pop(context);
    await widget.onConfirm(
        specialRequest: _ctrl.text.trim().isEmpty ? null : _ctrl.text.trim());
  }

  @override
  Widget build(BuildContext context) {
    return Dialog(
      backgroundColor: AppColors.panel,
      shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(20)),
      child: Padding(
        padding: const EdgeInsets.all(24),
        child: Column(
          mainAxisSize: MainAxisSize.min,
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            // Header
            Row(children: [
              IconTile(icon: symbol('smart_toy'), color: AppColors.warning),
              const SizedBox(width: 12),
              Expanded(
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Text('Start AI Backtest Agent',
                        style: AppTextStyles.cardTitle),
                    Text(
                        'Optionally provide a special instruction.',
                        style: AppTextStyles.nano
                            .copyWith(color: AppColors.textDim)),
                  ],
                ),
              ),
            ]),
            const SizedBox(height: 20),
            Text('Special Request',
                style:
                    AppTextStyles.nano.copyWith(color: AppColors.textMuted)),
            const SizedBox(height: 6),
            TextField(
              controller: _ctrl,
              maxLines: 4,
              style:
                  AppTextStyles.body.copyWith(color: AppColors.textHi),
              decoration: InputDecoration(
                hintText:
                    'e.g. Focus on high-volatility tech stocks…',
                hintStyle:
                    AppTextStyles.body.copyWith(color: AppColors.textFaint),
                filled: true,
                fillColor: AppColors.surface,
                border: OutlineInputBorder(
                    borderRadius: BorderRadius.circular(12),
                    borderSide: BorderSide(color: AppColors.border)),
                enabledBorder: OutlineInputBorder(
                    borderRadius: BorderRadius.circular(12),
                    borderSide: BorderSide(color: AppColors.border)),
                focusedBorder: OutlineInputBorder(
                    borderRadius: BorderRadius.circular(12),
                    borderSide: BorderSide(
                        color: AppColors.primary.withValues(alpha: 0.4))),
              ),
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
                  label: 'Start Agent',
                  icon: symbol('play_arrow'),
                  color: AppColors.success,
                  busy: _busy,
                  onPressed: _submit,
                ),
              ],
            ),
          ],
        ),
      ),
    );
  }
}

// ── Resume modal ──────────────────────────────────────────────────────────────

class _ResumeModal extends StatefulWidget {
  const _ResumeModal({required this.onConfirm});
  final Future<void> Function(int minutes) onConfirm;

  @override
  State<_ResumeModal> createState() => _ResumeModalState();
}

class _ResumeModalState extends State<_ResumeModal> {
  final _customCtrl = TextEditingController();

  static const _presets = [
    (label: 'Now', minutes: 0),
    (label: '5 min', minutes: 5),
    (label: '15 min', minutes: 15),
    (label: '30 min', minutes: 30),
    (label: '1 hr', minutes: 60),
  ];

  Future<void> _pick(int minutes) async {
    Navigator.pop(context);
    await widget.onConfirm(minutes);
  }

  @override
  void dispose() {
    _customCtrl.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    return Dialog(
      backgroundColor: AppColors.panel,
      shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(20)),
      child: Padding(
        padding: const EdgeInsets.all(24),
        child: Column(
          mainAxisSize: MainAxisSize.min,
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Row(children: [
              IconTile(icon: symbol('play_circle'), color: AppColors.info),
              const SizedBox(width: 12),
              Expanded(
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Text('Resume Agent', style: AppTextStyles.cardTitle),
                    Text('Resume now or schedule automatic resume.',
                        style: AppTextStyles.nano
                            .copyWith(color: AppColors.textDim)),
                  ],
                ),
              ),
              GestureDetector(
                onTap: () => Navigator.pop(context),
                child: Icon(symbol('close'),
                    size: 18, color: AppColors.textMuted),
              ),
            ]),
            const SizedBox(height: 20),
            Text('RESUME IN',
                style: AppTextStyles.eyebrow
                    .copyWith(color: AppColors.textDim)),
            const SizedBox(height: 8),
            Wrap(
              spacing: 8,
              runSpacing: 8,
              children: _presets.map((p) {
                return GestureDetector(
                  onTap: () => _pick(p.minutes),
                  child: Container(
                    padding: const EdgeInsets.symmetric(
                        horizontal: 12, vertical: 8),
                    decoration: BoxDecoration(
                      color: p.minutes == 0
                          ? AppColors.fill(AppColors.success)
                          : AppColors.fill(AppColors.info),
                      borderRadius: BorderRadius.circular(8),
                      border: Border.all(
                          color: p.minutes == 0
                              ? AppColors.stroke(AppColors.success)
                              : AppColors.stroke(AppColors.info)),
                    ),
                    child: Text(
                      p.label,
                      style: AppTextStyles.meta.copyWith(
                        color: p.minutes == 0
                            ? AppColors.success
                            : AppColors.info,
                        fontWeight: FontWeight.w600,
                      ),
                    ),
                  ),
                );
              }).toList(),
            ),
            const SizedBox(height: 16),
            Text('CUSTOM DELAY',
                style: AppTextStyles.eyebrow
                    .copyWith(color: AppColors.textDim)),
            const SizedBox(height: 8),
            Row(
              children: [
                SizedBox(
                  width: 80,
                  child: TextField(
                    controller: _customCtrl,
                    keyboardType: TextInputType.number,
                    style: AppTextStyles.body.copyWith(color: AppColors.textHi),
                    decoration: InputDecoration(
                      isDense: true,
                      hintText: '0',
                      hintStyle:
                          AppTextStyles.body.copyWith(color: AppColors.textFaint),
                      filled: true,
                      fillColor: AppColors.surface,
                      border: OutlineInputBorder(
                          borderRadius: BorderRadius.circular(8),
                          borderSide: BorderSide(color: AppColors.border)),
                      enabledBorder: OutlineInputBorder(
                          borderRadius: BorderRadius.circular(8),
                          borderSide: BorderSide(color: AppColors.border)),
                      focusedBorder: OutlineInputBorder(
                          borderRadius: BorderRadius.circular(8),
                          borderSide: BorderSide(
                              color: AppColors.info.withValues(alpha: 0.4))),
                    ),
                  ),
                ),
                const SizedBox(width: 8),
                Text('minutes',
                    style:
                        AppTextStyles.meta.copyWith(color: AppColors.textDim)),
                const SizedBox(width: 8),
                GestureDetector(
                  onTap: () {
                    final mins = int.tryParse(_customCtrl.text.trim()) ?? 0;
                    if (mins > 0) _pick(mins);
                  },
                  child: Container(
                    padding: const EdgeInsets.symmetric(
                        horizontal: 12, vertical: 8),
                    decoration: BoxDecoration(
                      color: AppColors.fill(AppColors.info),
                      borderRadius: BorderRadius.circular(8),
                      border: Border.all(color: AppColors.stroke(AppColors.info)),
                    ),
                    child: Text('Schedule',
                        style: AppTextStyles.meta.copyWith(
                            color: AppColors.info,
                            fontWeight: FontWeight.w600)),
                  ),
                ),
              ],
            ),
            const SizedBox(height: 20),
            Align(
              alignment: Alignment.centerRight,
              child: AppButton.ghost(
                  label: 'Cancel',
                  onPressed: () => Navigator.pop(context)),
            ),
          ],
        ),
      ),
    );
  }
}
