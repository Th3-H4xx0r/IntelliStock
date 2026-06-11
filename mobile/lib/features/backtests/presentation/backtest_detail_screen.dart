import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';
import 'package:syncfusion_flutter_charts/charts.dart';
import '../../../core/charts/scrubbable_area_chart.dart';
import '../../../core/formatters/formatters.dart';
import '../../../core/theme/app_colors.dart';
import '../../../core/theme/app_text_styles.dart';
import '../../../core/widgets/app_background.dart';
import '../../../core/widgets/common_widgets.dart';
import '../../../core/widgets/confirm_dialog.dart';
import '../../../core/widgets/glass_card.dart';
import '../../../core/widgets/material_symbols.dart';
import '../../../core/widgets/skeleton.dart';
import '../../../core/widgets/status_pill.dart';
import '../application/backtest_detail_controller.dart';
import '../data/models/backtest.dart';
import 'backtest_llm_pause_banner.dart';

class BacktestDetailScreen extends ConsumerStatefulWidget {
  const BacktestDetailScreen({super.key, required this.id});

  final String id;

  @override
  ConsumerState<BacktestDetailScreen> createState() =>
      _BacktestDetailScreenState();
}

class _BacktestDetailScreenState
    extends ConsumerState<BacktestDetailScreen> {
  final Set<String> _expandedStocks = {};
  final Map<String, int> _decisionLimits = {};
  bool _logsOpen = false;
  bool _strategyOpen = false;

  static const int _decisionPage = 5;

  @override
  Widget build(BuildContext context) {
    final state =
        ref.watch(backtestDetailControllerProvider(widget.id));
    final ctrl = ref.read(
        backtestDetailControllerProvider(widget.id).notifier);

    return Scaffold(
      backgroundColor: AppColors.canvas,
      body: AppBackground(
        child: SafeArea(
          child: CustomScrollView(
            slivers: [
              // ── Breadcrumb + back ───────────────────────────────────────────
              SliverToBoxAdapter(
                child: Padding(
                  padding: const EdgeInsets.fromLTRB(16, 16, 16, 0),
                  child: GestureDetector(
                    onTap: () => context.canPop()
                        ? context.pop()
                        : context.go('/backtests'),
                    child: Row(
                      children: [
                        Icon(symbol('arrow_back'),
                            color: AppColors.textDim, size: 16),
                        const SizedBox(width: 4),
                        Text('Back',
                            style: AppTextStyles.meta.copyWith(
                                color: AppColors.textDim)),
                        const SizedBox(width: 6),
                        Text('/',
                            style: AppTextStyles.meta.copyWith(
                                color: AppColors.textFaint)),
                        const SizedBox(width: 6),
                        Text('Backtest #${widget.id}',
                            style: AppTextStyles.meta.copyWith(
                                color: AppColors.textMuted)),
                      ],
                    ),
                  ),
                ),
              ),

              // ── Loading ─────────────────────────────────────────────────────
              if (state.loading)
                const SliverToBoxAdapter(
                  child: _BacktestDetailSkeleton(),
                ),

              // ── Error ───────────────────────────────────────────────────────
              if (!state.loading && state.error != null)
                SliverFillRemaining(
                  child: Padding(
                    padding: const EdgeInsets.all(16),
                    child: ErrorBanner(
                      message: state.error!,
                      onRetry: () => ref.invalidate(
                          backtestDetailControllerProvider(widget.id)),
                    ),
                  ),
                ),

              if (!state.loading && state.summary != null) ...[
                // ── Header ────────────────────────────────────────────────────
                SliverToBoxAdapter(
                  child: Padding(
                    padding: const EdgeInsets.fromLTRB(16, 16, 16, 0),
                    child: Column(
                      crossAxisAlignment: CrossAxisAlignment.start,
                      children: [
                        Row(
                          children: [
                            IconTile(
                                icon: symbol('analytics'),
                                color: AppColors.info,
                                size: 44),
                            const SizedBox(width: 12),
                            Expanded(
                              child: Column(
                                crossAxisAlignment:
                                    CrossAxisAlignment.start,
                                children: [
                                  Row(
                                    children: [
                                      Text(
                                        'Backtest #${widget.id}',
                                        style: AppTextStyles.h2,
                                      ),
                                      const SizedBox(width: 8),
                                      StatusPill(
                                        label: state.currentStatus
                                            .toUpperCase(),
                                        color: StatusPill.colorForStatus(
                                            state.currentStatus),
                                        pulsing: state.isActive,
                                      ),
                                    ],
                                  ),
                                  Text(
                                    '${state.summary!.startDate ?? '?'} → ${state.summary!.endDate ?? '?'}',
                                    style: AppTextStyles.meta.copyWith(
                                        color: AppColors.textDim),
                                  ),
                                  if (state.summary!.tickers.isNotEmpty)
                                    Text(
                                      state.summary!.tickers.join(', '),
                                      style: AppTextStyles.mono(11,
                                          color: AppColors.textDim),
                                    ),
                                ],
                              ),
                            ),
                          ],
                        ),
                        const SizedBox(height: 12),
                        // Progress bar (running)
                        if (state.progress != null && state.isActive)
                          Column(
                            children: [
                              Row(
                                mainAxisAlignment:
                                    MainAxisAlignment.spaceBetween,
                                children: [
                                  Text('Progress',
                                      style: AppTextStyles.meta.copyWith(
                                          color: AppColors.textDim)),
                                  Text(
                                    '${state.progress!.round()}%',
                                    style: AppTextStyles.mono(11,
                                        color: AppColors.textDim),
                                  ),
                                ],
                              ),
                              const SizedBox(height: 4),
                              ClipRRect(
                                borderRadius:
                                    BorderRadius.circular(4),
                                child: LinearProgressIndicator(
                                  value: (state.progress!.toDouble() /
                                          100)
                                      .clamp(0, 1),
                                  backgroundColor: AppColors.surface,
                                  valueColor:
                                      const AlwaysStoppedAnimation(
                                          AppColors.info),
                                  minHeight: 8,
                                ),
                              ),
                              const SizedBox(height: 12),
                            ],
                          ),
                        // Action cluster
                        _ActionCluster(
                          id: widget.id,
                          status: state.currentStatus,
                          onAction: (action) =>
                              _doAction(context, ctrl, action),
                          onPlayback: () => context.push(
                              '/backtests/${widget.id}/playback'),
                        ),
                      ],
                    ),
                  ),
                ),

                // ── LLM pause banner ─────────────────────────────────────────
                SliverToBoxAdapter(
                  child: Padding(
                    padding: const EdgeInsets.fromLTRB(16, 12, 16, 0),
                    child: BacktestLlmPauseBanner(
                        summary: state.summary!),
                  ),
                ),

                // ── Nexus lookback banner ─────────────────────────────────────
                if (state.nexusLookback != null)
                  SliverToBoxAdapter(
                    child: Padding(
                      padding:
                          const EdgeInsets.fromLTRB(16, 12, 16, 0),
                      child: _NexusLookbackBanner(
                          lookback: state.nexusLookback!),
                    ),
                  ),

                // ── Stat tiles (2-col) ────────────────────────────────────────
                SliverToBoxAdapter(
                  child: Padding(
                    padding:
                        const EdgeInsets.fromLTRB(16, 16, 16, 0),
                    child: _StatGrid(
                        summary: state.summary!,
                        elapsed: state.elapsedSeconds),
                  ),
                ),

                // ── AI Credits card ───────────────────────────────────────────
                SliverToBoxAdapter(
                  child: Padding(
                    padding:
                        const EdgeInsets.fromLTRB(16, 16, 16, 0),
                    child: _LlmCostCard(
                      llmCost: state.llmCost,
                      loading: state.llmCostLoading,
                      error: state.llmCostError,
                      onRefresh: ctrl.refreshLlmCost,
                    ),
                  ),
                ),

                // ── Strategy collapsible ──────────────────────────────────────
                if (state.summary!.strategySchema != null)
                  SliverToBoxAdapter(
                    child: Padding(
                      padding:
                          const EdgeInsets.fromLTRB(16, 16, 16, 0),
                      child: _StrategySection(
                        schema: state.summary!.strategySchema!,
                        strategyId: state.summary!.strategyId,
                        open: _strategyOpen,
                        onToggle: () => setState(
                            () => _strategyOpen = !_strategyOpen),
                      ),
                    ),
                  ),

                // ── Logs panel ────────────────────────────────────────────────
                SliverToBoxAdapter(
                  child: Padding(
                    padding:
                        const EdgeInsets.fromLTRB(16, 16, 16, 0),
                    child: _LogsPanel(
                      open: _logsOpen,
                      lines: state.logLines,
                      loading: state.logsLoading,
                      error: state.logsError,
                      source: state.logSource,
                      onToggle: () {
                        setState(() => _logsOpen = !_logsOpen);
                        if (!_logsOpen || state.logLines.isNotEmpty) {
                          return;
                        }
                        ctrl.loadLogs();
                      },
                    ),
                  ),
                ),

                // ── Portfolio chart ───────────────────────────────────────────
                if (state.graphData != null &&
                    state.graphData!.portfolioValueHistory.isNotEmpty)
                  SliverToBoxAdapter(
                    child: Padding(
                      padding:
                          const EdgeInsets.fromLTRB(16, 16, 16, 0),
                      child: _PortfolioChart(
                        history:
                            state.graphData!.portfolioValueHistory,
                        startValue: state.summary!.portfolioStartValue,
                      ),
                    ),
                  ),

                // ── P&L per stock cards ───────────────────────────────────────
                if (state.summary!.pnlPerStock != null &&
                    state.summary!.pnlPerStock!.isNotEmpty)
                  SliverToBoxAdapter(
                    child: Padding(
                      padding:
                          const EdgeInsets.fromLTRB(16, 16, 16, 0),
                      child: _PnlPerStockCard(
                          summary: state.summary!),
                    ),
                  ),

                // ── Per-ticker accordions ─────────────────────────────────────
                if (state.graphData != null &&
                    state.summary!.tickers.isNotEmpty)
                  SliverToBoxAdapter(
                    child: Padding(
                      padding:
                          const EdgeInsets.fromLTRB(16, 16, 16, 0),
                      child: Column(
                        crossAxisAlignment:
                            CrossAxisAlignment.start,
                        children: [
                          Row(
                            mainAxisAlignment:
                                MainAxisAlignment.spaceBetween,
                            children: [
                              Text(
                                'Stock Charts & Trades',
                                style: AppTextStyles.eyebrow,
                              ),
                              Row(
                                children: [
                                  TextButton(
                                    onPressed: () => setState(() {
                                      _expandedStocks.addAll(
                                          state.summary!.tickers);
                                    }),
                                    child: Text('Expand All',
                                        style: AppTextStyles.meta
                                            .copyWith(
                                                color: AppColors
                                                    .textMuted)),
                                  ),
                                  if (_expandedStocks.isNotEmpty)
                                    TextButton(
                                      onPressed: () => setState(() {
                                        _expandedStocks.clear();
                                        _decisionLimits.clear();
                                      }),
                                      child: Text('Collapse All',
                                          style:
                                              AppTextStyles.meta.copyWith(
                                                  color: AppColors
                                                      .textMuted)),
                                    ),
                                ],
                              ),
                            ],
                          ),
                          const SizedBox(height: 8),
                          ...state.summary!.tickers.map((sym) =>
                              _StockAccordion(
                                key: ValueKey(sym),
                                sym: sym,
                                summary: state.summary!,
                                graphData: state.graphData!,
                                expanded: _expandedStocks.contains(sym),
                                decisionLimit:
                                    _decisionLimits[sym] ??
                                        _decisionPage,
                                onToggle: () => setState(() {
                                  if (_expandedStocks.contains(sym)) {
                                    _expandedStocks.remove(sym);
                                  } else {
                                    _expandedStocks.add(sym);
                                  }
                                }),
                                onShowMoreDecisions: () =>
                                    setState(() {
                                  _decisionLimits[sym] =
                                      (_decisionLimits[sym] ??
                                              _decisionPage) +
                                          _decisionPage;
                                }),
                              )),
                        ],
                      ),
                    ),
                  ),

                // ── Round-trip stats ──────────────────────────────────────────
                SliverToBoxAdapter(
                  child: Padding(
                    padding:
                        const EdgeInsets.fromLTRB(16, 16, 16, 24),
                    child: _RoundTripStats(
                        summary: state.summary!),
                  ),
                ),
              ],

              // ── Pending / no summary ──────────────────────────────────────
              if (!state.loading && state.summary == null)
                SliverFillRemaining(
                  child: Center(
                    child: Column(
                      mainAxisSize: MainAxisSize.min,
                      children: [
                        Icon(symbol('hourglass_empty'),
                            color: AppColors.textFaint, size: 48),
                        const SizedBox(height: 12),
                        Text(
                          'This backtest has not completed yet.',
                          style: AppTextStyles.body.copyWith(
                              color: AppColors.textMuted),
                        ),
                        if (state.progress != null) ...[
                          const SizedBox(height: 16),
                          SizedBox(
                            width: 200,
                            child: Column(
                              children: [
                                Row(
                                  mainAxisAlignment:
                                      MainAxisAlignment.spaceBetween,
                                  children: [
                                    Text(state.currentStatus,
                                        style: AppTextStyles.meta
                                            .copyWith(
                                                color: AppColors
                                                    .textDim)),
                                    Text(
                                        '${state.progress!.round()}%',
                                        style: AppTextStyles.meta
                                            .copyWith(
                                                color: AppColors
                                                    .textDim)),
                                  ],
                                ),
                                const SizedBox(height: 4),
                                ClipRRect(
                                  borderRadius:
                                      BorderRadius.circular(4),
                                  child: LinearProgressIndicator(
                                    value: (state.progress!
                                                .toDouble() /
                                            100)
                                        .clamp(0, 1),
                                    backgroundColor: AppColors.surface,
                                    valueColor:
                                        const AlwaysStoppedAnimation(
                                            AppColors.info),
                                    minHeight: 8,
                                  ),
                                ),
                              ],
                            ),
                          ),
                        ],
                      ],
                    ),
                  ),
                ),
            ],
          ),
        ),
      ),
    );
  }

  Future<void> _doAction(
    BuildContext context,
    BacktestDetailController ctrl,
    String action,
  ) async {
    if (action == 'rerun') {
      await _doRerun(context, ctrl);
      return;
    }
    final meta = _actionMeta(action);
    await showConfirmDialog(
      context,
      title: meta.$1,
      body: '${meta.$2}\n\nBacktest #${widget.id}',
      confirmLabel: meta.$1.split(' ').last,
      confirmColor: meta.$3,
      icon: symbol(meta.$4),
      onConfirm: () async {
        final err = await ctrl.performAction(action);
        if (err != null) throw Exception(err);
        if (action == 'delete' && context.mounted) {
          context.canPop()
              ? context.pop()
              : context.go('/backtests');
        }
      },
    );
  }

  Future<void> _doRerun(
      BuildContext context, BacktestDetailController ctrl) async {
    await showConfirmDialog(
      context,
      title: 'Rerun Backtest',
      body:
          'A new backtest will be created with the same settings.\n\nBacktest #${widget.id}',
      confirmLabel: 'Rerun',
      confirmColor: AppColors.success,
      icon: symbol('replay'),
      onConfirm: () async {
        final data = await ctrl.rerun();
        final newId =
            (data['id'] ?? data['backtest_id'])?.toString();
        if (newId != null && context.mounted) {
          context.go('/backtests/$newId');
        }
      },
    );
  }

  static (String, String, Color, String) _actionMeta(String action) {
    switch (action) {
      case 'pause':
        return (
          'Pause Backtest',
          'The backtest will be paused and can be resumed later.',
          AppColors.primary,
          'pause_circle'
        );
      case 'resume':
        return (
          'Resume Backtest',
          'The backtest will continue from where it was paused.',
          AppColors.info,
          'play_circle'
        );
      case 'stop':
        return (
          'Stop Backtest',
          'This will permanently stop the backtest.',
          AppColors.danger,
          'stop_circle'
        );
      case 'delete':
      default:
        return (
          'Delete Backtest',
          'This will permanently delete all results and data.',
          AppColors.danger,
          'delete_forever'
        );
    }
  }
}

