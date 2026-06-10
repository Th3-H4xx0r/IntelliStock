import 'dart:convert';
import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';
import 'package:syncfusion_flutter_charts/charts.dart';
import '../../../core/formatters/formatters.dart';
import '../../../core/theme/app_colors.dart';
import '../../../core/theme/app_text_styles.dart';
import '../../../core/widgets/app_background.dart';
import '../../../core/widgets/common_widgets.dart';
import '../../../core/widgets/glass_card.dart';
import '../../../core/widgets/material_symbols.dart';
import '../../../core/widgets/skeleton.dart';
import '../application/token_usage_controller.dart';
import '../data/token_usage_repository.dart';

// ── Helpers ────────────────────────────────────────────────────────────────────

const double _kMaxPlanBudget = 100.0;

enum _TelemetryState { awaiting, healthy, degraded, lagging }

_TelemetryState _telemetryStateFor(TelemetryHealth? h) {
  if (h == null) return _TelemetryState.awaiting;
  if (h.writeErrors24h > 0) return _TelemetryState.degraded;
  if ((h.lastFlushAgeS ?? 0) > 30) return _TelemetryState.lagging;
  return _TelemetryState.healthy;
}

String _telemetryLabel(_TelemetryState s) {
  switch (s) {
    case _TelemetryState.awaiting: return 'Awaiting data';
    case _TelemetryState.healthy: return 'Healthy';
    case _TelemetryState.degraded: return 'Degraded';
    case _TelemetryState.lagging: return 'Lagging';
  }
}

Color _telemetryColor(_TelemetryState s) {
  switch (s) {
    case _TelemetryState.awaiting: return AppColors.textDim;
    case _TelemetryState.healthy: return AppColors.success;
    case _TelemetryState.degraded: return AppColors.danger;
    case _TelemetryState.lagging: return AppColors.warning;
  }
}

// ── Screen ─────────────────────────────────────────────────────────────────────

class TokenUsageScreen extends ConsumerWidget {
  const TokenUsageScreen({super.key});

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final state = ref.watch(tokenUsageControllerProvider);
    final controller = ref.read(tokenUsageControllerProvider.notifier);

    return Scaffold(
      backgroundColor: AppColors.canvas,
      body: AppBackground(
        child: SafeArea(
          child: Column(children: [
            // App bar
            Padding(
              padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 10),
              child: Row(children: [
                IconButton(
                  icon: const Icon(Icons.arrow_back, size: 22),
                  color: AppColors.textMuted,
                  onPressed: () => Navigator.of(context).pop(),
                ),
                const Spacer(),
                state.when(
                  loading: () => Skeleton(width: 22, height: 22, radius: 11),
                  error: (_, _) => const SizedBox.shrink(),
                  data: (_) => const SizedBox.shrink(),
                ),
                IconButton(
                  icon: const Icon(Icons.refresh, size: 22),
                  color: AppColors.textMuted,
                  onPressed: () => controller.refreshNow(),
                ),
              ]),
            ),
            // Content
            Expanded(
              child: RefreshIndicator(
                color: AppColors.primary,
                backgroundColor: AppColors.surface,
                onRefresh: () => controller.refreshNow(),
                child: state.when(
                  loading: () => const _TokenUsageSkeleton(),
                  error: (e, _) => ListView(
                    padding: const EdgeInsets.all(20),
                    children: [
                      ErrorBanner(message: e.toString(), onRetry: () => controller.refreshNow()),
                    ],
                  ),
                  data: (data) => _Body(data: data),
                ),
              ),
            ),
          ]),
        ),
      ),
    );
  }
}

// ── Loading skeleton ───────────────────────────────────────────────────────────

class _TokenUsageSkeleton extends StatelessWidget {
  const _TokenUsageSkeleton();

