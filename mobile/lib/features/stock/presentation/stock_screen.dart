import 'dart:math' as math;

import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import '../../../core/charts/scrubbable_area_chart.dart';
import '../../../core/formatters/formatters.dart';
import '../../../core/theme/app_colors.dart';
import '../../../core/theme/app_text_styles.dart';
import '../../../core/widgets/glass_card.dart';
import '../../../core/widgets/skeleton.dart';
import '../../dashboard/data/dashboard_repository.dart';
import '../../live_trading/data/models/live_state.dart';
import '../application/stock_controller.dart';

const _ranges = ['1D', '1W', '1M', '3M', 'YTD', '1Y', 'ALL'];

/// Passed as go_router `extra` when navigating from a holding so the screen has
/// the position, its brokerage, and the portfolio total (for the diversity
/// gauge) immediately, without a re-fetch.
class StockScreenArgs {
  const StockScreenArgs({this.position, this.brokerageId, this.portfolioTotal});
  final AccountPosition? position;
  final String? brokerageId;
  final double? portfolioTotal;
}

/// Full-screen view for one stock — modern-brokerage style: a price hero with an
/// odometer roll + live (10 s) chart, range pills, key stats, the user's
/// position with a diversity gauge, an About section, and recent orders.
class StockScreen extends ConsumerStatefulWidget {
  const StockScreen({
    super.key,
    required this.symbol,
    this.position,
    this.brokerageId,
    this.portfolioTotal,
  });

  final String symbol;
  final AccountPosition? position;
  final String? brokerageId;
  final double? portfolioTotal;

  @override
  ConsumerState<StockScreen> createState() => _StockScreenState();
}

class _StockScreenState extends ConsumerState<StockScreen> {
  String _range = '1D';
  final ValueNotifier<int?> _scrub = ValueNotifier<int?>(null);

  @override
  void dispose() {
    _scrub.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    final histAsync =
        ref.watch(stockHistoryProvider((symbol: widget.symbol, range: _range)));
    final info = ref.watch(stockInfoProvider(widget.symbol)).valueOrNull ??
        const <String, dynamic>{};
    final series = histAsync.valueOrNull;

    return Scaffold(
      backgroundColor: AppColors.canvas,
      appBar: AppBar(
        backgroundColor: AppColors.canvas,
        elevation: 0,
        titleSpacing: 0,
        title: Text(widget.symbol,
            style: AppTextStyles.h2.copyWith(fontWeight: FontWeight.w800)),
      ),
      body: ListView(
        padding: const EdgeInsets.fromLTRB(16, 4, 16, 36),
        children: [
          _hero(series, info, loading: series == null && histAsync.isLoading),
          const SizedBox(height: 14),
          _chartArea(series, error: histAsync.hasError && series == null),
          const SizedBox(height: 10),
          _rangeTabs(),
          const SizedBox(height: 20),
          _statsCard(series, info),
          if (widget.position != null) ...[
            const SizedBox(height: 16),
            _positionCard(widget.position!),
          ],
          if (((info['summary'] as String?) ?? '').trim().isNotEmpty) ...[
            const SizedBox(height: 16),
            _aboutCard(info),
          ],
          const SizedBox(height: 16),
          _ordersCard(),
        ],
      ),
    );
  }

