import 'package:flutter/material.dart';
import 'package:syncfusion_flutter_charts/charts.dart' hide Position;
import '../../../core/formatters/formatters.dart';
import '../../../core/theme/app_colors.dart';
import '../../../core/theme/app_text_styles.dart';
import '../../../core/widgets/app_button.dart';
import '../../../core/widgets/glass_card.dart';
import '../../../core/widgets/material_symbols.dart';
import '../data/live_repository.dart';
import '../data/models/live_state.dart';
import 'equity_chart.dart';

class PositionCard extends StatelessWidget {
  const PositionCard({
    super.key,
    required this.position,
    required this.chartStyle,
    required this.range,
    required this.historicals,
    required this.onClose,
  });

  final Position position;
  final ChartStyle chartStyle;
  final String range;
  final List<HistPoint> historicals;
  final VoidCallback onClose;

  bool get _isUp {
    if (historicals.length < 2) return position.unrealizedPnl >= 0;
    final startV = _toDouble(historicals.first.ts, historicals.first.value);
    final endV = historicals.last.value;
    return endV >= startV;
  }

  double _toDouble(dynamic _, double v) => v;

  List<_SparkPoint> get _sparkPoints {
    final pts = <_SparkPoint>[];
    for (int i = 0; i < historicals.length; i++) {
      pts.add(_SparkPoint(i, historicals[i].value));
    }
    return pts;
  }

  List<_SparkCandle> _bucketCandles(List<_SparkPoint> pts) {
    if (pts.length < 2) return [];
    final samplesPer = ((pts.length / 20).ceil()).clamp(1, pts.length);
    final out = <_SparkCandle>[];
    for (int i = 0; i < pts.length; i += samplesPer) {
      final slice = pts.skip(i).take(samplesPer).toList();
      if (slice.isEmpty) continue;
      final open = slice.first.value;
      final close = slice.last.value;
      double high = double.negativeInfinity, low = double.infinity;
      for (final p in slice) {
        if (p.value > high) high = p.value;
        if (p.value < low) low = p.value;
      }
      out.add(_SparkCandle(out.length, open, high, low, close));
    }
    return out;
  }