  @override
  Widget build(BuildContext context) {
    return ListView(
      padding: const EdgeInsets.symmetric(horizontal: 20),
      children: [
        const SizedBox(height: 4),
        // Header row skeleton
        Row(crossAxisAlignment: CrossAxisAlignment.start, children: [
          Skeleton.circle(40),
          const SizedBox(width: 12),
          Expanded(
            child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
              Row(children: [
                Skeleton(width: 120, height: 20),
                const SizedBox(width: 8),
                Skeleton(width: 70, height: 18, radius: 9),
              ]),
              const SizedBox(height: 6),
              Skeleton(width: double.infinity, height: 11),
            ]),
          ),
        ]),
        const SizedBox(height: 12),
        // Range toggle skeleton
        Row(children: [
          Skeleton(width: 48, height: 32, radius: 10),
          const SizedBox(width: 6),
          Skeleton(width: 42, height: 32, radius: 10),
          const SizedBox(width: 6),
          Skeleton(width: 48, height: 32, radius: 10),
        ]),
        const SizedBox(height: 20),
        // KPI grid — 2×2
        Row(children: [
          Expanded(child: GlassCard(padding: const EdgeInsets.all(16), child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Skeleton(width: 80, height: 10),
              const SizedBox(height: 8),
              Skeleton(width: 100, height: 22),
              const SizedBox(height: 6),
              Skeleton(width: double.infinity, height: 10),
              const SizedBox(height: 8),
              Row(children: [
                Skeleton(width: 50, height: 18, radius: 4),
                const SizedBox(width: 4),
                Skeleton(width: 50, height: 18, radius: 4),
              ]),
            ],
          ))),
          const SizedBox(width: 12),
          Expanded(child: GlassCard(padding: const EdgeInsets.all(16), child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Skeleton(width: 80, height: 10),
              const SizedBox(height: 8),
              Skeleton(width: 60, height: 22),
              const SizedBox(height: 8),
              Row(children: [
                Expanded(child: Skeleton(height: 30)),
                const SizedBox(width: 6),
                Expanded(child: Skeleton(height: 30)),
              ]),
            ],
          ))),
        ]),
        const SizedBox(height: 12),
        Row(children: [
          Expanded(child: GlassCard(padding: const EdgeInsets.all(16), child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Skeleton(width: 110, height: 10),
              const SizedBox(height: 8),
              Skeleton(width: 80, height: 22),
              const SizedBox(height: 8),
              Skeleton(width: double.infinity, height: 6, radius: 3),
              const SizedBox(height: 4),
              Skeleton(width: 150, height: 10),
            ],
          ))),
          const SizedBox(width: 12),
          Expanded(child: GlassCard(padding: const EdgeInsets.all(16), child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Skeleton(width: 120, height: 10),
              const SizedBox(height: 8),
              Row(children: [
                Expanded(child: Skeleton(height: 30)),
                const SizedBox(width: 4),
                Expanded(child: Skeleton(height: 30)),
                const SizedBox(width: 4),
                Expanded(child: Skeleton(height: 30)),
              ]),
            ],
          ))),
        ]),
        const SizedBox(height: 20),
        // Spend trend chart block skeleton
        GlassCard(
          padding: const EdgeInsets.all(16),
          child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
            Row(children: [
              Expanded(child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
                Skeleton(width: 90, height: 10),
                const SizedBox(height: 4),
                Skeleton(width: 110, height: 16),
              ])),
              Skeleton(width: 110, height: 10),
            ]),
            const SizedBox(height: 16),
            Skeleton(width: double.infinity, height: 200, radius: 12),
          ]),
        ),
        const SizedBox(height: 20),
        // Ranking rows section header + 3 rows
        Skeleton(width: 180, height: 16),
        const SizedBox(height: 8),
        GlassCard(
          padding: EdgeInsets.zero,
          child: Column(children: [
            for (int i = 0; i < 3; i++) ...[
              Padding(
                padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 12),
                child: Row(children: [
                  Expanded(child: Skeleton(height: 12)),
                  const SizedBox(width: 12),
                  Skeleton(width: 40, height: 12),
                  const SizedBox(width: 12),
                  Skeleton(width: 60, height: 12),
                  const SizedBox(width: 12),
                  Skeleton(width: 50, height: 12),
                ]),
              ),
              if (i < 2) Divider(height: 1, color: AppColors.border),
            ],
          ]),
        ),
        const SizedBox(height: 20),
        Skeleton(width: 200, height: 16),
        const SizedBox(height: 8),
        GlassCard(
          padding: EdgeInsets.zero,
          child: Column(children: [
            for (int i = 0; i < 3; i++) ...[
              Padding(
                padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 12),
                child: Row(children: [
                  Expanded(child: Skeleton(height: 12)),
                  const SizedBox(width: 12),
                  Skeleton(width: 40, height: 12),
                  const SizedBox(width: 12),
                  Skeleton(width: 60, height: 12),
                  const SizedBox(width: 12),
                  Skeleton(width: 50, height: 12),
                ]),
              ),
              if (i < 2) Divider(height: 1, color: AppColors.border),
            ],
          ]),
        ),
        const SizedBox(height: 40),
      ],
    );
  }
}

