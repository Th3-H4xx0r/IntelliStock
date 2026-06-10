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
import '../application/live_state_notifier.dart';
import '../data/models/live_state.dart';
import 'equity_chart.dart';
import 'manual_order_sheet.dart';
import 'position_card.dart';

// Cross-feature: live logs panel from the instances feature.
// The instances agent exports this widget.
import 'package:intellistock_mobile/features/instances/presentation/live_logs_panel.dart';

class LiveTradingScreen extends ConsumerStatefulWidget {
  const LiveTradingScreen({super.key, required this.instanceId});

  final String instanceId;

  @override
  ConsumerState<LiveTradingScreen> createState() => _LiveTradingScreenState();
}

class _LiveTradingScreenState extends ConsumerState<LiveTradingScreen> {
  ChartStyle _chartStyle = ChartStyle.area;
  int? _scrubIndex;
  bool _haltModalOpen = false;
  bool _haltConfirmed = false;
  final _haltReasonCtrl = TextEditingController(text: 'risk breach');

  static const _ranges = ['1D', '1W', '1M', '3M', 'YTD', '1Y', 'ALL'];

  @override
  void dispose() {
    _haltReasonCtrl.dispose();
    super.dispose();
  }

  late final _provider = liveStateProvider(widget.instanceId);

  LiveStateNotifier get _notifier => ref.read(_provider.notifier);

  @override
  Widget build(BuildContext context) {
    final asyncState = ref.watch(_provider);

    return Scaffold(
      backgroundColor: AppColors.canvas,
      body: AppBackground(
        child: SafeArea(
          child: Stack(
            children: [
              // Main scrollable content
              asyncState.when(
                loading: () => const Center(child: LoadingState(label: 'Loading live state…')),
                error: (e, _) => Center(
                  child: ErrorBanner(
                    message: e.toString(),
                    onRetry: () => ref.invalidate(_provider),
                  ),
                ),
                data: (s) => _buildBody(context, s),
              ),

              // Sticky halt pill (bottom of screen)
              _buildStickyHalt(asyncState.valueOrNull),

              // Command toast overlay
              if (asyncState.valueOrNull?.commandToast != null)
                _buildCommandToast(asyncState.value!.commandToast!),

              // Halt modal
              if (_haltModalOpen) _buildHaltModal(context),
            ],
          ),
        ),
      ),
    );
  }

  Widget _buildBody(BuildContext context, LiveTradingState s) {
    return ListView(
      padding: const EdgeInsets.fromLTRB(16, 0, 16, 96),
      children: [
        const SizedBox(height: 16),

        // Breadcrumb
        _buildBreadcrumb(context),
        const SizedBox(height: 16),

        // Header
        _buildHeader(context, s),
        const SizedBox(height: 12),

        // Lookback banner (when set)
        if (s.liveState?.lookback != null && !s.notRunning)
          _buildLookbackBanner(s.liveState!.lookback!),

        // Not running state
        if (s.notRunning)
          EmptyState(
            icon: symbol('power_off'),
            title: 'No live session',
            subtitle:
                'This instance has no active broker session. Start the instance to begin live trading.',
          )
        else if (s.liveState == null)
          const Center(child: LoadingState(label: 'Loading live state…'))
        else ...[
          _buildHeroEquityCard(context, s),
          const SizedBox(height: 12),
          _buildSecondaryStats(s.liveState!),
          const SizedBox(height: 12),
          _buildRecentExecutions(s.liveState!.recentTrades),
          const SizedBox(height: 12),
          _buildPositions(s),
          const SizedBox(height: 12),
        ],

        // Live logs panel
        LiveLogsPanel(instanceId: widget.instanceId),
        const SizedBox(height: 16),
      ],
    );
  }

  // ── Breadcrumb ───────────────────────────────────────────────────────────────

  Widget _buildBreadcrumb(BuildContext context) {
    return Row(
      children: [
        GestureDetector(
          onTap: () => Navigator.of(context).pop(),
          child: Row(
            children: [
              Icon(symbol('arrow_back'), size: 16, color: AppColors.textDim),
              const SizedBox(width: 4),
              Text('Back', style: AppTextStyles.meta.copyWith(color: AppColors.textDim)),
            ],
          ),
        ),
        Text(' / ', style: AppTextStyles.meta.copyWith(color: AppColors.textFaint)),
        Flexible(
          child: Text(
            'Live Trading (${widget.instanceId})',
            style: AppTextStyles.meta.copyWith(color: AppColors.textMuted),
            overflow: TextOverflow.ellipsis,
          ),
        ),
      ],
    );
  }