  // ── Hero: company name, odometer price, range change ──
  Widget _hero(StockSeries? series, Map<String, dynamic> info,
      {required bool loading}) {
    final name = ((info['name'] as String?) ?? '').trim();
    if (series == null || series.vals.length < 2) {
      return Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          if (name.isNotEmpty)
            Text(name.toUpperCase(),
                style: AppTextStyles.eyebrow,
                maxLines: 1,
                overflow: TextOverflow.ellipsis),
          const SizedBox(height: 8),
          Skeleton(width: 180, height: 38, radius: 10),
          const SizedBox(height: 8),
          Skeleton(width: 130, height: 16, radius: 6),
        ],
      );
    }
    final vals = series.vals;
    final first = vals.first;
    final last = vals.last;
    return ValueListenableBuilder<int?>(
      valueListenable: _scrub,
      builder: (_, idx, _) {
        final shown = (idx != null && idx >= 0 && idx < vals.length)
            ? vals[idx]
            : last;
        final dAbs = shown - first;
        final dPct = first != 0 ? dAbs / first * 100 : 0.0;
        final c = dAbs >= 0 ? AppColors.success : AppColors.danger;
        return Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            if (name.isNotEmpty)
              Text(name.toUpperCase(),
                  style: AppTextStyles.eyebrow,
                  maxLines: 1,
                  overflow: TextOverflow.ellipsis),
            const SizedBox(height: 6),
            // Odometer roll — 0→price on load, then rolls on each live update.
            TweenAnimationBuilder<double>(
              tween: Tween<double>(begin: 0, end: shown),
              duration: const Duration(milliseconds: 450),
              curve: Curves.easeOutCubic,
              builder: (_, v, _) =>
                  Text(fmtMoney(v), style: AppTextStyles.valueHero),
            ),
            const SizedBox(height: 6),
            Row(
              mainAxisSize: MainAxisSize.min,
              children: [
                Icon(dAbs >= 0 ? Icons.arrow_drop_up : Icons.arrow_drop_down,
                    color: c, size: 22),
                Text('${fmtPnl(dAbs)}  ${fmtPct(dPct)}',
                    style: AppTextStyles.bodyHi
                        .copyWith(color: c, fontWeight: FontWeight.w700)),
                const SizedBox(width: 6),
                Text(_range,
                    style: AppTextStyles.micro
                        .copyWith(color: AppColors.textDim)),
              ],
            ),
          ],
        );
      },
    );
  }

  // ── Chart (live; skeleton while a range loads) ──
  Widget _chartArea(StockSeries? series, {required bool error}) {
    if (error) {
      return SizedBox(
        height: 248,
        child: Center(
          child: Text("Couldn't load prices",
              style: AppTextStyles.micro.copyWith(color: AppColors.danger)),
        ),
      );
    }
    if (series == null || series.vals.length < 2) {
      return const Padding(
        padding: EdgeInsets.symmetric(vertical: 6),
        child: Skeleton(height: 248, radius: 16),
      );
    }
    final up = series.vals.last >= series.vals.first;
    return ScrubbableAreaChart(
      timestamps: series.ts,
      values: series.vals,
      lineColor: up ? AppColors.success : AppColors.danger,
      height: 248,
      indexed: true, // evenly-spaced points → no weekend/overnight gaps
      onScrub: (i) => _scrub.value = i,
    );
  }

  Widget _rangeTabs() {
    return Container(
      padding: const EdgeInsets.all(3),
      decoration: BoxDecoration(
        color: const Color(0x0FFFFFFF),
        borderRadius: BorderRadius.circular(10),
        border: Border.all(color: const Color(0x0DFFFFFF)),
      ),
      child: Row(
        children: _ranges.map((r) {
          final isActive = r == _range;
          return Expanded(
            child: GestureDetector(
              behavior: HitTestBehavior.opaque,
              onTap: () {
                if (r != _range) {
                  _scrub.value = null;
                  setState(() => _range = r);
                }
              },
              child: AnimatedContainer(
                duration: const Duration(milliseconds: 160),
                curve: Curves.easeOut,
                padding: const EdgeInsets.symmetric(vertical: 7),
                decoration: BoxDecoration(
                  color:
                      isActive ? const Color(0x24FFFFFF) : Colors.transparent,
                  borderRadius: BorderRadius.circular(7),
                ),
                child: Text(r,
                    textAlign: TextAlign.center,
                    style: AppTextStyles.micro.copyWith(
                      color: isActive ? AppColors.textHi : AppColors.textDim,
                      fontWeight:
                          isActive ? FontWeight.w700 : FontWeight.w500,
                    )),
              ),
            ),
          );
        }).toList(),
      ),
    );
  }

  // ── Key stats grid ──
  Widget _statsCard(StockSeries? series, Map<String, dynamic> info) {
    final stats = <(String, String)>[];
    if (series != null && series.vals.length >= 2) {
      final v = series.vals;
      stats.add(('Open', fmtMoney(v.first)));
      stats.add(('High', fmtMoney(v.reduce((a, b) => a > b ? a : b))));
      stats.add(('Low', fmtMoney(v.reduce((a, b) => a < b ? a : b))));
    }
    void addNum(String label, dynamic raw, String Function(num) f) {
      final n = (raw is num) ? raw : null;
      if (n != null && n != 0) stats.add((label, f(n)));
    }

    addNum('Prev close', info['previousClose'], fmtMoney);
    addNum('52W high', info['fiftyTwoWeekHigh'], fmtMoney);
    addNum('52W low', info['fiftyTwoWeekLow'], fmtMoney);
    addNum('Market cap', info['marketCap'], (n) => '\$${_compact(n)}');
    addNum('P/E', info['trailingPE'], (n) => n.toStringAsFixed(2));
    addNum('Beta', info['beta'], (n) => n.toStringAsFixed(2));
    addNum('Volume', info['volume'], (n) => _compact(n));

    if (stats.isEmpty) return const SizedBox.shrink();
    return GlassCard(
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Text('Key stats', style: AppTextStyles.h3),
          const SizedBox(height: 12),
          for (var i = 0; i < stats.length; i += 2)
            Padding(
              padding: const EdgeInsets.only(bottom: 12),
              child: Row(
                children: [
                  Expanded(child: _statCell(stats[i])),
                  Expanded(
                      child: i + 1 < stats.length
                          ? _statCell(stats[i + 1])
                          : const SizedBox.shrink()),
                ],
              ),
            ),
        ],
      ),
    );
  }

  Widget _statCell((String, String) s) {
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Text(s.$1.toUpperCase(),
            style: AppTextStyles.nano.copyWith(color: AppColors.textFaint)),
        const SizedBox(height: 2),
        Text(s.$2, style: AppTextStyles.value),
      ],
    );
  }

  // ── Position card with diversity gauge + redesigned P&L ──
  Widget _positionCard(AccountPosition p) {
    final up = p.unrealizedPnl >= 0;
    final color = up ? AppColors.success : AppColors.danger;
    final qty = p.qty;
    final qtyStr = qty == qty.roundToDouble()
        ? qty.toInt().toString()
        : qty.toStringAsFixed(2);
    final frac = (widget.portfolioTotal != null && widget.portfolioTotal! > 0)
        ? p.marketValue / widget.portfolioTotal!
        : null;

    return GlassCard(
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Text('Your position', style: AppTextStyles.h3),
          const SizedBox(height: 14),
          Row(
            children: [
              if (frac != null) ...[
                _DiversityGauge(fraction: frac, color: color),
                const SizedBox(width: 16),
              ],
              Expanded(
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Text('TOTAL P&L',
                        style: AppTextStyles.nano
                            .copyWith(color: AppColors.textFaint)),
                    const SizedBox(height: 3),
                    Text(fmtPnl(p.unrealizedPnl),
                        style: AppTextStyles.valueLg.copyWith(color: color)),
                    const SizedBox(height: 2),
                    Text(fmtPct(p.unrealizedPnlPct),
                        style: AppTextStyles.micro
                            .copyWith(color: color, fontWeight: FontWeight.w700)),
                  ],
                ),
              ),
            ],
          ),
          const Padding(
            padding: EdgeInsets.symmetric(vertical: 14),
            child: Divider(height: 1, color: Color(0x14FFFFFF)),
          ),
          Row(children: [
            Expanded(child: _statCell(('Shares', qtyStr))),
            Expanded(child: _statCell(('Market value', fmtMoney(p.marketValue)))),
            Expanded(child: _statCell(('Avg entry', fmtMoney(p.avgEntryPrice)))),
          ]),
        ],
      ),
    );
  }

  // ── About ──
  Widget _aboutCard(Map<String, dynamic> info) {
    final sector = ((info['sector'] as String?) ?? '').trim();
    final industry = ((info['industry'] as String?) ?? '').trim();
    final summary = ((info['summary'] as String?) ?? '').trim();
    final tags = [sector, industry].where((s) => s.isNotEmpty).toList();
    return GlassCard(
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Text('About', style: AppTextStyles.h3),
          if (tags.isNotEmpty) ...[
            const SizedBox(height: 10),
            Wrap(
              spacing: 8,
              runSpacing: 8,
              children: [for (final t in tags) _tag(t)],
            ),
          ],
          const SizedBox(height: 12),
          Text(summary,
              style: AppTextStyles.body.copyWith(height: 1.45),
              maxLines: 10,
              overflow: TextOverflow.ellipsis),
        ],
      ),
    );
  }

  Widget _tag(String label) => Container(
        padding: const EdgeInsets.symmetric(horizontal: 9, vertical: 4),
        decoration: BoxDecoration(
          color: AppColors.fill(AppColors.primary),
          borderRadius: BorderRadius.circular(6),
        ),
        child: Text(label,
            style: AppTextStyles.micro.copyWith(
                color: AppColors.primary, fontWeight: FontWeight.w600)),
      );

  // ── Orders ──
  Widget _ordersCard() {
    final bid = widget.brokerageId;
    return GlassCard(
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Text('Order history', style: AppTextStyles.h3),
          const SizedBox(height: 4),
          if (bid == null)
            _ordersEmpty('No linked brokerage')
          else
            Consumer(builder: (context, ref, _) {
              final ordersAsync = ref.watch(
                stockOrdersProvider(
                    (brokerageId: bid, symbol: widget.symbol)),
              );
              return ordersAsync.when(
                loading: () => const Padding(
                  padding: EdgeInsets.symmetric(vertical: 16),
                  child: Center(
                      child: SizedBox(
                          width: 18,
                          height: 18,
                          child:
                              CircularProgressIndicator(strokeWidth: 2))),
                ),
                error: (_, _) => _ordersEmpty("Couldn't load orders"),
                data: (orders) => orders.isEmpty
                    ? _ordersEmpty('No recent orders for ${widget.symbol}')
                    : Column(
                        children: [for (final t in orders) _OrderRow(trade: t)],
                      ),
              );
            }),
        ],
      ),
    );
  }

  Widget _ordersEmpty(String msg) => Padding(
        padding: const EdgeInsets.symmetric(vertical: 12),
        child: Text(msg,
            style: AppTextStyles.micro.copyWith(color: AppColors.textDim)),
      );
}

