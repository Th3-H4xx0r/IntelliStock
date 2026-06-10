import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';
import 'package:syncfusion_flutter_charts/charts.dart';
import '../../../core/formatters/formatters.dart';
import '../../../core/theme/app_colors.dart';
import '../../../core/theme/app_text_styles.dart';
import '../../../core/widgets/app_background.dart';
import '../../../core/widgets/glass_card.dart';
import '../../../core/widgets/material_symbols.dart';
import '../../../core/widgets/skeleton.dart';
import '../application/backtest_playback_controller.dart';
import '../data/models/backtest.dart';

class BacktestPlaybackScreen extends ConsumerWidget {
  const BacktestPlaybackScreen({super.key, required this.id});

  final String id;

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final state =
        ref.watch(backtestPlaybackControllerProvider(id));
    final ctrl = ref
        .read(backtestPlaybackControllerProvider(id).notifier);

    return Scaffold(
      backgroundColor: AppColors.canvas,
      body: AppBackground(
        child: SafeArea(
          child: state.loading
              ? const _PlaybackSkeleton()
              : state.error != null
                  ? _ErrorView(
                      message: state.error!,
                      onBack: () => context.canPop()
                          ? context.pop()
                          : context.go('/backtests/$id'))
                  : state.isEmpty
                      ? _EmptyView(
                          onBack: () => context.canPop()
                              ? context.pop()
                              : context.go('/backtests/$id'))
                      : _PlaybackBody(
                          id: id,
                          state: state,
                          ctrl: ctrl,
                          onBack: () => context.canPop()
                              ? context.pop()
                              : context.go('/backtests/$id'),
                        ),
        ),
      ),
    );
  }
}

// ── Main playback body ────────────────────────────────────────────────────────

class _PlaybackBody extends StatefulWidget {
  const _PlaybackBody({
    required this.id,
    required this.state,
    required this.ctrl,
    required this.onBack,
  });

  final String id;
  final BacktestPlaybackState state;
  final BacktestPlaybackController ctrl;
  final VoidCallback onBack;

  @override
  State<_PlaybackBody> createState() => _PlaybackBodyState();
}

class _PlaybackBodyState extends State<_PlaybackBody> {
  final ScrollController _eventScroll = ScrollController();

  @override
  void didUpdateWidget(_PlaybackBody old) {
    super.didUpdateWidget(old);
    // Auto-scroll event log when new frame is added
    if (old.state.frameIndex != widget.state.frameIndex) {
      WidgetsBinding.instance.addPostFrameCallback((_) {
        if (_eventScroll.hasClients) {
          _eventScroll.animateTo(
            _eventScroll.position.maxScrollExtent,
            duration: const Duration(milliseconds: 300),
            curve: Curves.easeOut,
          );
        }
      });
    }
  }