  // ── Header ───────────────────────────────────────────────────────────────────

  Widget _buildHeader(BuildContext context, LiveTradingState s) {
    final ls = s.liveState;
    final statusStr = ls?.status.toLowerCase() ?? 'unknown';
    final statusColor = _statusColor(statusStr, s.notRunning);
    final statusLabel = s.notRunning
        ? 'NOT RUNNING'
        : (ls?.status.toUpperCase() ?? 'UNKNOWN');
    final isActive = statusStr == 'active';
    final brokerFailed = ls?.brokerFetchError != null;
    final containerStale = ls?.containerStale == true;

    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Row(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            IconTile(icon: symbol('monitoring'), color: AppColors.info, size: 44),
            const SizedBox(width: 12),
            Expanded(
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Text('Live Trading Terminal', style: AppTextStyles.h2),
                  Text(
                    'Real-time positions & executions',
                    style: AppTextStyles.micro.copyWith(
                      color: AppColors.textFaint,
                      fontFamily: 'monospace',
                    ),
                  ),
                ],
              ),
            ),
          ],
        ),
        const SizedBox(height: 10),
        Wrap(
          spacing: 8,
          runSpacing: 6,
          crossAxisAlignment: WrapCrossAlignment.center,
          children: [
            // Status chip
            StatusPill(
              label: statusLabel,
              color: statusColor,
              pulsing: isActive,
            ),

            // Warning chips
            if (brokerFailed)
              _WarningChip(
                icon: symbol('error'),
                label: 'Broker offline',
                color: AppColors.danger,
              ),
            if (containerStale && !brokerFailed)
              _WarningChip(
                icon: symbol('error'),
                label: 'Container offline',
                color: AppColors.textDim,
              ),
            if (s.fetchError != null && ls != null)
              _WarningChip(
                icon: symbol('error'),
                label: 'Feed error',
                color: AppColors.danger,
              ),

            // Halt (desktop-style inline; mobile gets sticky pill)
            if (!s.notRunning)
              AppButton.semantic(
                label: 'Halt',
                icon: symbol('block'),
                color: AppColors.danger,
                dense: true,
                onPressed: () => setState(() => _haltModalOpen = true),
              ),

            // Manual Order button
            if (!s.notRunning)
              AppButton.semantic(
                label: 'Manual Order',
                icon: symbol('add_shopping_cart'),
                color: AppColors.info,
                dense: true,
                onPressed: () => showManualOrderSheet(context, ref, widget.instanceId),
              ),
          ],
        ),
      ],
    );
  }

  Color _statusColor(String status, bool notRunning) {
    if (notRunning) return AppColors.textDim;
    if (status == 'active') return AppColors.success;
    if (status == 'halted') return AppColors.warning;
    return AppColors.textDim;
  }

  // ── Lookback banner ──────────────────────────────────────────────────────────

  Widget _buildLookbackBanner(Lookback lb) {
    final pct = lb.pct;
    return Container(
      margin: const EdgeInsets.only(bottom: 12),
      padding: const EdgeInsets.all(14),
      decoration: BoxDecoration(
        color: AppColors.fill(AppColors.info),
        borderRadius: BorderRadius.circular(12),
        border: Border.all(color: AppColors.stroke(AppColors.info)),
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Row(
            children: [
              Icon(symbol('history'), color: AppColors.info, size: 16),
              const SizedBox(width: 6),
              Expanded(
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Text(
                      'HISTORIC LOOKBACK TRAINING',
                      style: AppTextStyles.nano.copyWith(
                        color: AppColors.info,
                        fontWeight: FontWeight.w700,
                        letterSpacing: 0.8,
                      ),
                    ),
                    Text(
                      '${lb.specName}  ·  ${lb.startDate} → ${lb.endDate}',
                      style: AppTextStyles.nano.copyWith(color: AppColors.textFaint),
                      overflow: TextOverflow.ellipsis,
                    ),
                  ],
                ),
              ),
              Column(
                crossAxisAlignment: CrossAxisAlignment.end,
                children: [
                  RichText(
                    text: TextSpan(
                      children: [
                        TextSpan(
                          text: '${lb.current}',
                          style: AppTextStyles.valueLg.copyWith(
                            color: AppColors.info,
                            fontSize: 20,
                          ),
                        ),
                        TextSpan(
                          text: '/${lb.total}',
                          style: AppTextStyles.meta.copyWith(color: AppColors.textFaint),
                        ),
                      ],
                    ),
                  ),
                  Text(
                    '${pct.toStringAsFixed(0)}%  ·  ${lb.currentDate}',
                    style: AppTextStyles.nano.copyWith(color: AppColors.textFaint),
                  ),
                ],
              ),
            ],
          ),
          const SizedBox(height: 8),
          ClipRRect(
            borderRadius: BorderRadius.circular(4),
            child: LinearProgressIndicator(
              value: pct / 100,
              backgroundColor: AppColors.surface,
              color: AppColors.info,
              minHeight: 4,
            ),
          ),
          const SizedBox(height: 4),
          Text(
            'Trades deferred until warmup completes.',
            style: AppTextStyles.nano.copyWith(color: AppColors.textFaint),
          ),
        ],
      ),
    );
  }

  // ── Hero equity card ─────────────────────────────────────────────────────────

  Widget _buildHeroEquityCard(BuildContext context, LiveTradingState s) {
    final ls = s.liveState!;
    final history = s.equityHistory;
    final range = s.currentRange;
    final stats = RangeStats.from(history);

    // Scrub overlay: show the scrubbed value or live equity.
    double displayEquity = ls.equity;
    if (_scrubIndex != null && history != null) {
      final idx = _scrubIndex!.clamp(0, history.values.length - 1);
      displayEquity = history.values[idx];
    }

    final changeColor = stats.isUp ? AppColors.chartUp : AppColors.chartDown;

    return GlassCard(
      padding: const EdgeInsets.all(16),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          // Equity label + value + range change
          Row(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Expanded(
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Text(
                      'PORTFOLIO EQUITY',
                      style: AppTextStyles.nano.copyWith(
                        color: AppColors.textDim,
                        letterSpacing: 1.0,
                        fontWeight: FontWeight.w700,
                      ),
                    ),
                    const SizedBox(height: 4),
                    Text(
                      fmtMoney(displayEquity),
                      style: AppTextStyles.valueXl,
                    ),
                    const SizedBox(height: 6),
                    Row(
                      children: [
                        Icon(
                          stats.isUp
                              ? symbol('arrow_upward')
                              : symbol('arrow_downward'),
                          size: 14,
                          color: changeColor,
                        ),
                        const SizedBox(width: 4),
                        Text(
                          '${stats.isUp ? '+' : ''}${fmtMoney(stats.dollars)}',
                          style: AppTextStyles.micro.copyWith(
                            color: changeColor,
                            fontWeight: FontWeight.w700,
                          ),
                        ),
                        const SizedBox(width: 4),
                        Text(
                          '(${stats.isUp ? '+' : ''}${stats.pct.toStringAsFixed(2)}%)',
                          style: AppTextStyles.micro.copyWith(
                            color: changeColor.withValues(alpha: 0.8),
                          ),
                        ),
                        const SizedBox(width: 6),
                        Text(
                          _rangeLabel(range),
                          style: AppTextStyles.nano.copyWith(color: AppColors.textFaint),
                        ),
                      ],
                    ),
                  ],
                ),
              ),
              // Uptime
              Column(
                crossAxisAlignment: CrossAxisAlignment.end,
                children: [
                  Text(
                    'UPTIME',
                    style: AppTextStyles.nano.copyWith(
                      color: AppColors.textDim,
                      letterSpacing: 0.8,
                    ),
                  ),
                  const SizedBox(height: 2),
                  Text(
                    fmtDuration(ls.uptimeSec),
                    style: AppTextStyles.value.copyWith(color: AppColors.textMd),
                  ),
                ],
              ),
            ],
          ),

          const SizedBox(height: 12),

          // Chart
          if (history != null && !history.isEmpty)
            EquityChart(
              history: history,
              style: _chartStyle,
              range: range,
              height: 240,
              onScrub: (idx) => setState(() => _scrubIndex = idx),
              onScrubEnd: () => setState(() => _scrubIndex = null),
            )
          else
            SizedBox(
              height: 240,
              child: Center(
                child: Text(
                  'No equity history yet — broker is fetching…',
                  style: AppTextStyles.micro.copyWith(color: AppColors.textFaint),
                ),
              ),
            ),

          const SizedBox(height: 8),

          // Range tabs + style toggle
          Row(
            children: [
              Expanded(
                child: SingleChildScrollView(
                  scrollDirection: Axis.horizontal,
                  child: Row(
                    children: _ranges.map((r) {
                      final isActive = r == range;
                      final Color activeColor =
                          stats.isUp ? AppColors.chartUp : AppColors.chartDown;
                      return GestureDetector(
                        onTap: () => _notifier.setRange(r),
                        child: Container(
                          padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 6),
                          margin: const EdgeInsets.only(right: 2),
                          decoration: BoxDecoration(
                            color: isActive
                                ? AppColors.fill(activeColor)
                                : Colors.transparent,
                            borderRadius: BorderRadius.circular(6),
                          ),
                          child: Text(
                            r,
                            style: AppTextStyles.micro.copyWith(
                              color: isActive ? activeColor : AppColors.textDim,
                              fontWeight: isActive ? FontWeight.w700 : FontWeight.w400,
                            ),
                          ),
                        ),
                      );
                    }).toList(),
                  ),
                ),
              ),
              const SizedBox(width: 8),
              // Chart style toggle
              Container(
                padding: const EdgeInsets.all(3),
                decoration: BoxDecoration(
                  color: AppColors.surface,
                  borderRadius: BorderRadius.circular(8),
                  border: Border.all(color: AppColors.border),
                ),
                child: Row(
                  mainAxisSize: MainAxisSize.min,
                  children: [
                    _StyleToggleBtn(
                      icon: 'area_chart',
                      active: _chartStyle == ChartStyle.area,
                      onTap: () => setState(() => _chartStyle = ChartStyle.area),
                    ),
                    _StyleToggleBtn(
                      icon: 'show_chart',
                      active: _chartStyle == ChartStyle.line,
                      onTap: () => setState(() => _chartStyle = ChartStyle.line),
                    ),
                    _StyleToggleBtn(
                      icon: 'candlestick_chart',
                      active: _chartStyle == ChartStyle.candle,
                      onTap: () => setState(() => _chartStyle = ChartStyle.candle),
                    ),
                  ],
                ),
              ),
            ],
          ),

          // 4-stat row (Range High / Low / Day P&L / Total P&L)
          if (stats.high != null) ...[
            const SizedBox(height: 12),
            const Divider(color: AppColors.border, height: 1),
            const SizedBox(height: 10),
            Row(
              children: [
                _MiniStat(label: 'RANGE HIGH', value: fmtMoney(stats.high)),
                _MiniStat(label: 'RANGE LOW', value: fmtMoney(stats.low)),
                _MiniStat(
                  label: 'DAY P&L',
                  value: fmtPct(ls.dayPnlPct),
                  color: pnlColor(ls.dayPnl),
                ),
                _MiniStat(
                  label: 'TOTAL P&L',
                  value: fmtPct(ls.totalPnlPct),
                  color: pnlColor(ls.totalPnl),
                ),
              ],
            ),
          ],
        ],
      ),
    );
  }

  String _rangeLabel(String r) {
    switch (r) {
      case '1D': return 'today';
      case '1W': return 'this week';
      case '1M': return 'this month';
      case '3M': return 'past 3 months';
      case 'YTD': return 'year to date';
      case '1Y': return 'past year';
      default: return 'all time';
    }
  }

  // ── Secondary stats ──────────────────────────────────────────────────────────

  Widget _buildSecondaryStats(LiveState ls) {
    return Row(
      children: [
        Expanded(
          child: GlassCard(
            padding: const EdgeInsets.all(12),
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Text('CASH', style: AppTextStyles.nano.copyWith(color: AppColors.textDim, letterSpacing: 0.8)),
                const SizedBox(height: 4),
                Text(fmtMoney(ls.cash), style: AppTextStyles.value.copyWith(color: AppColors.textHi)),
              ],
            ),
          ),
        ),
        const SizedBox(width: 8),
        Expanded(
          child: GlassCard(
            padding: const EdgeInsets.all(12),
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Text('BUYING POWER', style: AppTextStyles.nano.copyWith(color: AppColors.textDim, letterSpacing: 0.8)),
                const SizedBox(height: 4),
                Text(fmtMoney(ls.buyingPower), style: AppTextStyles.value.copyWith(color: AppColors.textHi)),
              ],
            ),
          ),
        ),
        const SizedBox(width: 8),
        Expanded(
          child: GlassCard(
            padding: const EdgeInsets.all(12),
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Text('TOTAL P&L', style: AppTextStyles.nano.copyWith(color: AppColors.textDim, letterSpacing: 0.8)),
                const SizedBox(height: 4),
                Text(
                  fmtMoney(ls.totalPnl),
                  style: AppTextStyles.value.copyWith(color: pnlColor(ls.totalPnl)),
                ),
              ],
            ),
          ),
        ),
      ],
    );
  }

  // ── Recent executions ────────────────────────────────────────────────────────

  Widget _buildRecentExecutions(List<Trade> trades) {
    return GlassCard(
      padding: const EdgeInsets.all(14),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Row(
            children: [
              Text(
                'RECENT EXECUTIONS',
                style: AppTextStyles.nano.copyWith(
                  color: AppColors.textDim,
                  letterSpacing: 1.0,
                  fontWeight: FontWeight.w700,
                ),
              ),
              const Spacer(),
              Text(
                '${trades.length}',
                style: AppTextStyles.nano.copyWith(color: AppColors.textFaint),
              ),
            ],
          ),
          const SizedBox(height: 8),
          if (trades.isEmpty)
            Padding(
              padding: const EdgeInsets.symmetric(vertical: 16),
              child: Text(
                'No executions recorded yet.',
                style: AppTextStyles.micro.copyWith(
                  color: AppColors.textFaint,
                  fontStyle: FontStyle.italic,
                ),
              ),
            )
          else
            ...trades.map((t) => _TradeRow(trade: t)),
        ],
      ),
    );
  }

  // ── Positions ────────────────────────────────────────────────────────────────

  Widget _buildPositions(LiveTradingState s) {
    final positions = s.liveState?.positions ?? [];
    return GlassCard(
      padding: const EdgeInsets.all(14),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Row(
            children: [
              Text(
                'ACTIVE POSITIONS',
                style: AppTextStyles.nano.copyWith(
                  color: AppColors.textDim,
                  letterSpacing: 1.0,
                  fontWeight: FontWeight.w700,
                ),
              ),
              const Spacer(),
              Text(
                '${positions.length}',
                style: AppTextStyles.nano.copyWith(color: AppColors.textFaint),
              ),
            ],
          ),
          const SizedBox(height: 8),
          if (positions.isEmpty)
            Padding(
              padding: const EdgeInsets.symmetric(vertical: 16),
              child: Text(
                'No open positions.',
                style: AppTextStyles.micro.copyWith(
                  color: AppColors.textFaint,
                  fontStyle: FontStyle.italic,
                ),
              ),
            )
          else
            ...positions.map((p) {
              final hist = s.positionHistoricals[p.symbol] ?? const [];
              return Padding(
                padding: const EdgeInsets.only(bottom: 10),
                child: PositionCard(
                  position: p,
                  chartStyle: _chartStyle,
                  range: s.currentRange,
                  historicals: hist,
                  onClose: () => _confirmClosePosition(p.symbol),
                ),
              );
            }),
        ],
      ),
    );
  }

  // ── Close position modal ─────────────────────────────────────────────────────

  void _confirmClosePosition(String symbol) {
    bool matched = false;
    showModalBottomSheet(
      context: context,
      isScrollControlled: true,
      backgroundColor: Colors.transparent,
      builder: (ctx) => StatefulBuilder(
        builder: (ctx, setModal) {
          return Container(
            padding: const EdgeInsets.all(20),
            decoration: BoxDecoration(
              color: AppColors.panel,
              borderRadius: const BorderRadius.vertical(top: Radius.circular(20)),
              border: Border.all(color: AppColors.stroke(AppColors.danger)),
            ),
            child: Column(
              mainAxisSize: MainAxisSize.min,
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Row(
                  children: [
                    Icon(Icons.logout, color: AppColors.danger, size: 20),
                    const SizedBox(width: 8),
                    Text('Close Position',
                        style: AppTextStyles.h3.copyWith(color: AppColors.textHi)),
                    const Spacer(),
                    IconButton(
                      icon: Icon(Icons.close, color: AppColors.textMuted, size: 20),
                      onPressed: () => Navigator.of(ctx).pop(),
                    ),
                  ],
                ),
                const SizedBox(height: 12),
                Text(
                  'Submit a market sell for the full quantity of ',
                  style: AppTextStyles.body,
                ),
                Text(symbol, style: AppTextStyles.bodyHi.copyWith(fontWeight: FontWeight.w800)),
                const SizedBox(height: 14),
                TypedConfirmField(
                  phrase: 'CLOSE $symbol',
                  onMatchChanged: (m) => setModal(() => matched = m),
                ),
                const SizedBox(height: 16),
                Row(
                  children: [
                    Expanded(
                      child: AppButton.ghost(
                        label: 'Cancel',
                        onPressed: () => Navigator.of(ctx).pop(),
                      ),
                    ),
                    const SizedBox(width: 12),
                    Expanded(
                      child: AppButton.semantic(
                        label: 'Submit Sell',
                        color: AppColors.danger,
                        onPressed: matched
                            ? () async {
                                Navigator.of(ctx).pop();
                                await _notifier.runCommand(
                                  'close_position',
                                  {'symbol': symbol},
                                );
                              }
                            : null,
                      ),
                    ),
                  ],
                ),
                SizedBox(height: MediaQuery.of(ctx).viewInsets.bottom),
              ],
            ),
          );
        },
      ),
    );
  }

  // ── Halt modal ───────────────────────────────────────────────────────────────

  Widget _buildHaltModal(BuildContext context) {
    return GestureDetector(
      onTap: () => setState(() => _haltModalOpen = false),
      child: Container(
        color: Colors.black.withValues(alpha: 0.65),
        child: Center(
          child: GestureDetector(
            onTap: () {}, // Prevent dismiss on inner tap
            child: ConstrainedBox(
              constraints: const BoxConstraints(maxWidth: 400),
              child: Padding(
                padding: const EdgeInsets.all(24),
                child: Container(
                  decoration: BoxDecoration(
                    color: AppColors.panel,
                    borderRadius: BorderRadius.circular(20),
                    border: Border.all(color: AppColors.stroke(AppColors.danger)),
                  ),
                  child: Column(
                    mainAxisSize: MainAxisSize.min,
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      Container(
                        padding: const EdgeInsets.fromLTRB(20, 16, 16, 16),
                        decoration: BoxDecoration(
                          border: Border(
                            bottom: BorderSide(
                              color: AppColors.stroke(AppColors.danger),
                            ),
                          ),
                        ),
                        child: Row(
                          children: [
                            Icon(symbol('block'), color: AppColors.danger, size: 20),
                            const SizedBox(width: 8),
                            Text(
                              'Halt Live Trading',
                              style: AppTextStyles.h3.copyWith(color: AppColors.textHi),
                            ),
                            const Spacer(),
                            IconButton(
                              icon: Icon(Icons.close, size: 18, color: AppColors.textMuted),
                              onPressed: () => setState(() => _haltModalOpen = false),
                            ),
                          ],
                        ),
                      ),
                      Padding(
                        padding: const EdgeInsets.all(20),
                        child: Column(
                          crossAxisAlignment: CrossAxisAlignment.start,
                          children: [
                            Container(
                              padding: const EdgeInsets.all(10),
                              decoration: BoxDecoration(
                                color: AppColors.fill(AppColors.warning),
                                borderRadius: BorderRadius.circular(8),
                                border: Border.all(
                                    color: AppColors.stroke(AppColors.warning)),
                              ),
                              child: Text(
                                'This cancels all open orders and stops the running broker. It does NOT liquidate open positions.',
                                style: AppTextStyles.nano.copyWith(
                                  color: AppColors.warning,
                                ),
                              ),
                            ),
                            const SizedBox(height: 14),
                            Text(
                              'REASON (OPTIONAL)',
                              style: AppTextStyles.nano.copyWith(
                                color: AppColors.textDim,
                                letterSpacing: 0.6,
                                fontWeight: FontWeight.w700,
                              ),
                            ),
                            const SizedBox(height: 6),
                            TextField(
                              controller: _haltReasonCtrl,
                              style: AppTextStyles.body.copyWith(color: AppColors.textHi),
                              decoration: InputDecoration(
                                hintText: 'e.g. risk breach',
                                hintStyle: AppTextStyles.body.copyWith(
                                  color: AppColors.textFaint,
                                ),
                                filled: true,
                                fillColor: AppColors.panelAlt,
                                border: OutlineInputBorder(
                                  borderRadius: BorderRadius.circular(6),
                                  borderSide: BorderSide(color: AppColors.border),
                                ),
                                enabledBorder: OutlineInputBorder(
                                  borderRadius: BorderRadius.circular(6),
                                  borderSide: BorderSide(color: AppColors.border),
                                ),
                              ),
                            ),
                            const SizedBox(height: 14),
                            StatefulBuilder(
                              builder: (ctx, setInner) {
                                return TypedConfirmField(
                                  phrase: 'HALT',
                                  onMatchChanged: (m) {
                                    setState(() => _haltConfirmed = m);
                                  },
                                );
                              },
                            ),
                          ],
                        ),
                      ),
                      Padding(
                        padding: const EdgeInsets.fromLTRB(20, 0, 20, 20),
                        child: Row(
                          children: [
                            Expanded(
                              child: AppButton.ghost(
                                label: 'Cancel',
                                onPressed: () =>
                                    setState(() => _haltModalOpen = false),
                              ),
                            ),
                            const SizedBox(width: 12),
                            Expanded(
                              child: AppButton.semantic(
                                label: 'Halt Now',
                                color: AppColors.danger,
                                onPressed: _haltConfirmed
                                    ? () async {
                                        setState(() => _haltModalOpen = false);
                                        await _notifier.runCommand('halt', {
                                          'reason': _haltReasonCtrl.text.trim().isNotEmpty
                                              ? _haltReasonCtrl.text.trim()
                                              : 'manual halt via UI',
                                        });
                                      }
                                    : null,
                              ),
                            ),
                          ],
                        ),
                      ),
                    ],
                  ),
                ),
              ),
            ),
          ),
        ),
      ),
    );
  }

  // ── Sticky halt pill ─────────────────────────────────────────────────────────

  Widget _buildStickyHalt(LiveTradingState? s) {
    if (s == null || s.notRunning) return const SizedBox.shrink();
    return Positioned(
      bottom: 16,
      right: 16,
      child: GestureDetector(
        onTap: () => setState(() => _haltModalOpen = true),
        child: Container(
          padding: const EdgeInsets.symmetric(horizontal: 18, vertical: 12),
          decoration: BoxDecoration(
            color: AppColors.fill(AppColors.danger).withValues(alpha: 0.25),
            borderRadius: BorderRadius.circular(999),
            border: Border.all(color: AppColors.danger.withValues(alpha: 0.4)),
            boxShadow: [
              BoxShadow(
                color: AppColors.danger.withValues(alpha: 0.20),
                blurRadius: 12,
                spreadRadius: 2,
              ),
            ],
          ),
          child: Row(
            mainAxisSize: MainAxisSize.min,
            children: [
              Icon(symbol('block'), color: AppColors.danger, size: 18),
              const SizedBox(width: 6),
              Text(
                'Halt',
                style: AppTextStyles.cardTitle.copyWith(
                  color: AppColors.danger,
                  fontWeight: FontWeight.w700,
                ),
              ),
            ],
          ),
        ),
      ),
    );
  }

  // ── Command toast ────────────────────────────────────────────────────────────

  Widget _buildCommandToast(CommandToast toast) {
    final Color toastColor;
    final IconData toastIcon;
    if (toast.isPending) {
      toastColor = AppColors.info;
      toastIcon = symbol('progress_activity');
    } else if (toast.status == 'completed') {
      toastColor = AppColors.success;
      toastIcon = symbol('check_circle');
    } else {
      toastColor = AppColors.danger;
      toastIcon = symbol('error');
    }

    return Positioned(
      bottom: 80,
      left: 16,
      right: 16,
      child: Material(
        color: Colors.transparent,
        child: Container(
          padding: const EdgeInsets.all(12),
          decoration: BoxDecoration(
            color: AppColors.panelAlt,
            borderRadius: BorderRadius.circular(12),
            border: Border.all(color: AppColors.stroke(toastColor)),
            boxShadow: [
              BoxShadow(
                color: Colors.black.withValues(alpha: 0.4),
                blurRadius: 16,
              ),
            ],
          ),
          child: Row(
            children: [
              Icon(toastIcon, color: toastColor, size: 18),
              const SizedBox(width: 8),
              Expanded(
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  mainAxisSize: MainAxisSize.min,
                  children: [
                    Text(
                      '${toast.type.toUpperCase()} · ${toast.status.toUpperCase()}',
                      style: AppTextStyles.nano.copyWith(
                        color: AppColors.textHi,
                        fontWeight: FontWeight.w700,
                        letterSpacing: 0.5,
                      ),
                    ),
                    if (toast.error != null)
                      Text(
                        toast.error!,
                        style: AppTextStyles.nano.copyWith(color: AppColors.danger),
                      )
                    else if (toast.result != null && toast.result!.isNotEmpty)
                      Text(
                        toast.result.toString(),
                        style: AppTextStyles.nano.copyWith(color: AppColors.textMuted),
                        overflow: TextOverflow.ellipsis,
                      ),
                  ],
                ),
              ),
              IconButton(
                icon: Icon(Icons.close, size: 16, color: AppColors.textMuted),
                onPressed: () => _notifier.dismissToast(),
                padding: EdgeInsets.zero,
                constraints: const BoxConstraints(minWidth: 28, minHeight: 28),
              ),
            ],
          ),
        ),
      ),
    );
  }
}