// ── Action cluster ────────────────────────────────────────────────────────────

class _ActionCluster extends StatelessWidget {
  const _ActionCluster({
    required this.id,
    required this.status,
    required this.onAction,
    required this.onPlayback,
  });

  final String id;
  final String status;
  final void Function(String) onAction;
  final VoidCallback onPlayback;

  bool get _isRunning => status.toLowerCase() == 'running';
  bool get _isPaused =>
      status.toLowerCase() == 'paused' ||
      status.toLowerCase() == 'paused_llm_critical';
  bool get _canStop =>
      _isRunning ||
      _isPaused ||
      status.toLowerCase() == 'queued';

  @override
  Widget build(BuildContext context) {
    return Wrap(
      spacing: 8,
      runSpacing: 8,
      children: [
        if (_isRunning)
          _ChipBtn(
              label: 'Pause',
              icon: symbol('pause_circle'),
              color: AppColors.primary,
              onTap: () => onAction('pause')),
        if (_isPaused)
          _ChipBtn(
              label: 'Resume',
              icon: symbol('play_circle'),
              color: AppColors.info,
              onTap: () => onAction('resume')),
        if (_canStop)
          _ChipBtn(
              label: 'Stop',
              icon: symbol('stop_circle'),
              color: AppColors.danger,
              onTap: () => onAction('stop')),
        _ChipBtn(
            label: 'Rerun',
            icon: symbol('replay'),
            color: AppColors.success,
            onTap: () => onAction('rerun')),
        _ChipBtn(
            label: 'Playback',
            icon: symbol('movie'),
            color: AppColors.warning,
            onTap: onPlayback),
        _ChipBtn(
            label: 'Delete',
            icon: symbol('delete_forever'),
            color: AppColors.danger,
            onTap: () => onAction('delete')),
      ],
    );
  }
}