  @override
  void dispose() {
    _eventScroll.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    final s = widget.state;
    final ctrl = widget.ctrl;

    return Column(
      children: [
        // ── Top header ────────────────────────────────────────────────────────
        Padding(
          padding: const EdgeInsets.fromLTRB(12, 12, 12, 8),
          child: Row(
            children: [
              // Back button
              InkWell(
                onTap: widget.onBack,
                borderRadius: BorderRadius.circular(8),
                child: Container(
                  padding: const EdgeInsets.all(6),
                  decoration: BoxDecoration(
                    color: AppColors.surface,
                    borderRadius: BorderRadius.circular(8),
                    border: Border.all(color: AppColors.border),
                  ),
                  child: Icon(symbol('arrow_back'),
                      color: AppColors.textMuted, size: 16),
                ),
              ),
              const SizedBox(width: 10),
              Expanded(
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Row(
                      children: [
                        Text('Backtest Playback',
                            style: AppTextStyles.h3),
                        const SizedBox(width: 6),
                        Container(
                          padding: const EdgeInsets.symmetric(
                              horizontal: 6, vertical: 2),
                          decoration: BoxDecoration(
                            color: AppColors.fill(AppColors.warning),
                            borderRadius: BorderRadius.circular(4),
                            border: Border.all(
                                color: AppColors.stroke(
                                    AppColors.warning)),
                          ),
                          child: Text('LIVE',
                              style: AppTextStyles.nano.copyWith(
                                  color: AppColors.warning,
                                  fontWeight: FontWeight.w700,
                                  fontFamily: 'monospace')),
                        ),
                      ],
                    ),
                    Text('Backtest #${widget.id}',
                        style: AppTextStyles.nano
                            .copyWith(color: AppColors.textDim)),
                  ],
                ),
              ),
              // Current date pill
              Container(
                padding: const EdgeInsets.symmetric(
                    horizontal: 10, vertical: 5),
                decoration: BoxDecoration(
                  color: AppColors.panelAlt,
                  borderRadius: BorderRadius.circular(10),
                  border: Border.all(color: AppColors.border),
                ),
                child: Row(
                  mainAxisSize: MainAxisSize.min,
                  children: [
                    Icon(symbol('calendar_today'),
                        color: AppColors.info, size: 12),
                    const SizedBox(width: 5),
                    Text(
                      s.currentDateLabel,
                      style: AppTextStyles.mono(11,
                          color: AppColors.textMd,
                          weight: FontWeight.w600),
                    ),
                  ],
                ),
              ),
              const SizedBox(width: 8),
              // Controls
              _ControlBar(
                isPlaying: s.isPlaying,
                speed: s.speed,
                onPlay: ctrl.togglePlay,
                onReset: ctrl.reset,
                onSpeed: ctrl.cycleSpeed,
              ),
            ],
          ),
        ),

        // ── Main content: event stepper + sidebar ─────────────────────────────
        Expanded(
          child: Padding(
            padding: const EdgeInsets.fromLTRB(12, 0, 12, 12),
            child: Column(
              children: [
                // Portfolio chart + holdings (top on mobile)
                _PortfolioPanel(state: s),
                const SizedBox(height: 10),
                // Event stepper (scrollable)
                Expanded(
                  child: _EventStepper(
                    events: s.visibleEvents,
                    isPlaying: s.isPlaying,
                    scrollController: _eventScroll,
                  ),
                ),
              ],
            ),
          ),
        ),
      ],
    );
  }
}

// ── Control bar ───────────────────────────────────────────────────────────────

class _ControlBar extends StatelessWidget {
  const _ControlBar({
    required this.isPlaying,
    required this.speed,
    required this.onPlay,
    required this.onReset,
    required this.onSpeed,
  });

  final bool isPlaying;
  final double speed;
  final VoidCallback onPlay;
  final VoidCallback onReset;
  final VoidCallback onSpeed;

  @override
  Widget build(BuildContext context) => Container(
        padding: const EdgeInsets.symmetric(
            horizontal: 6, vertical: 4),
        decoration: BoxDecoration(
          color: AppColors.panelAlt,
          borderRadius: BorderRadius.circular(12),
          border: Border.all(color: AppColors.border),
        ),
        child: Row(
          mainAxisSize: MainAxisSize.min,
          children: [
            // Play/pause
            _CtrlBtn(
              icon: isPlaying ? symbol('pause') : symbol('play_arrow'),
              color: isPlaying ? AppColors.warning : AppColors.success,
              onTap: onPlay,
            ),
            const SizedBox(width: 4),
            // Reset
            _CtrlBtn(
              icon: symbol('replay'),
              color: AppColors.textMuted,
              onTap: onReset,
            ),
            const SizedBox(width: 4),
            // Speed
            InkWell(
              onTap: onSpeed,
              borderRadius: BorderRadius.circular(6),
              child: Container(
                padding: const EdgeInsets.symmetric(
                    horizontal: 8, vertical: 5),
                decoration: BoxDecoration(
                  color: AppColors.surface,
                  borderRadius: BorderRadius.circular(6),
                  border: Border.all(color: AppColors.border),
                ),
                child: Text(
                  '${speed % 1 == 0 ? speed.toInt() : speed}x',
                  style: AppTextStyles.mono(11,
                      color: AppColors.textMd,
                      weight: FontWeight.w700),
                ),
              ),
            ),
          ],
        ),
      );
}

class _CtrlBtn extends StatelessWidget {
  const _CtrlBtn({
    required this.icon,
    required this.color,
    required this.onTap,
  });

  final IconData icon;
  final Color color;
  final VoidCallback onTap;

  @override
  Widget build(BuildContext context) => InkWell(
        onTap: onTap,
        borderRadius: BorderRadius.circular(8),
        child: Container(
          width: 32,
          height: 32,
          decoration: BoxDecoration(
            color: AppColors.fill(color),
            borderRadius: BorderRadius.circular(8),
            border: Border.all(color: AppColors.stroke(color)),
          ),
          child: Icon(icon, color: color, size: 18),
        ),
      );
}