// ── Trade row ─────────────────────────────────────────────────────────────────

class _TradeRow extends StatelessWidget {
  const _TradeRow({required this.trade});
  final Trade trade;

  @override
  Widget build(BuildContext context) {
    final isBuy = trade.side.toLowerCase() == 'buy';
    final sideColor = isBuy ? AppColors.chartUp : AppColors.chartDown;
    final total = trade.price * trade.qty;

    return Container(
      margin: const EdgeInsets.only(bottom: 8),
      padding: const EdgeInsets.all(10),
      decoration: BoxDecoration(
        color: AppColors.surface.withValues(alpha: 0.5),
        borderRadius: BorderRadius.circular(8),
        border: Border.all(color: AppColors.border),
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Row(
            children: [
              Text(
                trade.side.toUpperCase(),
                style: AppTextStyles.cardTitle.copyWith(
                  color: sideColor,
                  fontWeight: FontWeight.w800,
                  letterSpacing: 0.4,
                ),
              ),
              const SizedBox(width: 8),
              Text(
                trade.symbol,
                style: AppTextStyles.cardTitle.copyWith(
                  color: AppColors.textHi,
                  fontWeight: FontWeight.w800,
                ),
              ),
              const Spacer(),
              Column(
                crossAxisAlignment: CrossAxisAlignment.end,
                children: [
                  Text(
                    'FILL PRICE',
                    style: AppTextStyles.nano.copyWith(
                      color: AppColors.textDim,
                      letterSpacing: 0.5,
                    ),
                  ),
                  Text(
                    fmtMoney(trade.price),
                    style: AppTextStyles.value.copyWith(
                      color: AppColors.textHi,
                      fontWeight: FontWeight.w800,
                      fontSize: 15,
                    ),
                  ),
                ],
              ),
            ],
          ),
          const SizedBox(height: 6),
          Row(
            children: [
              _TradeField(label: 'WHEN', value: fmtDateTime(trade.ts)),
              _TradeField(
                label: 'SHARES',
                value: trade.qty.toStringAsFixed(4),
              ),
              _TradeField(label: 'TOTAL', value: fmtMoney(total)),
            ],
          ),
        ],
      ),
    );
  }
}