class _ChipBtn extends StatelessWidget {
  const _ChipBtn({
    required this.label,
    required this.icon,
    required this.color,
    required this.onTap,
  });

  final String label;
  final IconData icon;
  final Color color;
  final VoidCallback onTap;

  @override
  Widget build(BuildContext context) => InkWell(
        onTap: onTap,
        borderRadius: BorderRadius.circular(8),
        child: Container(
          padding:
              const EdgeInsets.symmetric(horizontal: 10, vertical: 6),
          decoration: BoxDecoration(
            color: AppColors.fill(color),
            borderRadius: BorderRadius.circular(8),
            border: Border.all(color: AppColors.stroke(color)),
          ),
          child: Row(
            mainAxisSize: MainAxisSize.min,
            children: [
              Icon(icon, color: color, size: 14),
              const SizedBox(width: 4),
              Text(label,
                  style: AppTextStyles.micro
                      .copyWith(color: color, fontWeight: FontWeight.w600)),
            ],
          ),
        ),
      );
}

// ── Nexus lookback banner ─────────────────────────────────────────────────────

class _NexusLookbackBanner extends StatelessWidget {
  const _NexusLookbackBanner({required this.lookback});

  final NexusLookback lookback;

  @override
  Widget build(BuildContext context) => Container(
        padding: const EdgeInsets.all(16),
        decoration: BoxDecoration(
          color: AppColors.fill(AppColors.primary),
          borderRadius: BorderRadius.circular(16),
          border: Border.all(color: AppColors.stroke(AppColors.primary)),
        ),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Row(
              children: [
                Icon(symbol('hub'), color: AppColors.primary, size: 18),
                const SizedBox(width: 8),
                Text('Nexus Lookback Training',
                    style: AppTextStyles.bodyHi
                        .copyWith(color: AppColors.primary)),
                const Spacer(),
                Text(
                  'Day ${lookback.current} / ${lookback.total}',
                  style: AppTextStyles.mono(11,
                      color: AppColors.textDim),
                ),
              ],
            ),
            const SizedBox(height: 10),
            ClipRRect(
              borderRadius: BorderRadius.circular(4),
              child: LinearProgressIndicator(
                value: lookback.fraction.clamp(0, 1),
                backgroundColor: AppColors.surface,
                valueColor:
                    AlwaysStoppedAnimation(AppColors.primary),
                minHeight: 8,
              ),
            ),
            const SizedBox(height: 6),
            Row(
              mainAxisAlignment: MainAxisAlignment.spaceBetween,
              children: [
                Text(lookback.startDate ?? '',
                    style: AppTextStyles.nano
                        .copyWith(color: AppColors.textDim)),
                Text(lookback.currentDate ?? '',
                    style: AppTextStyles.nano
                        .copyWith(color: AppColors.primary)),
                Text(lookback.endDate ?? '',
                    style: AppTextStyles.nano
                        .copyWith(color: AppColors.textDim)),
              ],
            ),
            const SizedBox(height: 6),
            Text(
              'Building historical event context before trading begins. '
              'This runs once per scope.',
              style: AppTextStyles.nano
                  .copyWith(color: AppColors.textDim),
            ),
          ],
        ),
      );
}

// ── Stat grid ─────────────────────────────────────────────────────────────────

class _StatGrid extends StatelessWidget {
  const _StatGrid({required this.summary, this.elapsed});

  final BacktestSummary summary;
  final num? elapsed;

  @override
  Widget build(BuildContext context) {
    final tiles = [
      (
        'Total P&L',
        fmtPnl(summary.pnl),
        pnlColor(summary.pnl),
        null
      ),
      (
        'P&L %',
        fmtPct(summary.pnlPercent),
        pnlColor(summary.pnlPercent),
        null
      ),
      (
        'Portfolio',
        fmtMoney(summary.portfolioEndValue),
        pnlColor((summary.portfolioEndValue?.toDouble() ?? 0) -
            (summary.portfolioStartValue?.toDouble() ?? 0)),
        'From ${fmtMoney(summary.portfolioStartValue)}'
      ),
      (
        'Trades',
        '${summary.totalTrades ?? '—'}',
        AppColors.textHi,
        '${summary.totalBuys ?? 0} buy / ${summary.totalSells ?? 0} sell'
      ),
      (
        'Elapsed',
        fmtElapsed(elapsed),
        AppColors.textHi,
        null
      ),
      (
        'Win Rate',
        summary.winRatePercent != null
            ? '${summary.winRatePercent!.toStringAsFixed(1)}%'
            : '—',
        (summary.winRatePercent ?? 0) >= 50
            ? AppColors.success
            : AppColors.danger,
        '${summary.winningRoundTrips ?? 0}W / ${summary.losingRoundTrips ?? 0}L'
      ),
      (
        'Portfolio High',
        fmtMoney(summary.portfolioValueHigh),
        AppColors.success,
        'Low: ${fmtMoney(summary.portfolioValueLow)}'
      ),
    ];

    return GridView.builder(
      shrinkWrap: true,
      physics: const NeverScrollableScrollPhysics(),
      gridDelegate: const SliverGridDelegateWithFixedCrossAxisCount(
        crossAxisCount: 2,
        crossAxisSpacing: 10,
        mainAxisSpacing: 10,
        childAspectRatio: 2.2,
      ),
      itemCount: tiles.length,
      itemBuilder: (_, i) => StatTile(
        label: tiles[i].$1,
        value: tiles[i].$2,
        valueColor: tiles[i].$3,
        sub: tiles[i].$4,
      ),
    );
  }
}