// ── Portfolio panel ───────────────────────────────────────────────────────────

class _PortfolioPanel extends StatelessWidget {
  const _PortfolioPanel({required this.state});

  final BacktestPlaybackState state;

  @override
  Widget build(BuildContext context) {
    final history = state.portfolioHistory;
    final range = state.xRange;

    // Build series with a leading anchor point
    final points = [
      (
        time: range.min,
        value: (state.metadata.initialCash?.toDouble() ?? 0.0)
      ),
      ...history,
    ];

    return GlassCard(
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
                    Text('PORTFOLIO VALUE',
                        style: AppTextStyles.eyebrow),
                    Text(
                      fmtMoney(state.currentPortfolioValue),
                      style: AppTextStyles.valueXl
                          .copyWith(color: AppColors.textHi),
                    ),
                  ],
                ),
              ),
              // Holdings grid (compact)
              if (state.currentHoldings.isNotEmpty)
                _HoldingsMini(holdings: state.currentHoldings),
            ],
          ),
          const SizedBox(height: 10),
          SizedBox(
            height: 150,
            child: SfCartesianChart(
              plotAreaBorderWidth: 0,
              backgroundColor: Colors.transparent,
              margin: EdgeInsets.zero,
              primaryXAxis: DateTimeAxis(
                minimum: range.min,
                maximum: range.max,
                majorGridLines: const MajorGridLines(
                    color: AppColors.chartGrid, width: 1),
                axisLine: const AxisLine(width: 0),
                labelStyle: const TextStyle(
                    color: AppColors.chartAxis, fontSize: 9),
              ),
              primaryYAxis: NumericAxis(
                isVisible: false,
                majorGridLines: const MajorGridLines(width: 0),
                axisLine: const AxisLine(width: 0),
              ),
              tooltipBehavior: TooltipBehavior(enable: false),
              series: <CartesianSeries>[
                SplineAreaSeries<({DateTime time, double value}), DateTime>(
                  dataSource: points,
                  splineType: SplineType.monotonic,
                  xValueMapper: (d, _) => d.time,
                  yValueMapper: (d, _) => d.value,
                  color: AppColors.chartLine.withValues(alpha: 0.25),
                  borderColor: AppColors.chartLine,
                  borderWidth: 2,
                  animationDuration: 500,
                ),
              ],
            ),
          ),
        ],
      ),
    );
  }
}

class _HoldingsMini extends StatelessWidget {
  const _HoldingsMini({required this.holdings});

  final List<PlaybackHolding> holdings;

  @override
  Widget build(BuildContext context) => Wrap(
        spacing: 4,
        runSpacing: 4,
        children: holdings
            .take(6)
            .map(
              (h) => Container(
                padding: const EdgeInsets.symmetric(
                    horizontal: 6, vertical: 3),
                decoration: BoxDecoration(
                  color: AppColors.canvas,
                  borderRadius: BorderRadius.circular(6),
                  border: Border.all(color: AppColors.border),
                ),
                child: Column(
                  children: [
                    Text(
                      h.ticker,
                      style: AppTextStyles.mono(10,
                          color: AppColors.textHi,
                          weight: FontWeight.w700),
                    ),
                    if (h.qty != null)
                      Text(
                        '${h.qty!.toStringAsFixed(0)} sh',
                        style: AppTextStyles.nano
                            .copyWith(color: AppColors.textDim),
                      ),
                    if (h.curr != null && h.avg != null)
                      Text(
                        fmtMoney(h.curr),
                        style: AppTextStyles.nano.copyWith(
                          color: h.curr! > h.avg!
                              ? AppColors.success
                              : h.curr! < h.avg!
                                  ? AppColors.danger
                                  : AppColors.textDim,
                        ),
                      ),
                  ],
                ),
              ),
            )
            .toList(),
      );
}

// ── Event stepper ─────────────────────────────────────────────────────────────

class _EventStepper extends StatelessWidget {
  const _EventStepper({
    required this.events,
    required this.isPlaying,
    required this.scrollController,
  });

  final List<PlaybackEvent> events;
  final bool isPlaying;
  final ScrollController scrollController;