// ── Body ───────────────────────────────────────────────────────────────────────

class _Body extends ConsumerStatefulWidget {
  const _Body({required this.data});
  final TokenUsageData data;

  @override
  ConsumerState<_Body> createState() => _BodyState();
}

class _BodyState extends ConsumerState<_Body> {
  RecentCall? _selectedCall;

  @override
  Widget build(BuildContext context) {
    final data = widget.data;
    final ts = _telemetryStateFor(data.summary?.telemetryHealth);
    final controller = ref.read(tokenUsageControllerProvider.notifier);
    final range = controller.range;

    return Stack(children: [
      ListView(
        padding: const EdgeInsets.symmetric(horizontal: 20),
        children: [
          // ── Header ────────────────────────────────────────────────────────
          const SizedBox(height: 4),
          Row(crossAxisAlignment: CrossAxisAlignment.start, children: [
            IconTile(icon: symbol('payments'), color: AppColors.primary),
            const SizedBox(width: 12),
            Expanded(
              child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
                Row(children: [
                  Text('Token Usage', style: AppTextStyles.h2),
                  const SizedBox(width: 8),
                  _TelemetryPill(state: ts),
                ]),
                const SizedBox(height: 4),
                Text(
                  'Live telemetry across providers, models, and strategy call sites.',
                  style: AppTextStyles.meta.copyWith(color: AppColors.textDim),
                ),
              ]),
            ),
          ]),
          const SizedBox(height: 12),
          // Range toggle + refresh
          Row(children: [
            for (final r in ['24h', '7d', '30d']) ...[
              _RangeButton(
                label: r,
                selected: range == r,
                onTap: () => ref.read(tokenUsageControllerProvider.notifier).setRange(r),
              ),
              const SizedBox(width: 6),
            ],
          ]),
          if (data.partialError != null) ...[
            const SizedBox(height: 12),
            ErrorBanner(message: data.partialError!),
          ],
          const SizedBox(height: 20),

          // ── KPI cards ─────────────────────────────────────────────────────
          _KpiGrid(data: data, range: range),
          const SizedBox(height: 20),

          // ── Spend trend chart ─────────────────────────────────────────────
          _SpendTrendCard(timeseries: data.timeseries),
          const SizedBox(height: 20),

          // ── Top spenders ──────────────────────────────────────────────────
          Text('Top spenders by model', style: AppTextStyles.h3),
          const SizedBox(height: 8),
          _SpenderCards(rows: data.topByModel, emptyMsg: 'No model spend recorded yet.'),
          const SizedBox(height: 20),
          Text('Top spenders by call site', style: AppTextStyles.h3),
          const SizedBox(height: 8),
          _SpenderCards(rows: data.topByCallSite, emptyMsg: 'No call-site spend recorded yet.'),
          const SizedBox(height: 20),

          // ── By backtest ───────────────────────────────────────────────────
          Text('LLM cost by run', style: AppTextStyles.h3),
          const SizedBox(height: 8),
          _BacktestRows(rows: data.byBacktest),
          const SizedBox(height: 20),

          // ── Recent calls ──────────────────────────────────────────────────
          Text('Recent calls', style: AppTextStyles.h3),
          const SizedBox(height: 8),
          _RecentCallCards(
            calls: data.recentCalls,
            onTap: (c) => setState(() => _selectedCall = c),
          ),
          const SizedBox(height: 40),
        ],
      ),

      // ── Call detail dialog ─────────────────────────────────────────────────
      if (_selectedCall != null)
        _CallDetailOverlay(
          call: _selectedCall!,
          onClose: () => setState(() => _selectedCall = null),
        ),
    ]);
  }
}

// ── Telemetry pill ─────────────────────────────────────────────────────────────