// ── LLM Cost card ─────────────────────────────────────────────────────────────

class _LlmCostCard extends StatelessWidget {
  const _LlmCostCard({
    this.llmCost,
    required this.loading,
    this.error,
    required this.onRefresh,
  });

  final LlmCost? llmCost;
  final bool loading;
  final String? error;
  final VoidCallback onRefresh;

  @override
  Widget build(BuildContext context) {
    return GlassCard(
      padding: EdgeInsets.zero,
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          // Header
          Padding(
            padding: const EdgeInsets.fromLTRB(16, 14, 12, 14),
            child: Row(
              children: [
                IconTile(
                    icon: symbol('payments'),
                    color: AppColors.primary,
                    size: 32),
                const SizedBox(width: 10),
                Expanded(
                  child: Column(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      Text('AI CREDITS',
                          style: AppTextStyles.eyebrow),
                      Text('LLM cost for this backtest',
                          style: AppTextStyles.meta),
                    ],
                  ),
                ),
                loading
                    ? const SizedBox(
                        width: 16,
                        height: 16,
                        child: CircularProgressIndicator(strokeWidth: 2))
                    : IconButton(
                        icon: Icon(symbol('refresh'),
                            size: 18, color: AppColors.textMuted),
                        onPressed: onRefresh,
                        padding: EdgeInsets.zero,
                        constraints: const BoxConstraints(),
                      ),
              ],
            ),
          ),
          const Divider(color: AppColors.border, height: 1),

          if (error != null)
            Padding(
              padding: const EdgeInsets.all(16),
              child: Text(error!,
                  style: AppTextStyles.meta
                      .copyWith(color: AppColors.danger)),
            )
          else if (llmCost == null && loading)
            const Padding(
              padding: EdgeInsets.all(16),
              child: _LlmCostSkeleton(),
            )
          else if (llmCost == null || (llmCost!.totalCalls ?? 0) == 0)
            Padding(
              padding: const EdgeInsets.all(16),
              child: Text(
                'No LLM calls were attributed to this backtest.',
                style: AppTextStyles.meta
                    .copyWith(color: AppColors.textDim),
              ),
            )
          else ...[
            // Headline totals — 2x2 grid so labels/values aren't cramped.
            Padding(
              padding: const EdgeInsets.all(16),
              child: Column(
                children: [
                  Row(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      Expanded(
                          child: _CostTile(
                              label: 'Total cost',
                              value: fmtUsdCost(llmCost!.totalCostUsd))),
                      const SizedBox(width: 12),
                      Expanded(
                          child: _CostTile(
                              label: 'Calls',
                              value: '${llmCost!.totalCalls ?? 0}',
                              sub:
                                  '${llmCost!.okCalls ?? 0} ok · ${llmCost!.failedCalls ?? 0} failed')),
                    ],
                  ),
                  const SizedBox(height: 16),
                  Row(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      Expanded(
                          child: _CostTile(
                              label: 'Input tokens',
                              value: fmtTokens(llmCost!.totalInputTokens))),
                      const SizedBox(width: 12),
                      Expanded(
                          child: _CostTile(
                              label: 'Output tokens',
                              value:
                                  fmtTokens(llmCost!.totalOutputTokens))),
                    ],
                  ),
                ],
              ),
            ),
            const Divider(color: AppColors.border, height: 1),
            // Breakdown rows
            Padding(
              padding: const EdgeInsets.all(16),
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  if (llmCost!.byModel.isNotEmpty) ...[
                    Text('By model',
                        style: AppTextStyles.eyebrow),
                    const SizedBox(height: 6),
                    ...llmCost!.byModel.take(6).map(
                          (r) => _CostRow(
                              rowKey: r.key, value: r.costUsd),
                        ),
                    const SizedBox(height: 12),
                  ],
                  if (llmCost!.byCallSite.isNotEmpty) ...[
                    Text('By call site',
                        style: AppTextStyles.eyebrow),
                    const SizedBox(height: 6),
                    ...llmCost!.byCallSite.take(6).map(
                          (r) => _CostRow(
                              rowKey: r.key, value: r.costUsd),
                        ),
                    const SizedBox(height: 12),
                  ],
                  if (llmCost!.byProvider.isNotEmpty) ...[
                    Text('By provider',
                        style: AppTextStyles.eyebrow),
                    const SizedBox(height: 6),
                    ...llmCost!.byProvider.take(6).map(
                          (r) => _CostRow(
                              rowKey: r.key, value: r.costUsd),
                        ),
                  ],
                ],
              ),
            ),
          ],
        ],
      ),
    );
  }
}

class _CostTile extends StatelessWidget {
  const _CostTile({required this.label, required this.value, this.sub});

  final String label;
  final String value;
  final String? sub;

  @override
  Widget build(BuildContext context) => Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Text(label, style: AppTextStyles.eyebrow),
          const SizedBox(height: 2),
          Text(value,
              style: AppTextStyles.valueLg
                  .copyWith(color: AppColors.textHi)),
          if (sub != null)
            Text(sub!,
                style: AppTextStyles.nano
                    .copyWith(color: AppColors.textDim)),
        ],
      );
}

class _CostRow extends StatelessWidget {
  const _CostRow({required this.rowKey, this.value});

  final String rowKey;
  final num? value;

  @override
  Widget build(BuildContext context) => Padding(
        padding: const EdgeInsets.only(bottom: 4),
        child: Row(
          children: [
            Expanded(
                child: Text(rowKey,
                    style: AppTextStyles.mono(11,
                        color: AppColors.textMuted),
                    overflow: TextOverflow.ellipsis)),
            Text(fmtUsdCost(value),
                style: AppTextStyles.mono(11,
                    color: AppColors.textHi)),
          ],
        ),
      );
}

// ── Strategy section ──────────────────────────────────────────────────────────

class _StrategySection extends StatelessWidget {
  const _StrategySection({
    required this.schema,
    this.strategyId,
    required this.open,
    required this.onToggle,
  });

  final StrategySchema schema;
  final String? strategyId;
  final bool open;
  final VoidCallback onToggle;

  @override
  Widget build(BuildContext context) {
    return GlassCard(
      padding: EdgeInsets.zero,
      child: Column(
        children: [
          InkWell(
            onTap: onToggle,
            borderRadius: BorderRadius.circular(16),
            child: Padding(
              padding: const EdgeInsets.all(16),
              child: Row(
                children: [
                  IconTile(
                      icon: symbol('schema'),
                      color: AppColors.primary,
                      size: 32),
                  const SizedBox(width: 10),
                  Expanded(
                    child: Column(
                      crossAxisAlignment: CrossAxisAlignment.start,
                      children: [
                        Text('STRATEGY', style: AppTextStyles.eyebrow),
                        Text(schema.name ?? '—',
                            style: AppTextStyles.cardTitle),
                      ],
                    ),
                  ),
                  AnimatedRotation(
                    turns: open ? 0.5 : 0,
                    duration: const Duration(milliseconds: 200),
                    child: Icon(symbol('expand_more'),
                        color: AppColors.textDim),
                  ),
                ],
              ),
            ),
          ),
          if (open) ...[
            const Divider(color: AppColors.border, height: 1),
            Padding(
              padding: const EdgeInsets.all(16),
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  if (strategyId != null)
                    Padding(
                      padding: const EdgeInsets.only(bottom: 8),
                      child: Text('ID: $strategyId',
                          style: AppTextStyles.nano
                              .copyWith(color: AppColors.textFaint)),
                    ),
                  Text('Sub-strategies',
                      style: AppTextStyles.eyebrow),
                  const SizedBox(height: 8),
                  ...schema.strategies.asMap().entries.map(
                        (e) => _SubStrategyCard(
                            index: e.key, sub: e.value),
                      ),
                  if (schema.strategies.isEmpty)
                    Text('No sub-strategies defined',
                        style: AppTextStyles.meta
                            .copyWith(color: AppColors.textDim)),
                ],
              ),
            ),
          ],
        ],
      ),
    );
  }
}