class _TradeField extends StatelessWidget {
  const _TradeField({required this.label, required this.value});
  final String label;
  final String value;

  @override
  Widget build(BuildContext context) {
    return Expanded(
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Text(
            label,
            style: AppTextStyles.nano.copyWith(
              color: AppColors.textDim,
              letterSpacing: 0.5,
            ),
          ),
          Text(
            value,
            style: AppTextStyles.micro.copyWith(
              color: AppColors.textHi,
              fontWeight: FontWeight.w700,
            ),
            overflow: TextOverflow.ellipsis,
          ),
        ],
      ),
    );
  }
}

// ── Small helpers ─────────────────────────────────────────────────────────────

class _WarningChip extends StatelessWidget {
  const _WarningChip({
    required this.icon,
    required this.label,
    required this.color,
  });

  final IconData icon;
  final String label;
  final Color color;

  @override
  Widget build(BuildContext context) {
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 4),
      decoration: BoxDecoration(
        color: AppColors.fill(color),
        borderRadius: BorderRadius.circular(6),
        border: Border.all(color: AppColors.stroke(color)),
      ),
      child: Row(
        mainAxisSize: MainAxisSize.min,
        children: [
          Icon(icon, size: 12, color: color),
          const SizedBox(width: 4),
          Text(
            label,
            style: AppTextStyles.nano.copyWith(color: color, fontWeight: FontWeight.w600),
          ),
        ],
      ),
    );
  }
}