class _TelemetryPill extends StatelessWidget {
  const _TelemetryPill({required this.state});
  final _TelemetryState state;

  @override
  Widget build(BuildContext context) {
    final color = _telemetryColor(state);
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 3),
      decoration: BoxDecoration(
        color: AppColors.fill(color),
        borderRadius: BorderRadius.circular(100),
        border: Border.all(color: AppColors.stroke(color)),
      ),
      child: Row(mainAxisSize: MainAxisSize.min, children: [
        Container(width: 6, height: 6, decoration: BoxDecoration(color: color, shape: BoxShape.circle)),
        const SizedBox(width: 4),
        Text(_telemetryLabel(state),
            style: AppTextStyles.nano.copyWith(
                color: color, fontWeight: FontWeight.w600, letterSpacing: 0.5)),
      ]),
    );
  }
}

// ── Range button ───────────────────────────────────────────────────────────────

class _RangeButton extends StatelessWidget {
  const _RangeButton({required this.label, required this.selected, required this.onTap});
  final String label;
  final bool selected;
  final VoidCallback onTap;

  @override
  Widget build(BuildContext context) {
    return GestureDetector(
      onTap: onTap,
      child: Container(
        padding: const EdgeInsets.symmetric(horizontal: 14, vertical: 7),
        decoration: BoxDecoration(
          color: selected ? AppColors.fill(AppColors.primary) : AppColors.surface.withValues(alpha: 0.6),
          borderRadius: BorderRadius.circular(10),
          border: Border.all(color: selected ? AppColors.stroke(AppColors.primary) : AppColors.border),
        ),
        child: Text(
          label,
          style: AppTextStyles.meta.copyWith(
              color: selected ? AppColors.primary : AppColors.textMd,
              fontWeight: FontWeight.w500),
        ),
      ),
    );
  }
}

// ── KPI grid ───────────────────────────────────────────────────────────────────

class _KpiGrid extends StatelessWidget {
  const _KpiGrid({required this.data, required this.range});
  final TokenUsageData data;
  final String range;