String _compact(num v) {
  final a = v.abs();
  if (a >= 1e12) return '${(v / 1e12).toStringAsFixed(2)}T';
  if (a >= 1e9) return '${(v / 1e9).toStringAsFixed(2)}B';
  if (a >= 1e6) return '${(v / 1e6).toStringAsFixed(2)}M';
  if (a >= 1e3) return '${(v / 1e3).toStringAsFixed(1)}K';
  return v.toStringAsFixed(0);
}

/// A circular allocation gauge: an arc = this position's share of the
/// portfolio, with the percentage in the centre — the "diversity" indicator.
class _DiversityGauge extends StatelessWidget {
  const _DiversityGauge({required this.fraction, required this.color});
  final double fraction;
  final Color color;

  String get _label {
    final pct = fraction * 100;
    if (pct <= 0) return '0%';
    if (pct < 1) return '<1%';
    return '${pct.round()}%';
  }

  @override
  Widget build(BuildContext context) {
    return SizedBox(
      width: 58,
      height: 58,
      child: Stack(
        alignment: Alignment.center,
        children: [
          CustomPaint(
            size: const Size(58, 58),
            painter: _GaugePainter(
                fraction: fraction.clamp(0.0, 1.0), color: color),
          ),
          Text(_label,
              style: AppTextStyles.micro.copyWith(
                  color: AppColors.textHi, fontWeight: FontWeight.w800)),
        ],
      ),
    );
  }
}