  @override
  Widget build(BuildContext context) {
    return GlassCard(
      padding: EdgeInsets.zero,
      borderColor: AppColors.border,
      child: Column(
        children: [
          Container(
            padding: const EdgeInsets.symmetric(
                horizontal: 14, vertical: 10),
            decoration: const BoxDecoration(
              color: AppColors.canvas,
              borderRadius: BorderRadius.vertical(
                  top: Radius.circular(16)),
            ),
            child: Row(
              children: [
                Text('Execution Log',
                    style: AppTextStyles.cardTitle),
              ],
            ),
          ),
          Expanded(
            child: ListView.builder(
              controller: scrollController,
              padding: const EdgeInsets.all(12),
              itemCount: events.length + (isPlaying ? 1 : 0),
              itemBuilder: (_, i) {
                if (i == events.length) {
                  // Typing indicator
                  return const _TypingNode();
                }
                return _EventNode(event: events[i], index: i);
              },
            ),
          ),
        ],
      ),
    );
  }
}

class _EventNode extends StatelessWidget {
  const _EventNode({required this.event, required this.index});

  final PlaybackEvent event;
  final int index;

  @override
  Widget build(BuildContext context) {
    switch (event.type) {
      case 'date':
        return _DateMarker(event: event);
      case 'strategy':
        return _StrategyNode(event: event);
      case 'outcome':
        return _OutcomeNode(event: event);
      case 'decision':
        return _DecisionNode(event: event);
      case 'portfolio':
        return const SizedBox.shrink(); // hidden in stepper
      default:
        return const SizedBox.shrink();
    }
  }
}

class _DateMarker extends StatelessWidget {
  const _DateMarker({required this.event});

  final PlaybackEvent event;

  @override
  Widget build(BuildContext context) => Padding(
        padding: const EdgeInsets.symmetric(vertical: 8),
        child: Row(
          children: [
            Expanded(
                child: Divider(
                    color: AppColors.border, thickness: 1)),
            const SizedBox(width: 8),
            Container(
              padding: const EdgeInsets.symmetric(
                  horizontal: 10, vertical: 4),
              decoration: BoxDecoration(
                color: AppColors.fill(AppColors.primary),
                borderRadius: BorderRadius.circular(999),
                border: Border.all(
                    color: AppColors.stroke(AppColors.primary)),
                boxShadow: [
                  BoxShadow(
                      color: AppColors.primary
                          .withValues(alpha: 0.2),
                      blurRadius: 10),
                ],
              ),
              child: Text(
                '${event.label ?? ''} — ${event.time ?? ''}',
                style: AppTextStyles.nano.copyWith(
                    color: AppColors.primary,
                    fontWeight: FontWeight.w700),
              ),
            ),
            const SizedBox(width: 8),
            Expanded(
                child: Divider(
                    color: AppColors.border, thickness: 1)),
          ],
        ),
      );
}

class _StrategyNode extends StatelessWidget {
  const _StrategyNode({required this.event});

  final PlaybackEvent event;

  @override
  Widget build(BuildContext context) {
    final desc = event.desc ?? '';
    final parts = desc.contains(' → ') ? desc.split(' → ') : null;

    return _StepRow(
      icon: symbol('psychology'),
      color: const Color(0xFF818CF8), // indigo-400
      child: GlassCard(
        padding: const EdgeInsets.all(12),
        borderColor: const Color(0xFF818CF8).withValues(alpha: 0.2),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            if (parts != null) ...[
              Row(
                children: [
                  Text(parts[0],
                      style: AppTextStyles.mono(11,
                          color: AppColors.textMd,
                          weight: FontWeight.w700)),
                  const SizedBox(width: 6),
                  _ActionBadge(action: parts[1]),
                ],
              ),
            ] else
              Text(
                desc.toUpperCase(),
                style: AppTextStyles.nano.copyWith(
                    color: const Color(0xFF818CF8),
                    fontWeight: FontWeight.w700,
                    letterSpacing: 0.5),
              ),
            const SizedBox(height: 4),
            Text(event.name ?? '',
                style: AppTextStyles.bodyHi),
            if (event.reason != null &&
                event.reason!.isNotEmpty) ...[
              const SizedBox(height: 4),
              Text(event.reason!,
                  style: AppTextStyles.meta
                      .copyWith(color: AppColors.textDim)),
            ],
            if (event.tickers.isNotEmpty) ...[
              const SizedBox(height: 6),
              Wrap(
                spacing: 4,
                runSpacing: 4,
                children: [
                  Text('Scanning:',
                      style: AppTextStyles.nano.copyWith(
                          color: AppColors.textFaint,
                          fontWeight: FontWeight.w700)),
                  ...event.tickers.map((t) => Container(
                        padding:
                            const EdgeInsets.symmetric(
                                horizontal: 5, vertical: 2),
                        decoration: BoxDecoration(
                          color: AppColors.surface,
                          borderRadius: BorderRadius.circular(4),
                          border: Border.all(
                              color: AppColors.border),
                        ),
                        child: Text(t,
                            style: AppTextStyles.mono(10,
                                color: AppColors.textMuted)),
                      )),
                ],
              ),
            ],
          ],
        ),
      ),
    );
  }
}