  @override
  Widget build(BuildContext context) {
    final s = data.summary;
    final totalCalls = s?.totalCalls ?? 0;
    final totalTokens = s?.totalTokens ?? 0;
    final totalCost = s?.totalCostUsd ?? 0.0;
    final avgCost = totalCalls > 0 ? totalCost / totalCalls : 0.0;
    final maxPlanUsd = s?.maxPlanEstimateUsd ?? 0.0;
    final maxPlanPct = _kMaxPlanBudget > 0 ? (maxPlanUsd / _kMaxPlanBudget).clamp(0.0, 1.0) : 0.0;
    final health = s?.telemetryHealth;

    final topProviders = [...(s?.byProvider ?? [])]
      ..sort((a, b) => (b.costUsd ?? 0).compareTo(a.costUsd ?? 0));
    final topThree = topProviders.take(3).toList();

    return Column(children: [
      // Row 1: Period cost + Period calls
      Row(children: [
        Expanded(child: GlassCard(padding: const EdgeInsets.all(16), child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Text('PERIOD COST', style: AppTextStyles.eyebrow),
            const SizedBox(height: 8),
            Text(fmtUsdCost(totalCost), style: AppTextStyles.valueLg),
            const SizedBox(height: 4),
            Text('${fmtTokens(totalTokens)} tokens · $totalCalls calls',
                style: AppTextStyles.meta.copyWith(color: AppColors.textMuted)),
            const SizedBox(height: 8),
            Wrap(spacing: 4, runSpacing: 4, children: topThree.isNotEmpty
              ? topThree.map((p) => _providerPill(p)).toList()
              : [Text('No provider spend', style: AppTextStyles.nano.copyWith(color: AppColors.textFaint))],
            ),
          ],
        ))),
        const SizedBox(width: 12),
        Expanded(child: GlassCard(padding: const EdgeInsets.all(16), child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Text('PERIOD CALLS', style: AppTextStyles.eyebrow),
            const SizedBox(height: 8),
            Text('$totalCalls', style: AppTextStyles.valueLg),
            const SizedBox(height: 8),
            Row(children: [
              Expanded(child: _mini('Avg cost', fmtUsdCost(avgCost))),
              Expanded(child: _mini('Recent rows', '${data.recentCalls.length}')),
            ]),
          ],
        ))),
      ]),
      const SizedBox(height: 12),
      // Row 2: Max plan estimate + Telemetry health
      Row(children: [
        Expanded(child: GlassCard(padding: const EdgeInsets.all(16), child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Text('MAX PLAN ESTIMATE', style: AppTextStyles.eyebrow),
            const SizedBox(height: 8),
            Text(fmtUsdCost(maxPlanUsd), style: AppTextStyles.valueLg),
            const SizedBox(height: 8),
            ClipRRect(
              borderRadius: BorderRadius.circular(4),
              child: LinearProgressIndicator(
                value: maxPlanPct,
                backgroundColor: AppColors.surface,
                valueColor: AlwaysStoppedAnimation<Color>(const Color(0xFFA78BFA).withValues(alpha: 0.8)),
                minHeight: 6,
              ),
            ),
            const SizedBox(height: 4),
            Text('${(maxPlanPct * 100).round()}% of \$${_kMaxPlanBudget.toInt()} Claude Max budget',
                style: AppTextStyles.nano.copyWith(color: AppColors.textMuted)),
          ],
        ))),
        const SizedBox(width: 12),
        Expanded(child: GlassCard(padding: const EdgeInsets.all(16), child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Text('TELEMETRY HEALTH', style: AppTextStyles.eyebrow,
                maxLines: 1, overflow: TextOverflow.ellipsis),
            const SizedBox(height: 8),
            Row(children: [
              Expanded(child: _mini('Buffer', '${health?.bufferDepth ?? '—'}')),
              Expanded(child: _mini('Last flush', health?.lastFlushAgeS != null ? '${health!.lastFlushAgeS}s' : '—')),
              Expanded(child: _mini('Errors 24h', '${health?.writeErrors24h ?? 0}')),
            ]),
          ],
        ))),
      ]),
    ]);
  }

  Widget _providerPill(ProviderBreakdown p) {
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 6, vertical: 2),
      decoration: BoxDecoration(
        color: AppColors.surface.withValues(alpha: 0.7),
        borderRadius: BorderRadius.circular(4),
        border: Border.all(color: AppColors.border),
      ),
      child: Text(
        '${p.provider} · ${fmtUsdCost(p.costUsd)}',
        style: AppTextStyles.nano.copyWith(color: AppColors.textMd),
      ),
    );
  }

  Widget _mini(String label, String value) {
    return Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
      Text(label.toUpperCase(),
          style: AppTextStyles.nano.copyWith(color: AppColors.textFaint, letterSpacing: 0.5)),
      const SizedBox(height: 2),
      Text(value, style: AppTextStyles.meta.copyWith(color: AppColors.textMd)),
    ]);
  }
}

// ── Spend trend chart ─────────────────────────────────────────────────────────

class _ChartPoint {
  const _ChartPoint(this.x, this.y);
  final DateTime x;
  final double y;
}

class _SpendTrendCard extends StatelessWidget {
  const _SpendTrendCard({required this.timeseries});
  final List<TimeseriesRow> timeseries;

  List<StackedColumnSeries<_ChartPoint, DateTime>> _buildSeries() {
    final grouped = <String, Map<int, double>>{};
    for (final row in timeseries) {
      grouped.putIfAbsent(row.provider, () => {});
      final bucket = grouped[row.provider]!;
      bucket[row.bucketStartTs] = (bucket[row.bucketStartTs] ?? 0) + (row.costUsd ?? 0);
    }

    final providerColors = [
      AppColors.primary, AppColors.info, AppColors.success,
      AppColors.warning, AppColors.teal, AppColors.danger,
    ];
    int colorIdx = 0;

    return grouped.entries.map((e) {
      final color = providerColors[colorIdx % providerColors.length];
      colorIdx++;
      final pts = e.value.entries
          .map((kv) => _ChartPoint(DateTime.fromMillisecondsSinceEpoch(kv.key), kv.value))
          .toList()
        ..sort((a, b) => a.x.compareTo(b.x));
      return StackedColumnSeries<_ChartPoint, DateTime>(
        name: e.key,
        dataSource: pts,
        xValueMapper: (d, _) => d.x,
        yValueMapper: (d, _) => d.y,
        color: color,
        borderRadius: const BorderRadius.vertical(top: Radius.circular(3)),
      );
    }).toList();
  }