class _GaugePainter extends CustomPainter {
  _GaugePainter({required this.fraction, required this.color});
  final double fraction;
  final Color color;

  @override
  void paint(Canvas canvas, Size size) {
    final center = size.center(Offset.zero);
    final radius = size.width / 2 - 4;
    final track = Paint()
      ..color = Colors.white.withValues(alpha: 0.10)
      ..style = PaintingStyle.stroke
      ..strokeWidth = 5;
    final prog = Paint()
      ..color = color
      ..style = PaintingStyle.stroke
      ..strokeWidth = 5
      ..strokeCap = StrokeCap.round;
    canvas.drawCircle(center, radius, track);
    canvas.drawArc(
      Rect.fromCircle(center: center, radius: radius),
      -math.pi / 2,
      fraction * 2 * math.pi,
      false,
      prog,
    );
  }

  @override
  bool shouldRepaint(covariant _GaugePainter old) =>
      old.fraction != fraction || old.color != color;
}

class _OrderRow extends StatelessWidget {
  const _OrderRow({required this.trade});
  final Trade trade;

  @override
  Widget build(BuildContext context) {
    final isBuy = trade.side.toLowerCase() == 'buy';
    final color = isBuy ? AppColors.success : AppColors.danger;
    final qty = trade.qty;
    final qtyStr = qty == qty.roundToDouble()
        ? qty.toInt().toString()
        : qty.toStringAsFixed(2);
    return Padding(
      padding: const EdgeInsets.symmetric(vertical: 8),
      child: Row(
        children: [
          Container(
            padding: const EdgeInsets.symmetric(horizontal: 7, vertical: 3),
            decoration: BoxDecoration(
              color: AppColors.fill(color),
              borderRadius: BorderRadius.circular(5),
            ),
            child: Text(trade.side.toUpperCase(),
                style: AppTextStyles.nano
                    .copyWith(color: color, fontWeight: FontWeight.w800)),
          ),
          const SizedBox(width: 10),
          Expanded(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Text('$qtyStr @ ${fmtMoney(trade.price)}',
                    style: AppTextStyles.bodyHi
                        .copyWith(fontWeight: FontWeight.w600)),
                const SizedBox(height: 2),
                Text(fmtDateTime(trade.ts),
                    style:
                        AppTextStyles.nano.copyWith(color: AppColors.textDim)),
              ],
            ),
          ),
          Text(fmtMoney(trade.price * trade.qty), style: AppTextStyles.value),
        ],
      ),
    );
  }
}