class _MiniStat extends StatelessWidget {
  const _MiniStat({required this.label, required this.value, this.color});
  final String label;
  final String value;
  final Color? color;

  @override
  Widget build(BuildContext context) {
    return Expanded(
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Text(
            label,
            style: AppTextStyles.nano.copyWith(
              color: AppColors.textDim,
              letterSpacing: 0.5,
            ),
          ),
          const SizedBox(height: 2),
          Text(
            value,
            style: AppTextStyles.micro.copyWith(
              color: color ?? AppColors.textMd,
              fontWeight: FontWeight.w700,
            ),
            overflow: TextOverflow.ellipsis,
          ),
        ],
      ),
    );
  }
}

class _StyleToggleBtn extends StatelessWidget {
  const _StyleToggleBtn({
    required this.icon,
    required this.active,
    required this.onTap,
  });

  final String icon;
  final bool active;
  final VoidCallback onTap;

  @override
  Widget build(BuildContext context) {
    return GestureDetector(
      onTap: onTap,
      child: Container(
        width: 30,
        height: 30,
        decoration: BoxDecoration(
          color: active ? AppColors.border : Colors.transparent,
          borderRadius: BorderRadius.circular(6),
        ),
        child: Icon(
          symbol(icon),
          size: 16,
          color: active ? AppColors.textHi : AppColors.textDim,
        ),
      ),
    );
  }
}