  @override
  Widget build(BuildContext context) {
    final series = _buildSeries();
    final hasData = series.isNotEmpty;

    return GlassCard(
      padding: const EdgeInsets.all(16),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Row(children: [
            Expanded(child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
              Text('SPEND TREND', style: AppTextStyles.eyebrow),
              const SizedBox(height: 2),
              Text('Cost over time', style: AppTextStyles.h3),
            ])),
            Text('Stacked by provider', style: AppTextStyles.nano.copyWith(color: AppColors.textDim)),
          ]),
          const SizedBox(height: 16),
          hasData
              ? SizedBox(
                  height: 240,
                  child: SfCartesianChart(
                  backgroundColor: Colors.transparent,
                  plotAreaBorderWidth: 0,
                  margin: EdgeInsets.zero,
                  legend: Legend(
                    isVisible: true,
                    position: LegendPosition.top,
                    textStyle: TextStyle(color: AppColors.textMd, fontSize: 10),
                    overflowMode: LegendItemOverflowMode.wrap,
                  ),
                  primaryXAxis: DateTimeAxis(
                    majorGridLines: MajorGridLines(color: AppColors.chartGrid),
                    axisLine: const AxisLine(width: 0),
                    labelStyle: TextStyle(color: AppColors.chartAxis, fontSize: 9),
                  ),
                  primaryYAxis: NumericAxis(
                    title: AxisTitle(
                      text: 'Cost (USD)',
                      textStyle: TextStyle(color: AppColors.chartAxis, fontSize: 9),
                    ),
                    majorGridLines: MajorGridLines(
                        color: AppColors.chartGrid, dashArray: const [3, 3]),
                    axisLine: const AxisLine(width: 0),
                    labelStyle: TextStyle(color: AppColors.chartAxis, fontSize: 9),
                    axisLabelFormatter: (AxisLabelRenderDetails args) {
                      final v = args.value;
                      final label = v < 1 ? '\$${v.toStringAsFixed(4)}' : '\$${v.toStringAsFixed(2)}';
                      return ChartAxisLabel(label, TextStyle(color: AppColors.chartAxis, fontSize: 9));
                    },
                  ),
                  tooltipBehavior: TooltipBehavior(enable: true),
                  series: series,
                ))
              : Container(
                  height: 180,
                  decoration: BoxDecoration(
                    color: AppColors.surface.withValues(alpha: 0.3),
                    borderRadius: BorderRadius.circular(12),
                    border: Border.all(color: AppColors.border, style: BorderStyle.solid),
                  ),
                  child: Center(
                    child: Column(mainAxisSize: MainAxisSize.min, children: [
                      Text('No usage in this window.', style: AppTextStyles.meta.copyWith(color: AppColors.textMd)),
                      const SizedBox(height: 4),
                      Text('Calls will appear here after telemetry flushes.',
                          style: AppTextStyles.nano.copyWith(color: AppColors.textDim)),
                    ]),
                  ),
                ),
        ],
      ),
    );
  }
}

// ── Spender cards ──────────────────────────────────────────────────────────────

class _SpenderCards extends StatelessWidget {
  const _SpenderCards({required this.rows, required this.emptyMsg});
  final List<SpenderRow> rows;
  final String emptyMsg;

  @override
  Widget build(BuildContext context) {
    if (rows.isEmpty) {
      return GlassCard(
        padding: const EdgeInsets.all(16),
        child: Center(child: Text(emptyMsg,
            style: AppTextStyles.meta.copyWith(color: AppColors.textDim))),
      );
    }
    return GlassCard(
      padding: const EdgeInsets.all(0),
      child: Column(
        children: rows.asMap().entries.map((e) {
          final row = e.value;
          final isLast = e.key == rows.length - 1;
          return Column(children: [
            Padding(
              padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 10),
              child: Row(children: [
                Expanded(child: Text(row.key,
                    style: AppTextStyles.mono(12, color: AppColors.textMd),
                    overflow: TextOverflow.ellipsis)),
                const SizedBox(width: 8),
                _mini('${row.calls ?? 0}', 'calls'),
                const SizedBox(width: 12),
                _mini(fmtTokens(row.tokens), 'tokens'),
                const SizedBox(width: 12),
                Text(fmtUsdCost(row.costUsd), style: AppTextStyles.meta.copyWith(color: AppColors.textHi, fontWeight: FontWeight.w600)),
              ]),
            ),
            if (!isLast) Divider(height: 1, color: AppColors.border),
          ]);
        }).toList(),
      ),
    );
  }

  Widget _mini(String value, String label) {
    return Column(mainAxisSize: MainAxisSize.min, children: [
      Text(value, style: AppTextStyles.meta.copyWith(color: AppColors.textMd)),
      Text(label, style: AppTextStyles.nano.copyWith(color: AppColors.textFaint)),
    ]);
  }
}