  @override
  Widget build(BuildContext context) {
    final unrealizedPnlColor = pnlColor(position.unrealizedPnl);
    final sparkColor = _isUp ? AppColors.chartUp : AppColors.chartDown;

    return GlassCard(
      padding: const EdgeInsets.all(14),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          // Header: symbol + unrealized pnl% | market value
          Row(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Expanded(
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Row(
                      children: [
                        Text(
                          position.symbol,
                          style: AppTextStyles.cardTitle.copyWith(
                            color: AppColors.textHi,
                            fontWeight: FontWeight.w800,
                            letterSpacing: 0.5,
                          ),
                        ),
                        const SizedBox(width: 8),
                        if (position.avgEntryPrice != null)
                          Text(
                            fmtPct(position.unrealizedPnlPct),
                            style: AppTextStyles.micro.copyWith(
                              color: unrealizedPnlColor,
                              fontWeight: FontWeight.w700,
                            ),
                          ),
                      ],
                    ),
                    if (historicals.length >= 2) ...[
                      const SizedBox(height: 2),
                      Row(
                        children: [
                          Icon(
                            _isUp ? symbol('arrow_upward') : symbol('arrow_downward'),
                            size: 10,
                            color: sparkColor.withValues(alpha: 0.9),
                          ),
                          const SizedBox(width: 2),
                          Text(
                            '${_isUp ? '+' : ''}${_rangePct.toStringAsFixed(2)}%  $range',
                            style: AppTextStyles.nano.copyWith(
                              color: sparkColor.withValues(alpha: 0.9),
                              fontWeight: FontWeight.w700,
                            ),
                          ),
                        ],
                      ),
                    ],
                  ],
                ),
              ),
              Column(
                crossAxisAlignment: CrossAxisAlignment.end,
                children: [
                  Text(
                    'MARKET VALUE',
                    style: AppTextStyles.nano.copyWith(
                      color: AppColors.textDim,
                      letterSpacing: 0.5,
                    ),
                  ),
                  Text(
                    fmtMoney(position.marketValue),
                    style: AppTextStyles.value.copyWith(
                      color: AppColors.textHi,
                      fontWeight: FontWeight.w800,
                    ),
                  ),
                ],
              ),
            ],
          ),

          const SizedBox(height: 8),

          // Mini sparkline
          SizedBox(
            height: chartStyle == ChartStyle.candle ? 90 : 64,
            child: historicals.isEmpty
                ? Center(
                    child: Text(
                      'No price history',
                      style: AppTextStyles.nano.copyWith(color: AppColors.textFaint),
                    ),
                  )
                : _buildSparkline(sparkColor),
          ),

          const SizedBox(height: 10),

          // 4-stat grid: Shares / Last / Entry / P&L$
          Row(
            children: [
              _Stat(label: 'SHARES', value: position.qty.toStringAsFixed(4)),
              _Stat(label: 'LAST', value: fmtMoney(position.lastPrice)),
              _Stat(
                label: 'ENTRY',
                value: position.avgEntryPrice != null
                    ? fmtMoney(position.avgEntryPrice)
                    : '—',
              ),
              _Stat(
                label: 'P&L \$',
                value: position.avgEntryPrice != null
                    ? fmtPnl(position.unrealizedPnl)
                    : '—',
                valueColor: position.avgEntryPrice != null
                    ? unrealizedPnlColor
                    : AppColors.textDim,
              ),
            ],
          ),

          const SizedBox(height: 10),

          // Close button
          Align(
            alignment: Alignment.centerRight,
            child: AppButton.semantic(
              label: 'Close',
              icon: symbol('logout'),
              color: AppColors.danger,
              dense: true,
              onPressed: onClose,
            ),
          ),
        ],
      ),
    );
  }

  double get _rangePct {
    if (historicals.length < 2) return 0;
    final start = historicals.first.value;
    final end = historicals.last.value;
    if (start == 0) return 0;
    return ((end - start) / start) * 100;
  }

  Widget _buildSparkline(Color color) {
    final sparkPts = _sparkPoints;
    if (chartStyle == ChartStyle.candle) {
      final candles = _bucketCandles(sparkPts);
      return SfCartesianChart(
        backgroundColor: Colors.transparent,
        plotAreaBorderWidth: 0,
        margin: EdgeInsets.zero,
        primaryXAxis: NumericAxis(
          isVisible: false,
          majorGridLines: const MajorGridLines(width: 0),
          axisLine: const AxisLine(width: 0),
        ),
        primaryYAxis: NumericAxis(
          isVisible: false,
          majorGridLines: const MajorGridLines(width: 0),
          axisLine: const AxisLine(width: 0),
        ),
        series: <CartesianSeries>[
          CandleSeries<_SparkCandle, num>(
            dataSource: candles,
            xValueMapper: (d, _) => d.x,
            lowValueMapper: (d, _) => d.low,
            highValueMapper: (d, _) => d.high,
            openValueMapper: (d, _) => d.open,
            closeValueMapper: (d, _) => d.close,
            bullColor: AppColors.chartUp,
            bearColor: AppColors.chartDown,
            enableSolidCandles: true,
          ),
        ],
      );
    }

    final isLine = chartStyle == ChartStyle.line;
    return SfCartesianChart(
      backgroundColor: Colors.transparent,
      plotAreaBorderWidth: 0,
      margin: EdgeInsets.zero,
      primaryXAxis: NumericAxis(
        isVisible: false,
        majorGridLines: const MajorGridLines(width: 0),
        axisLine: const AxisLine(width: 0),
      ),
      primaryYAxis: NumericAxis(
        isVisible: false,
        majorGridLines: const MajorGridLines(width: 0),
        axisLine: const AxisLine(width: 0),
      ),
      series: <CartesianSeries>[
        if (!isLine)
          SplineAreaSeries<_SparkPoint, num>(
            dataSource: sparkPts,
            splineType: SplineType.monotonic,
            xValueMapper: (d, _) => d.x,
            yValueMapper: (d, _) => d.value,
            color: color.withValues(alpha: 0.20),
            borderColor: color,
            borderWidth: 1.5,
            gradient: LinearGradient(
              begin: Alignment.topCenter,
              end: Alignment.bottomCenter,
              colors: [
                color.withValues(alpha: 0.30),
                color.withValues(alpha: 0.02),
              ],
            ),
          )
        else
          SplineSeries<_SparkPoint, num>(
            dataSource: sparkPts,
            splineType: SplineType.monotonic,
            xValueMapper: (d, _) => d.x,
            yValueMapper: (d, _) => d.value,
            color: color,
            width: 1.25,
          ),
      ],
    );
  }
}

// ── Internal point types ──────────────────────────────────────────────────────

class _SparkPoint {
  const _SparkPoint(this.x, this.value);
  final int x;
  final double value;
}

class _SparkCandle {
  const _SparkCandle(this.x, this.open, this.high, this.low, this.close);
  final int x;
  final double open, high, low, close;
}

// ── Micro stat tile ───────────────────────────────────────────────────────────

class _Stat extends StatelessWidget {
  const _Stat({
    required this.label,
    required this.value,
    this.valueColor = AppColors.textMd,
  });

  final String label;
  final String value;
  final Color valueColor;

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
              letterSpacing: 0.4,
            ),
          ),
          const SizedBox(height: 2),
          Text(
            value,
            style: AppTextStyles.micro.copyWith(
              color: valueColor,
              fontWeight: FontWeight.w700,
            ),
            overflow: TextOverflow.ellipsis,
          ),
        ],
      ),
    );
  }
}