class _OutcomeNode extends StatelessWidget {
  const _OutcomeNode({required this.event});

  final PlaybackEvent event;

  @override
  Widget build(BuildContext context) => _StepRow(
        icon: symbol('score'),
        color: AppColors.info,
        child: Container(
          padding:
              const EdgeInsets.symmetric(horizontal: 12, vertical: 8),
          decoration: BoxDecoration(
            border: const Border(
                left: BorderSide(
                    color: AppColors.info, width: 2)),
            color: AppColors.fill(AppColors.info),
            borderRadius: const BorderRadius.horizontal(
                right: Radius.circular(10)),
          ),
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Text(event.name ?? '',
                  style: AppTextStyles.bodyHi
                      .copyWith(color: AppColors.info)),
              if (event.details != null)
                Text(event.details!,
                    style: AppTextStyles.mono(10,
                        color: AppColors.textDim)),
            ],
          ),
        ),
      );
}

class _DecisionNode extends StatelessWidget {
  const _DecisionNode({required this.event});

  final PlaybackEvent event;

  @override
  Widget build(BuildContext context) => _StepRow(
        icon: symbol('gavel'),
        color: AppColors.warning,
        child: GlassCard(
          padding: const EdgeInsets.all(12),
          borderColor: AppColors.warning.withValues(alpha: 0.2),
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Text('EXECUTION DECISIONS',
                  style: AppTextStyles.nano.copyWith(
                      color: AppColors.warning,
                      fontWeight: FontWeight.w700,
                      letterSpacing: 0.5)),
              const SizedBox(height: 8),
              if (event.buys.isEmpty && event.sells.isEmpty)
                Text('No actions taken.',
                    style: AppTextStyles.meta
                        .copyWith(color: AppColors.textDim))
              else ...[
                ...event.buys.map((b) => _TradeRow(
                    trade: b, isBuy: true)),
                ...event.sells.map((s) => _TradeRow(
                    trade: s, isBuy: false)),
              ],
            ],
          ),
        ),
      );
}

class _TradeRow extends StatelessWidget {
  const _TradeRow({required this.trade, required this.isBuy});

  final PlaybackTrade trade;
  final bool isBuy;

  @override
  Widget build(BuildContext context) {
    final c = isBuy ? AppColors.success : AppColors.danger;
    return Container(
      margin: const EdgeInsets.only(bottom: 4),
      padding:
          const EdgeInsets.symmetric(horizontal: 10, vertical: 6),
      decoration: BoxDecoration(
        color: AppColors.fill(c),
        borderRadius: BorderRadius.circular(8),
        border: Border.all(color: AppColors.stroke(c)),
      ),
      child: Row(
        children: [
          Container(
            padding:
                const EdgeInsets.symmetric(horizontal: 5, vertical: 2),
            decoration: BoxDecoration(
              color: AppColors.fill(c).withValues(alpha: 0.3),
              borderRadius: BorderRadius.circular(4),
            ),
            child: Text(isBuy ? 'Buy' : 'Sell',
                style: AppTextStyles.nano.copyWith(
                    color: c, fontWeight: FontWeight.w700)),
          ),
          const SizedBox(width: 8),
          Text(trade.ticker ?? '?',
              style: AppTextStyles.mono(12,
                  color: AppColors.textHi,
                  weight: FontWeight.w700)),
          const SizedBox(width: 6),
          Text(
            '${trade.qty != null ? trade.qty!.toStringAsFixed(0) : '?'} @ ${fmtMoney(trade.price)}',
            style: AppTextStyles.meta
                .copyWith(color: AppColors.textDim),
          ),
          const Spacer(),
          if (trade.reason != null)
            Flexible(
              child: Text(
                trade.reason!,
                style: AppTextStyles.nano
                    .copyWith(color: AppColors.textFaint),
                overflow: TextOverflow.ellipsis,
                textAlign: TextAlign.end,
              ),
            ),
        ],
      ),
    );
  }
}