class _SubStrategyCard extends StatelessWidget {
  const _SubStrategyCard(
      {required this.index, required this.sub});

  final int index;
  final SubStrategy sub;

  @override
  Widget build(BuildContext context) {
    final combined = {
      ...sub.conditions,
      ...sub.config,
    };
    return Container(
      margin: const EdgeInsets.only(bottom: 8),
      padding: const EdgeInsets.all(12),
      decoration: BoxDecoration(
        color: AppColors.surface.withValues(alpha: 0.4),
        borderRadius: BorderRadius.circular(12),
        border: Border.all(color: AppColors.border),
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Row(
            children: [
              Container(
                width: 20,
                height: 20,
                alignment: Alignment.center,
                decoration: BoxDecoration(
                  color: AppColors.fill(AppColors.primary),
                  borderRadius: BorderRadius.circular(4),
                  border: Border.all(
                      color: AppColors.stroke(AppColors.primary)),
                ),
                child: Text('${index + 1}',
                    style: AppTextStyles.nano.copyWith(
                        color: AppColors.primary,
                        fontWeight: FontWeight.w700)),
              ),
              const SizedBox(width: 8),
              Expanded(
                  child: Text(sub.strategy ?? '?',
                      style: AppTextStyles.bodyHi)),
              if (sub.weight != null)
                _Chip(
                    label:
                        '${(sub.weight! * 100).toStringAsFixed(0)}%'),
              if (sub.decisionPhase != null)
                _Chip(label: sub.decisionPhase!),
            ],
          ),
          if (combined.isNotEmpty) ...[
            const SizedBox(height: 8),
            Wrap(
              spacing: 6,
              runSpacing: 4,
              children: combined.entries
                  .map((e) => Container(
                        padding:
                            const EdgeInsets.symmetric(
                                horizontal: 8, vertical: 4),
                        decoration: BoxDecoration(
                          color: AppColors.canvas,
                          borderRadius: BorderRadius.circular(6),
                          border:
                              Border.all(color: AppColors.border),
                        ),
                        child: Column(
                          crossAxisAlignment:
                              CrossAxisAlignment.start,
                          children: [
                            Text(e.key,
                                style: AppTextStyles.nano.copyWith(
                                    color: AppColors.textFaint)),
                            Text('${e.value ?? 'null'}',
                                style: AppTextStyles.mono(11,
                                    color: AppColors.textMuted)),
                          ],
                        ),
                      ))
                  .toList(),
            ),
          ],
        ],
      ),
    );
  }
}

class _Chip extends StatelessWidget {
  const _Chip({required this.label});

  final String label;

  @override
  Widget build(BuildContext context) => Container(
        margin: const EdgeInsets.only(left: 4),
        padding:
            const EdgeInsets.symmetric(horizontal: 6, vertical: 2),
        decoration: BoxDecoration(
          color: AppColors.surface,
          borderRadius: BorderRadius.circular(4),
          border: Border.all(color: AppColors.border),
        ),
        child: Text(label,
            style: AppTextStyles.mono(10,
                color: AppColors.textMuted)),
      );
}

// ── Logs panel ────────────────────────────────────────────────────────────────

class _LogsPanel extends StatelessWidget {
  const _LogsPanel({
    required this.open,
    required this.lines,
    required this.loading,
    this.error,
    required this.source,
    required this.onToggle,
  });

  final bool open;
  final List<String> lines;
  final bool loading;
  final String? error;
  final String source;
  final VoidCallback onToggle;

  @override
  Widget build(BuildContext context) {
    return GlassCard(
      padding: EdgeInsets.zero,
      borderColor: AppColors.panelAlt,
      child: Column(
        children: [
          // Header bar
          Container(
            padding: const EdgeInsets.symmetric(
                horizontal: 12, vertical: 10),
            decoration: BoxDecoration(
              color: AppColors.panelAlt,
              borderRadius: open
                  ? const BorderRadius.vertical(
                      top: Radius.circular(16))
                  : BorderRadius.circular(16),
            ),
            child: Row(
              children: [
                Container(
                  width: 8,
                  height: 8,
                  decoration: BoxDecoration(
                    color: open
                        ? AppColors.success
                        : AppColors.textFaint,
                    shape: BoxShape.circle,
                  ),
                ),
                const SizedBox(width: 8),
                Text('backtest-${lines.hashCode}.log',
                    style: AppTextStyles.mono(11,
                        color: AppColors.textMd)),
                if (lines.isNotEmpty) ...[
                  const SizedBox(width: 6),
                  Text('${lines.length} lines',
                      style: AppTextStyles.nano
                          .copyWith(color: AppColors.textFaint)),
                ],
                if (source == 'db')
                  Padding(
                    padding: const EdgeInsets.only(left: 4),
                    child: Text('(last 500)',
                        style: AppTextStyles.nano.copyWith(
                            color: AppColors.warning
                                .withValues(alpha: 0.7))),
                  ),
                const Spacer(),
                InkWell(
                  onTap: onToggle,
                  borderRadius: BorderRadius.circular(6),
                  child: Container(
                    padding: const EdgeInsets.symmetric(
                        horizontal: 8, vertical: 4),
                    decoration: BoxDecoration(
                      color: open
                          ? AppColors.fill(AppColors.info)
                          : AppColors.fill(AppColors.border),
                      borderRadius: BorderRadius.circular(6),
                      border: Border.all(
                          color: open
                              ? AppColors.stroke(AppColors.info)
                              : AppColors.border),
                    ),
                    child: Text(
                      open ? 'Hide Logs' : 'View Logs',
                      style: AppTextStyles.micro.copyWith(
                          color: open
                              ? AppColors.info
                              : AppColors.textMuted,
                          fontWeight: FontWeight.w600),
                    ),
                  ),
                ),
              ],
            ),
          ),
          if (open)
            Container(
              constraints:
                  const BoxConstraints(maxHeight: 400),
              child: loading
                  ? const Padding(
                      padding: EdgeInsets.all(16),
                      child: LoadingState(label: 'Loading logs…'))
                  : error != null
                      ? Padding(
                          padding: const EdgeInsets.all(12),
                          child: Text(error!,
                              style: AppTextStyles.meta.copyWith(
                                  color: AppColors.danger)))
                      : lines.isEmpty
                          ? Padding(
                              padding: const EdgeInsets.all(12),
                              child: Text(
                                  'No logs available.',
                                  style: AppTextStyles.meta
                                      .copyWith(
                                          color:
                                              AppColors.textDim)))
                          : ListView.builder(
                              padding: const EdgeInsets.all(8),
                              itemCount: lines.length,
                              itemBuilder: (_, i) =>
                                  _LogLine(raw: lines[i]),
                            ),
            ),
        ],
      ),
    );
  }
}

class _LogLine extends StatelessWidget {
  const _LogLine({required this.raw});

  final String raw;

  @override
  Widget build(BuildContext context) {
    final color = _levelColor(raw);
    return Padding(
      padding: const EdgeInsets.symmetric(vertical: 1),
      child: Text(
        raw,
        style: AppTextStyles.mono(10, color: color),
      ),
    );
  }

  static Color _levelColor(String line) {
    final l = line.toLowerCase();
    if (l.contains('error') ||
        l.contains('fail') ||
        l.contains('exception')) {
      return AppColors.danger;
    }
    if (l.contains('warn') ||
        l.contains('retry') ||
        l.contains('skip')) {
      return AppColors.warning;
    }
    if (l.contains('success') ||
        l.contains('completed') ||
        l.contains('profit') ||
        l.contains('passed')) {
      return AppColors.success;
    }
    if (l.contains('broker')) return AppColors.info;
    return AppColors.textMuted;
  }
}

// ── Portfolio chart ───────────────────────────────────────────────────────────