// ── By-backtest rows ──────────────────────────────────────────────────────────

class _BacktestRows extends StatelessWidget {
  const _BacktestRows({required this.rows});
  final List<BacktestUsageRow> rows;

  @override
  Widget build(BuildContext context) {
    if (rows.isEmpty) {
      return GlassCard(
        padding: const EdgeInsets.all(16),
        child: Center(child: Text('No LLM cost data in this range.',
            style: AppTextStyles.meta.copyWith(color: AppColors.textDim))),
      );
    }
    return Column(
      children: rows.map((row) {
        final isBacktest = (row.kind ?? 'backtest') == 'backtest';
        return Padding(
          padding: const EdgeInsets.only(bottom: 8),
          child: GlassCard(
            padding: const EdgeInsets.all(14),
            onTap: isBacktest && row.backtestId != null
                ? () => GoRouter.of(context).push('/backtests/${row.backtestId}')
                : null,
            child: Row(children: [
              Expanded(child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
                Text(
                  row.displayLabel ?? '#${row.backtestId ?? '?'}',
                  style: AppTextStyles.mono(12, color: AppColors.textMd),
                  overflow: TextOverflow.ellipsis,
                ),
                const SizedBox(height: 2),
                Row(children: [
                  _kindBadge(row.kind ?? 'backtest'),
                  const SizedBox(width: 8),
                  if (row.instanceId != null)
                    Text(row.instanceId!, style: AppTextStyles.nano.copyWith(color: AppColors.textDim)),
                  if (row.firstTs != null) ...[
                    const SizedBox(width: 8),
                    Text(fmtDateTime(row.firstTs), style: AppTextStyles.nano.copyWith(color: AppColors.textFaint)),
                  ],
                ]),
              ])),
              const SizedBox(width: 8),
              Column(crossAxisAlignment: CrossAxisAlignment.end, children: [
                Text(fmtUsdCost(row.costUsd),
                    style: AppTextStyles.meta.copyWith(color: AppColors.textHi, fontWeight: FontWeight.w600)),
                const SizedBox(height: 2),
                Text('${fmtTokens(row.tokens)} · ${row.calls ?? 0} calls',
                    style: AppTextStyles.nano.copyWith(color: AppColors.textDim)),
                const SizedBox(height: 2),
                Text(
                  '${row.okCalls ?? 0}/${row.calls ?? 0} ok',
                  style: AppTextStyles.nano.copyWith(
                      color: (row.failedCalls ?? 0) > 0 ? AppColors.warning : AppColors.success),
                ),
              ]),
            ]),
          ),
        );
      }).toList(),
    );
  }

  Widget _kindBadge(String kind) {
    final isLive = kind == 'live';
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 5, vertical: 1),
      decoration: BoxDecoration(
        color: isLive ? AppColors.fill(AppColors.success) : AppColors.fill(AppColors.primary),
        borderRadius: BorderRadius.circular(3),
      ),
      child: Text(kind.toUpperCase(),
          style: AppTextStyles.nano.copyWith(
              color: isLive ? AppColors.success : AppColors.primary,
              fontWeight: FontWeight.w700, letterSpacing: 0.5)),
    );
  }
}

// ── Recent call cards ─────────────────────────────────────────────────────────

class _RecentCallCards extends StatelessWidget {
  const _RecentCallCards({required this.calls, required this.onTap});
  final List<RecentCall> calls;
  final ValueChanged<RecentCall> onTap;