class _StepRow extends StatelessWidget {
  const _StepRow({
    required this.icon,
    required this.color,
    required this.child,
  });

  final IconData icon;
  final Color color;
  final Widget child;

  @override
  Widget build(BuildContext context) => Padding(
        padding: const EdgeInsets.only(bottom: 10),
        child: Row(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Column(
              children: [
                Container(
                  width: 30,
                  height: 30,
                  decoration: BoxDecoration(
                    color: AppColors.fill(color),
                    shape: BoxShape.circle,
                    border: Border.all(
                        color: AppColors.stroke(color)),
                    boxShadow: [
                      BoxShadow(
                          color: color.withValues(alpha: 0.25),
                          blurRadius: 8),
                    ],
                  ),
                  child: Icon(icon, color: color, size: 14),
                ),
              ],
            ),
            const SizedBox(width: 8),
            Expanded(child: child),
          ],
        ),
      );
}

class _TypingNode extends StatelessWidget {
  const _TypingNode();

  @override
  Widget build(BuildContext context) => Padding(
        padding: const EdgeInsets.only(bottom: 10),
        child: Row(
          children: [
            Container(
              width: 30,
              height: 30,
              decoration: BoxDecoration(
                color: AppColors.fill(AppColors.border),
                shape: BoxShape.circle,
                border: Border.all(color: AppColors.border),
              ),
              child: const Icon(Icons.more_horiz,
                  color: AppColors.textFaint, size: 14),
            ),
            const SizedBox(width: 8),
            const SizedBox(
              width: 80,
              height: 28,
              // The "loading bar" placeholder (no animation to avoid State)
              child: DecoratedBox(
                decoration: BoxDecoration(
                    color: AppColors.surface,
                    borderRadius:
                        BorderRadius.all(Radius.circular(10))),
              ),
            ),
          ],
        ),
      );
}

class _ActionBadge extends StatelessWidget {
  const _ActionBadge({required this.action});

  final String action;

  @override
  Widget build(BuildContext context) {
    Color c;
    switch (action.toUpperCase()) {
      case 'BUY':
        c = AppColors.success;
        break;
      case 'SELL':
        c = AppColors.danger;
        break;
      default:
        c = AppColors.textMuted;
    }
    return Container(
      padding:
          const EdgeInsets.symmetric(horizontal: 6, vertical: 2),
      decoration: BoxDecoration(
        color: AppColors.fill(c),
        borderRadius: BorderRadius.circular(4),
        border: Border.all(color: AppColors.stroke(c)),
      ),
      child: Text(action.toUpperCase(),
          style: AppTextStyles.nano
              .copyWith(color: c, fontWeight: FontWeight.w700)),
    );
  }
}

// ── Playback skeleton ─────────────────────────────────────────────────────────

class _PlaybackSkeleton extends StatelessWidget {
  const _PlaybackSkeleton();