class _PortfolioChart extends StatefulWidget {
  const _PortfolioChart(
      {required this.history, this.startValue});

  final List<PortfolioValuePoint> history;
  final num? startValue;

  @override
  State<_PortfolioChart> createState() => _PortfolioChartState();
}

class _PortfolioChartState extends State<_PortfolioChart> {
  // Scrub index drives only the header value (via this notifier), so scrubbing
  // never rebuilds the chart.
  final ValueNotifier<int?> _scrubIdx = ValueNotifier(null);

  @override
  void dispose() {
    _scrubIdx.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    final history = widget.history;
    final timestamps = [for (final p in history) p.timestamp];
    final values = [for (final p in history) p.value.toDouble()];
    final startVal = widget.startValue?.toDouble() ??
        (values.isNotEmpty ? values.first : 0.0);
    final isUp = values.isNotEmpty && values.last >= startVal;
    final lineColor = isUp ? AppColors.success : AppColors.danger;

    return GlassCard(
      padding: const EdgeInsets.fromLTRB(16, 14, 16, 12),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Text('Portfolio Value Over Time', style: AppTextStyles.eyebrow),
          const SizedBox(height: 4),
          ValueListenableBuilder<int?>(
            valueListenable: _scrubIdx,
            builder: (_, idx, _) {
              final i = (idx != null && idx >= 0 && idx < values.length)
                  ? idx
                  : null;
              final displayValue = i != null
                  ? values[i]
                  : (values.isNotEmpty ? values.last : 0.0);
              final pnl = displayValue - startVal;
              return Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Row(
                    crossAxisAlignment: CrossAxisAlignment.baseline,
                    textBaseline: TextBaseline.alphabetic,
                    children: [
                      Text(
                        fmtMoney(displayValue),
                        style: AppTextStyles.valueXl
                            .copyWith(color: AppColors.textHi),
                      ),
                      const SizedBox(width: 8),
                      Text(
                        fmtPnl(pnl),
                        style: AppTextStyles.value
                            .copyWith(color: pnlColor(pnl)),
                      ),
                      const SizedBox(width: 4),
                      Text('vs start',
                          style: AppTextStyles.meta
                              .copyWith(color: AppColors.textDim)),
                    ],
                  ),
                  SizedBox(
                    height: 16,
                    child: i != null
                        ? Text(fmtDateTime(timestamps[i]),
                            style: AppTextStyles.meta
                                .copyWith(color: AppColors.textDim))
                        : null,
                  ),
                ],
              );
            },
          ),
          const SizedBox(height: 8),
          ScrubbableAreaChart(
            timestamps: timestamps,
            values: values,
            lineColor: lineColor,
            height: 240,
            baseline: widget.startValue?.toDouble(),
            onScrub: (i) => _scrubIdx.value = i,
          ),
        ],
      ),
    );
  }
}

// ── P&L per stock card ────────────────────────────────────────────────────────

class _PnlPerStockCard extends StatelessWidget {
  const _PnlPerStockCard({required this.summary});

  final BacktestSummary summary;

  @override
  Widget build(BuildContext context) {
    return GlassCard(
      padding: EdgeInsets.zero,
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Padding(
            padding: const EdgeInsets.all(16),
            child: Text('P&L per Stock',
                style: AppTextStyles.eyebrow),
          ),
          const Divider(color: AppColors.border, height: 1),
          ...summary.tickers.map((sym) {
            final p = summary.pnlPerStock?[sym];
            final pp = summary.pnlPercentPerStock?[sym];
            final pc = summary.stockPriceChange?[sym];
            return Padding(
              padding: const EdgeInsets.symmetric(
                  horizontal: 16, vertical: 10),
              child: Row(
                children: [
                  Text(sym,
                      style: AppTextStyles.mono(12,
                          color: AppColors.textHi,
                          weight: FontWeight.w700)),
                  const Spacer(),
                  Text(fmtPnl(p),
                      style: AppTextStyles.mono(12,
                          color: pnlColor(p),
                          weight: FontWeight.w600)),
                  const SizedBox(width: 12),
                  Text(fmtPct(pp),
                      style: AppTextStyles.mono(11,
                          color: pnlColor(pp))),
                  const SizedBox(width: 12),
                  Text(fmtPct(pc),
                      style: AppTextStyles.mono(11,
                          color: pnlColor(pc))),
                ],
              ),
            );
          }).expand((w) => [
                w,
                const Divider(color: AppColors.border, height: 1)
              ]),
        ],
      ),
    );
  }
}

// ── Stock accordion ───────────────────────────────────────────────────────────

class _StockAccordion extends StatelessWidget {
  const _StockAccordion({
    super.key,
    required this.sym,
    required this.summary,
    required this.graphData,
    required this.expanded,
    required this.decisionLimit,
    required this.onToggle,
    required this.onShowMoreDecisions,
  });

  final String sym;
  final BacktestSummary summary;
  final BacktestGraphData graphData;
  final bool expanded;
  final int decisionLimit;
  final VoidCallback onToggle;
  final VoidCallback onShowMoreDecisions;

  List<BacktestTrade> get _trades => graphData.backtestTrades
      .where((t) => t.ticker == sym)
      .toList()
    ..sort((a, b) =>
        (a.timestamp ?? DateTime(0))
            .compareTo(b.timestamp ?? DateTime(0)));

  List<BacktestDecision> get _decisions => graphData.backtestDecisions
      .where((d) => d.symbol == sym)
      .toList()
    ..sort((a, b) =>
        (b.timestamp ?? DateTime(0))
            .compareTo(a.timestamp ?? DateTime(0)));

  List<PortfolioValuePoint> get _prices => graphData.backtestPrices
      .where((p) => p.symbol == sym)
      .map((p) => PortfolioValuePoint(
          timestamp: p.timestamp, value: p.close))
      .toList()
    ..sort((a, b) => a.timestamp.compareTo(b.timestamp));

  List<BacktestTrade> get _buys =>
      _trades.where((t) => (t.action ?? '').toLowerCase().contains('buy')).toList();

  List<BacktestTrade> get _sells =>
      _trades.where((t) => (t.action ?? '').toLowerCase().contains('sell')).toList();

  @override
  Widget build(BuildContext context) {
    final pnl = summary.pnlPerStock?[sym];
    final pnlPct = summary.pnlPercentPerStock?[sym];

    return Container(
      margin: const EdgeInsets.only(bottom: 8),
      decoration: BoxDecoration(
        color: AppColors.surface.withValues(alpha: 0.3),
        borderRadius: BorderRadius.circular(16),
        border: Border.all(color: AppColors.border),
      ),
      child: Column(
        children: [
          // Clickable header
          InkWell(
            onTap: onToggle,
            borderRadius: BorderRadius.circular(16),
            child: Padding(
              padding: const EdgeInsets.all(14),
              child: Row(
                children: [
                  Text(sym,
                      style: AppTextStyles.mono(13,
                          color: AppColors.textHi,
                          weight: FontWeight.w700)),
                  const SizedBox(width: 8),
                  Text(
                    '${fmtPnl(pnl)} (${fmtPct(pnlPct)})',
                    style: AppTextStyles.meta
                        .copyWith(color: pnlColor(pnl)),
                  ),
                  const SizedBox(width: 8),
                  Text('${_trades.length} trades',
                      style: AppTextStyles.nano.copyWith(
                          color: AppColors.textFaint)),
                  const Spacer(),
                  AnimatedRotation(
                    turns: expanded ? 0.5 : 0,
                    duration:
                        const Duration(milliseconds: 200),
                    child: Icon(symbol('expand_more'),
                        color: AppColors.textDim, size: 18),
                  ),
                ],
              ),
            ),
          ),
          if (expanded) ...[
            const Divider(color: AppColors.border, height: 1),
            // Price chart
            if (_prices.isNotEmpty)
              Padding(
                padding: const EdgeInsets.all(12),
                child: _StockChart(
                  sym: sym,
                  prices: _prices,
                  buys: _buys,
                  sells: _sells,
                ),
              ),
            // Trade table
            if (_trades.isNotEmpty) _TradeTable(trades: _trades),
            // Decision trace
            if (_decisions.isNotEmpty)
              _DecisionTrace(
                sym: sym,
                decisions: _decisions,
                limit: decisionLimit,
                onShowMore: onShowMoreDecisions,
              ),
          ],
        ],
      ),
    );
  }
}