  @override
  Widget build(BuildContext context) {
    if (calls.isEmpty) {
      return GlassCard(
        padding: const EdgeInsets.all(16),
        child: Center(child: Text('No calls recorded yet.',
            style: AppTextStyles.meta.copyWith(color: AppColors.textDim))),
      );
    }
    return Column(
      children: calls.map((call) {
        return Padding(
          padding: const EdgeInsets.only(bottom: 6),
          child: GlassCard(
            padding: const EdgeInsets.symmetric(horizontal: 14, vertical: 10),
            onTap: () => onTap(call),
            child: Row(children: [
              Expanded(
                child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
                  Row(children: [
                    if (call.provider != null) ...[
                      Text(call.provider!, style: AppTextStyles.meta.copyWith(color: AppColors.textMd)),
                      const SizedBox(width: 6),
                    ],
                    Flexible(child: Text(call.model ?? '—',
                        style: AppTextStyles.mono(11, color: AppColors.textMuted),
                        overflow: TextOverflow.ellipsis)),
                  ]),
                  const SizedBox(height: 2),
                  Text(
                    '${call.strategy ?? '—'} / ${call.callSite ?? '—'}',
                    style: AppTextStyles.nano.copyWith(color: AppColors.textDim),
                    overflow: TextOverflow.ellipsis,
                  ),
                ]),
              ),
              const SizedBox(width: 10),
              Column(crossAxisAlignment: CrossAxisAlignment.end, children: [
                Text(fmtUsdCost(call.totalCostUsd),
                    style: AppTextStyles.meta.copyWith(color: AppColors.textHi, fontWeight: FontWeight.w500)),
                const SizedBox(height: 1),
                Text(
                  '↑${fmtTokens(call.inputTokens)} ↓${fmtTokens(call.outputTokens)}',
                  style: AppTextStyles.nano.copyWith(color: AppColors.textDim),
                ),
                if (call.ts != null) ...[
                  const SizedBox(height: 1),
                  Text(fmtRelative(call.ts), style: AppTextStyles.nano.copyWith(color: AppColors.textFaint)),
                ],
              ]),
            ]),
          ),
        );
      }).toList(),
    );
  }
}

// ── Call detail overlay ───────────────────────────────────────────────────────

class _CallDetailOverlay extends StatelessWidget {
  const _CallDetailOverlay({required this.call, required this.onClose});
  final RecentCall call;
  final VoidCallback onClose;

  @override
  Widget build(BuildContext context) {
    final json = const JsonEncoder.withIndent('  ').convert(call.raw);
    return GestureDetector(
      onTap: onClose,
      child: Container(
        color: Colors.black.withValues(alpha: 0.7),
        child: Center(
          child: GestureDetector(
            onTap: () {}, // prevent dismiss on inner tap
            child: Container(
              margin: const EdgeInsets.all(20),
              constraints: BoxConstraints(
                  maxWidth: 600,
                  maxHeight: MediaQuery.sizeOf(context).height * 0.8),
              decoration: BoxDecoration(
                color: AppColors.panel,
                borderRadius: BorderRadius.circular(16),
                border: Border.all(color: AppColors.border),
              ),
              child: Column(children: [
                Padding(
                  padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 12),
                  child: Row(children: [
                    Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
                      Text('RECENT CALL', style: AppTextStyles.eyebrow),
                      Text(call.model ?? call.provider ?? 'Call detail', style: AppTextStyles.h3),
                    ]),
                    const Spacer(),
                    IconButton(
                      icon: const Icon(Icons.close, size: 18),
                      color: AppColors.textMuted,
                      onPressed: onClose,
                    ),
                  ]),
                ),
                const Divider(height: 1),
                Expanded(
                  child: SingleChildScrollView(
                    padding: const EdgeInsets.all(16),
                    child: Container(
                      padding: const EdgeInsets.all(12),
                      decoration: BoxDecoration(
                        color: AppColors.panelAlt,
                        borderRadius: BorderRadius.circular(8),
                        border: Border.all(color: AppColors.border),
                      ),
                      child: SelectableText(
                        json,
                        style: AppTextStyles.mono(11, color: AppColors.textMd),
                      ),
                    ),
                  ),
                ),
              ]),
            ),
          ),
        ),
      ),
    );
  }
}