  @override
  Widget build(BuildContext context) {
    return Column(
      children: [
        // Top header bar
        Padding(
          padding: const EdgeInsets.fromLTRB(12, 12, 12, 8),
          child: Row(
            children: [
              Skeleton(width: 32, height: 32, radius: 8),
              const SizedBox(width: 10),
              Expanded(
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Skeleton(width: 160, height: 16, radius: 6),
                    const SizedBox(height: 4),
                    Skeleton(width: 90, height: 10, radius: 4),
                  ],
                ),
              ),
              Skeleton(width: 80, height: 28, radius: 10),
              const SizedBox(width: 8),
              Skeleton(width: 88, height: 32, radius: 12),
            ],
          ),
        ),
        // Main area
        Expanded(
          child: Padding(
            padding: const EdgeInsets.fromLTRB(12, 0, 12, 12),
            child: Column(
              children: [
                // Portfolio panel skeleton
                GlassCard(
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
                                Skeleton.line(width: 110, height: 10),
                                const SizedBox(height: 6),
                                Skeleton(width: 150, height: 22, radius: 6),
                              ],
                            ),
                          ),
                          // Holdings mini chips
                          Wrap(
                            spacing: 4,
                            runSpacing: 4,
                            children: List.generate(
                              3,
                              (_) => Skeleton(
                                  width: 46, height: 42, radius: 6),
                            ),
                          ),
                        ],
                      ),
                      const SizedBox(height: 10),
                      // Chart block
                      Skeleton(height: 150, radius: 8),
                    ],
                  ),
                ),
                const SizedBox(height: 10),
                // Event log skeleton
                Expanded(
                  child: GlassCard(
                    padding: EdgeInsets.zero,
                    borderColor: AppColors.border,
                    child: Column(
                      children: [
                        // Log header
                        Container(
                          padding: const EdgeInsets.symmetric(
                              horizontal: 14, vertical: 10),
                          decoration: const BoxDecoration(
                            color: AppColors.canvas,
                            borderRadius: BorderRadius.vertical(
                                top: Radius.circular(16)),
                          ),
                          child: Skeleton.line(width: 100, height: 14),
                        ),
                        // Skeleton event rows
                        Expanded(
                          child: Padding(
                            padding: const EdgeInsets.all(12),
                            child: Column(
                              children: List.generate(
                                5,
                                (i) => Padding(
                                  padding: const EdgeInsets.only(bottom: 10),
                                  child: Row(
                                    crossAxisAlignment:
                                        CrossAxisAlignment.start,
                                    children: [
                                      Skeleton.circle(30),
                                      const SizedBox(width: 8),
                                      Expanded(
                                        child: Skeleton(
                                          height: 44 + (i % 2) * 14.0,
                                          radius: 10,
                                        ),
                                      ),
                                    ],
                                  ),
                                ),
                              ),
                            ),
                          ),
                        ),
                      ],
                    ),
                  ),
                ),
              ],
            ),
          ),
        ),
      ],
    );
  }
}

// ── Error / empty ─────────────────────────────────────────────────────────────

class _ErrorView extends StatelessWidget {
  const _ErrorView({required this.message, required this.onBack});

  final String message;
  final VoidCallback onBack;

  @override
  Widget build(BuildContext context) => Center(
        child: Padding(
          padding: const EdgeInsets.all(24),
          child: Column(
            mainAxisSize: MainAxisSize.min,
            children: [
              Icon(symbol('error'), color: AppColors.danger, size: 48),
              const SizedBox(height: 12),
              Text('Failed to load playback',
                  style: AppTextStyles.cardTitle),
              const SizedBox(height: 6),
              Text(message,
                  style: AppTextStyles.meta
                      .copyWith(color: AppColors.textDim),
                  textAlign: TextAlign.center),
              const SizedBox(height: 20),
              TextButton.icon(
                onPressed: onBack,
                icon: Icon(symbol('arrow_back'),
                    color: AppColors.textMuted, size: 16),
                label: Text('Back to Backtest',
                    style: AppTextStyles.body
                        .copyWith(color: AppColors.textMuted)),
              ),
            ],
          ),
        ),
      );
}

class _EmptyView extends StatelessWidget {
  const _EmptyView({required this.onBack});

  final VoidCallback onBack;

  @override
  Widget build(BuildContext context) => Center(
        child: Padding(
          padding: const EdgeInsets.all(24),
          child: Column(
            mainAxisSize: MainAxisSize.min,
            children: [
              Icon(symbol('play_disabled'),
                  color: AppColors.textFaint, size: 48),
              const SizedBox(height: 12),
              Text('No playback data',
                  style: AppTextStyles.cardTitle),
              const SizedBox(height: 6),
              Text(
                'This backtest has no portfolio snapshots or trades to replay.',
                style: AppTextStyles.meta
                    .copyWith(color: AppColors.textDim),
                textAlign: TextAlign.center,
              ),
              const SizedBox(height: 20),
              TextButton.icon(
                onPressed: onBack,
                icon: Icon(symbol('arrow_back'),
                    color: AppColors.textMuted, size: 16),
                label: Text('Back to Backtest',
                    style: AppTextStyles.body
                        .copyWith(color: AppColors.textMuted)),
              ),
            ],
          ),
        ),
      );
}