class _StockChart extends StatelessWidget {
  const _StockChart({
    required this.sym,
    required this.prices,
    required this.buys,
    required this.sells,
  });

  final String sym;
  final List<PortfolioValuePoint> prices;
  final List<BacktestTrade> buys;
  final List<BacktestTrade> sells;

  @override
  Widget build(BuildContext context) {
    if (prices.isEmpty) return const SizedBox(height: 220);
    final timestamps = [for (final p in prices) p.timestamp];
    final values = [for (final p in prices) p.value.toDouble()];

    return ScrubbableAreaChart(
      timestamps: timestamps,
      values: values,
      // Distinct line colour so it doesn't read as a buy/sell marker.
      lineColor: AppColors.chartLineAlt,
      height: 220,
      markerSeries: () => <CartesianSeries>[
        ScatterSeries<BacktestTrade, DateTime>(
          name: 'Buy',
          dataSource: buys,
          xValueMapper: (t, _) => t.timestamp ?? DateTime.now(),
          yValueMapper: (t, _) => t.price?.toDouble() ?? 0,
          color: AppColors.success,
          markerSettings:
              const MarkerSettings(isVisible: true, height: 8, width: 8),
        ),
        ScatterSeries<BacktestTrade, DateTime>(
          name: 'Sell',
          dataSource: sells,
          xValueMapper: (t, _) => t.timestamp ?? DateTime.now(),
          yValueMapper: (t, _) => t.price?.toDouble() ?? 0,
          color: AppColors.danger,
          markerSettings:
              const MarkerSettings(isVisible: true, height: 8, width: 8),
        ),
      ],
    );
  }
}

class _TradeTable extends StatelessWidget {
  const _TradeTable({required this.trades});

  final List<BacktestTrade> trades;

  @override
  Widget build(BuildContext context) {
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        const Divider(color: AppColors.border, height: 1),
        Padding(
          padding: const EdgeInsets.symmetric(
              horizontal: 12, vertical: 8),
          child: SingleChildScrollView(
            scrollDirection: Axis.horizontal,
            child: DataTable(
              headingRowHeight: 32,
              dataRowMinHeight: 30,
              dataRowMaxHeight: 40,
              horizontalMargin: 0,
              columnSpacing: 16,
              headingTextStyle: AppTextStyles.nano
                  .copyWith(color: AppColors.textFaint),
              dataTextStyle: AppTextStyles.mono(11,
                  color: AppColors.textMuted),
              columns: const [
                DataColumn(label: Text('Time')),
                DataColumn(label: Text('Action')),
                DataColumn(label: Text('Shares'),
                    numeric: true),
                DataColumn(label: Text('Price'), numeric: true),
                DataColumn(label: Text('Total'), numeric: true),
                DataColumn(label: Text('Cash After'),
                    numeric: true),
              ],
              rows: trades
                  .map(
                    (t) => DataRow(cells: [
                      DataCell(Text(fmtDateTime(t.timestamp))),
                      DataCell(_ActionBadge(
                          action: t.action ?? '')),
                      DataCell(Text(t.shares != null
                          ? t.shares!
                              .toStringAsFixed(4)
                          : '—')),
                      DataCell(Text(fmtMoney(t.price))),
                      DataCell(Text(
                        t.total != null
                            ? fmtMoney(t.total!.abs())
                            : '—',
                        style: AppTextStyles.mono(11,
                            color: (t.action ?? '')
                                    .toLowerCase()
                                    .contains('buy')
                                ? AppColors.danger
                                : AppColors.success),
                      )),
                      DataCell(Text(fmtMoney(t.cashAfter))),
                    ]),
                  )
                  .toList(),
            ),
          ),
        ),
      ],
    );
  }
}

class _ActionBadge extends StatelessWidget {
  const _ActionBadge({required this.action});

  final String action;

  @override
  Widget build(BuildContext context) {
    final isBuy = action.toLowerCase().contains('buy');
    final c = isBuy ? AppColors.success : AppColors.danger;
    return Container(
      padding:
          const EdgeInsets.symmetric(horizontal: 6, vertical: 2),
      decoration: BoxDecoration(
        color: AppColors.fill(c),
        borderRadius: BorderRadius.circular(4),
        border: Border.all(color: AppColors.stroke(c)),
      ),
      child: Text(
        action.toUpperCase(),
        style: AppTextStyles.nano
            .copyWith(color: c, fontWeight: FontWeight.w700),
      ),
    );
  }
}

class _DecisionTrace extends StatelessWidget {
  const _DecisionTrace({
    required this.sym,
    required this.decisions,
    required this.limit,
    required this.onShowMore,
  });

  final String sym;
  final List<BacktestDecision> decisions;
  final int limit;
  final VoidCallback onShowMore;

  @override
  Widget build(BuildContext context) {
    final visible = decisions.take(limit).toList();
    final remaining = decisions.length - visible.length;

    return Padding(
      padding: const EdgeInsets.all(12),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Row(
            mainAxisAlignment: MainAxisAlignment.spaceBetween,
            children: [
              Text('Decision Trace',
                  style: AppTextStyles.eyebrow),
              Text('${decisions.length} evaluations',
                  style: AppTextStyles.nano
                      .copyWith(color: AppColors.textDim)),
            ],
          ),
          const SizedBox(height: 8),
          ...visible.map((d) => _DecisionCard(d: d)),
          if (remaining > 0)
            InkWell(
              onTap: onShowMore,
              borderRadius: BorderRadius.circular(10),
              child: Container(
                width: double.infinity,
                padding:
                    const EdgeInsets.symmetric(vertical: 12),
                decoration: BoxDecoration(
                  color: AppColors.surface.withValues(alpha: 0.4),
                  borderRadius:
                      BorderRadius.circular(10),
                  border: Border.all(color: AppColors.border),
                ),
                alignment: Alignment.center,
                child: Text(
                  'Show more ($remaining remaining)',
                  style: AppTextStyles.meta
                      .copyWith(color: AppColors.textMuted),
                ),
              ),
            ),
        ],
      ),
    );
  }
}

class _DecisionCard extends StatelessWidget {
  const _DecisionCard({required this.d});

  final BacktestDecision d;

  @override
  Widget build(BuildContext context) {
    final label = d.decisionLabel();
    final labelColor = label == 'BUY'
        ? AppColors.success
        : label == 'SELL'
            ? AppColors.danger
            : AppColors.textMuted;

    return Container(
      margin: const EdgeInsets.only(bottom: 8),
      padding: const EdgeInsets.all(12),
      decoration: BoxDecoration(
        color: AppColors.surface.withValues(alpha: 0.3),
        borderRadius: BorderRadius.circular(12),
        border: Border.all(color: AppColors.border),
      ),
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
                    Text(fmtDateTime(d.timestamp),
                        style: AppTextStyles.nano
                            .copyWith(color: AppColors.textDim)),
                    const SizedBox(height: 4),
                    Wrap(
                      spacing: 6,
                      runSpacing: 4,
                      children: [
                        AppBadge(label: label, color: labelColor),
                        if (d.overrideApplied == true)
                          AppBadge(
                              label: 'Override',
                              color: AppColors.warning),
                        if (d.primaryStrategy != null)
                          _Chip(label: d.primaryStrategy!),
                      ],
                    ),
                  ],
                ),
              ),
              if (d.normalizedScore != null)
                Column(
                  crossAxisAlignment: CrossAxisAlignment.end,
                  children: [
                    Text('Weighted Score',
                        style: AppTextStyles.nano.copyWith(
                            color: AppColors.textFaint)),
                    Text(
                      d.normalizedScore!.toStringAsFixed(3),
                      style: AppTextStyles.mono(12,
                          color: AppColors.textMuted),
                    ),
                  ],
                ),
            ],
          ),
          if (d.finalReason != null &&
              d.finalReason!.isNotEmpty) ...[
            const SizedBox(height: 8),
            Text(d.finalReason!,
                style: AppTextStyles.body
                    .copyWith(color: AppColors.textMd)),
          ],
          if (d.strategies.isNotEmpty) ...[
            const SizedBox(height: 8),
            ...d.strategies.map((s) => Padding(
                  padding: const EdgeInsets.only(bottom: 4),
                  child: Row(
                    crossAxisAlignment:
                        CrossAxisAlignment.start,
                    children: [
                      AppBadge(
                          label: s.decisionLabel(),
                          color: s.decisionLabel() == 'BUY'
                              ? AppColors.success
                              : s.decisionLabel() == 'SELL'
                                  ? AppColors.danger
                                  : AppColors.textMuted),
                      const SizedBox(width: 6),
                      Expanded(
                        child: Column(
                          crossAxisAlignment:
                              CrossAxisAlignment.start,
                          children: [
                            Text(s.strategy ?? '?',
                                style: AppTextStyles.bodyHi),
                            if (s.reason != null)
                              Text(
                                s.reason!.length > 360
                                    ? '${s.reason!.substring(0, 359)}…'
                                    : s.reason!,
                                style: AppTextStyles.meta.copyWith(
                                    color: AppColors.textDim),
                              ),
                          ],
                        ),
                      ),
                    ],
                  ),
                )),
          ],
        ],
      ),
    );
  }
}

// ── Backtest detail skeleton ──────────────────────────────────────────────────

class _BacktestDetailSkeleton extends StatelessWidget {
  const _BacktestDetailSkeleton();

  @override
  Widget build(BuildContext context) {
    return Padding(
      padding: const EdgeInsets.fromLTRB(16, 16, 16, 24),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          // Header: icon + title line + status pill + date line
          Row(
            children: [
              Skeleton.circle(44),
              const SizedBox(width: 12),
              Expanded(
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Row(
                      children: [
                        Skeleton(width: 160, height: 20, radius: 6),
                        const SizedBox(width: 8),
                        Skeleton(width: 70, height: 20, radius: 999),
                      ],
                    ),
                    const SizedBox(height: 6),
                    Skeleton.line(width: 140, height: 11),
                    const SizedBox(height: 4),
                    Skeleton.line(width: 100, height: 10),
                  ],
                ),
              ),
            ],
          ),
          const SizedBox(height: 16),
          // Action chip row
          Row(
            children: [
              Skeleton(width: 64, height: 28, radius: 8),
              const SizedBox(width: 8),
              Skeleton(width: 64, height: 28, radius: 8),
              const SizedBox(width: 8),
              Skeleton(width: 80, height: 28, radius: 8),
            ],
          ),
          const SizedBox(height: 20),
          // Stat grid: 2 cols x 4 rows = ~7 tiles
          GridView.builder(
            shrinkWrap: true,
            physics: const NeverScrollableScrollPhysics(),
            gridDelegate: const SliverGridDelegateWithFixedCrossAxisCount(
              crossAxisCount: 2,
              crossAxisSpacing: 10,
              mainAxisSpacing: 10,
              childAspectRatio: 2.2,
            ),
            itemCount: 6,
            itemBuilder: (_, __) => Container(
              padding: const EdgeInsets.all(12),
              decoration: BoxDecoration(
                color: AppColors.surface.withValues(alpha: 0.5),
                borderRadius: BorderRadius.circular(8),
                border: Border.all(color: AppColors.border),
              ),
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Skeleton.line(width: 60, height: 9),
                  const SizedBox(height: 6),
                  Skeleton.line(width: 80, height: 14),
                ],
              ),
            ),
          ),
          const SizedBox(height: 16),
          // AI Credits card skeleton
          GlassCard(
            padding: const EdgeInsets.all(16),
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Row(
                  children: [
                    Skeleton.circle(32),
                    const SizedBox(width: 10),
                    Expanded(
                      child: Column(
                        crossAxisAlignment: CrossAxisAlignment.start,
                        children: [
                          Skeleton.line(width: 80, height: 11),
                          const SizedBox(height: 4),
                          Skeleton.line(width: 140, height: 10),
                        ],
                      ),
                    ),
                  ],
                ),
              ],
            ),
          ),
          const SizedBox(height: 16),
          // Chart block skeleton
          GlassCard(
            padding: const EdgeInsets.all(16),
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Skeleton.line(width: 160, height: 11),
                const SizedBox(height: 8),
                Skeleton(height: 180, radius: 10),
              ],
            ),
          ),
          const SizedBox(height: 16),
          // Stock accordion rows (3)
          ...List.generate(
            3,
            (_) => Padding(
              padding: const EdgeInsets.only(bottom: 8),
              child: Container(
                padding: const EdgeInsets.all(14),
                decoration: BoxDecoration(
                  color: AppColors.surface.withValues(alpha: 0.3),
                  borderRadius: BorderRadius.circular(16),
                  border: Border.all(color: AppColors.border),
                ),
                child: Row(
                  children: [
                    Skeleton(width: 50, height: 13, radius: 4),
                    const SizedBox(width: 8),
                    Skeleton(width: 90, height: 11, radius: 4),
                    const Spacer(),
                    Skeleton(width: 18, height: 18, radius: 4),
                  ],
                ),
              ),
            ),
          ),
        ],
      ),
    );
  }
}

// ── LLM cost card skeleton ─────────────────────────────────────────────────────

class _LlmCostSkeleton extends StatelessWidget {
  const _LlmCostSkeleton();

  @override
  Widget build(BuildContext context) {
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Row(
          children: [
            Expanded(child: Skeleton.line(width: 80, height: 13)),
            const SizedBox(width: 16),
            Expanded(child: Skeleton.line(width: 40, height: 13)),
            const SizedBox(width: 16),
            Expanded(child: Skeleton.line(width: 60, height: 13)),
            const SizedBox(width: 16),
            Expanded(child: Skeleton.line(width: 60, height: 13)),
          ],
        ),
        const SizedBox(height: 12),
        Skeleton.line(height: 10),
        const SizedBox(height: 6),
        Skeleton.line(height: 10),
      ],
    );
  }
}

// ── Round-trip stats ──────────────────────────────────────────────────────────

class _RoundTripStats extends StatelessWidget {
  const _RoundTripStats({required this.summary});

  final BacktestSummary summary;

  @override
  Widget build(BuildContext context) {
    return GlassCard(
      padding: const EdgeInsets.all(16),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Text('Round Trip Statistics',
              style: AppTextStyles.eyebrow),
          const SizedBox(height: 12),
          GridView.count(
            shrinkWrap: true,
            physics: const NeverScrollableScrollPhysics(),
            crossAxisCount: 2,
            crossAxisSpacing: 10,
            mainAxisSpacing: 10,
            childAspectRatio: 2.5,
            children: [
              StatTile(
                  label: 'Round Trips',
                  value: '${summary.roundTrips ?? '—'}'),
              StatTile(
                  label: 'Total RT P&L',
                  value: fmtPnl(summary.totalRoundTripPnl),
                  valueColor: pnlColor(summary.totalRoundTripPnl)),
              StatTile(
                  label: 'Avg Winning',
                  value: fmtMoney(summary.avgWinningRoundTrip),
                  valueColor: AppColors.success),
              StatTile(
                  label: 'Avg Losing',
                  value: fmtMoney(summary.avgLosingRoundTrip),
                  valueColor: AppColors.danger),
            ],
          ),
        ],
      ),
    );
  }
}
