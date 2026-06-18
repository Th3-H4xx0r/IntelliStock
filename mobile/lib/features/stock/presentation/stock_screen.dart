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

/// Passed as go_router `extra` from a holding so the screen has the position,
/// its brokerage, and the portfolio total (for the diversity gauge) up front.
class StockScreenArgs {
  const StockScreenArgs({this.position, this.brokerageId, this.portfolioTotal});
  final AccountPosition? position;
  final String? brokerageId;
  final double? portfolioTotal;
}

/// Full-screen stock view, modeled on modern brokerage apps (Robinhood-style):
/// a violet gradient crown, name + odometer price header, a live (10 s) gapless
/// chart with range pills, a clean stats list, the user's position with a
/// diversity gauge, an About section, and recent orders.
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
    final topInset = MediaQuery.viewPaddingOf(context).top;

    return Scaffold(
      backgroundColor: AppColors.canvas,
      body: Stack(
        children: [
          // Violet gradient crown behind the header + chart (depth, like the
          // dashboard) that falls off to canvas black.
          IgnorePointer(
            child: Container(
              height: 360,
              decoration: BoxDecoration(
                gradient: LinearGradient(
                  begin: Alignment.topCenter,
                  end: Alignment.bottomCenter,
                  colors: [
                    AppColors.primary.withValues(alpha: 0.20),
                    AppColors.primary.withValues(alpha: 0.04),
                    AppColors.canvas.withValues(alpha: 0.0),
                  ],
                  stops: const [0.0, 0.45, 1.0],
                ),
              ),
            ),
          ),
          ListView(
            padding: EdgeInsets.fromLTRB(20, topInset + 4, 20, 40),
            children: [
              _topBar(),
              const SizedBox(height: 14),
              _header(series, info),
              const SizedBox(height: 18),
              _chartArea(series, error: histAsync.hasError && series == null),
              const SizedBox(height: 10),
              _rangeTabs(),
              const SizedBox(height: 26),
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
        ],
      ),
    );
  }

  Widget _topBar() {
    return Row(
      children: [
        GestureDetector(
          behavior: HitTestBehavior.opaque,
          onTap: () => Navigator.of(context).maybePop(),
          child: Container(
            width: 38,
            height: 38,
            decoration: BoxDecoration(
              color: Colors.white.withValues(alpha: 0.06),
              shape: BoxShape.circle,
              border: Border.all(color: Colors.white.withValues(alpha: 0.08)),
            ),
            child: const Icon(Icons.arrow_back_ios_new,
                size: 15, color: AppColors.textHi),
          ),
        ),
      ],
    );
  }

  // ── Header: name + odometer price on one row, ticker + change below ──
  Widget _header(StockSeries? series, Map<String, dynamic> info) {
    final name = ((info['name'] as String?) ?? '').trim();
    final vals = series?.vals;
    final ready = vals != null && vals.length >= 2;
    final first = ready ? vals.first : 0.0;
    final last = ready ? vals.last : 0.0;

    return ValueListenableBuilder<int?>(
      valueListenable: _scrub,
      builder: (_, idx, _) {
        final shown = (ready && idx != null && idx >= 0 && idx < vals.length)
            ? vals[idx]
            : last;
        final dAbs = shown - first;
        final dPct = first != 0 ? dAbs / first * 100 : 0.0;
        final c = dAbs >= 0 ? AppColors.success : AppColors.danger;
        return Row(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            // Name + ticker
            Expanded(
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Text(name.isNotEmpty ? name : widget.symbol,
                      maxLines: 2,
                      overflow: TextOverflow.ellipsis,
                      style: AppTextStyles.h2
                          .copyWith(fontWeight: FontWeight.w800, height: 1.1)),
                  const SizedBox(height: 3),
                  Text(widget.symbol,
                      style: AppTextStyles.micro
                          .copyWith(color: AppColors.textMuted)),
                ],
              ),
            ),
            const SizedBox(width: 12),
            // Price + change
            Column(
              crossAxisAlignment: CrossAxisAlignment.end,
              children: [
                ready
                    ? TweenAnimationBuilder<double>(
                        tween: Tween<double>(begin: 0, end: shown),
                        duration: const Duration(milliseconds: 450),
                        curve: Curves.easeOutCubic,
                        builder: (_, v, _) => Text(fmtMoney(v),
                            style: AppTextStyles.valueXl
                                .copyWith(fontWeight: FontWeight.w800)),
                      )
                    : Skeleton(width: 120, height: 28, radius: 8),
                const SizedBox(height: 4),
                if (ready)
                  Row(
                    mainAxisSize: MainAxisSize.min,
                    children: [
                      Icon(
                          dAbs >= 0
                              ? Icons.arrow_drop_up
                              : Icons.arrow_drop_down,
                          color: c,
                          size: 18),
                      Text('${fmtPnl(dAbs)}  ${fmtPct(dPct)}',
                          style: AppTextStyles.meta.copyWith(
                              color: c, fontWeight: FontWeight.w700)),
                    ],
                  )
                else
                  Skeleton(width: 90, height: 13, radius: 5),
              ],
            ),
          ],
        );
      },
    );
  }

  // ── Live, gapless chart (skeleton while a range loads) ──
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
    return Row(
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
            child: Container(
              margin: const EdgeInsets.symmetric(horizontal: 2),
              padding: const EdgeInsets.symmetric(vertical: 7),
              decoration: BoxDecoration(
                color: isActive
                    ? Colors.white.withValues(alpha: 0.10)
                    : Colors.transparent,
                borderRadius: BorderRadius.circular(8),
              ),
              child: Text(r,
                  textAlign: TextAlign.center,
                  style: AppTextStyles.micro.copyWith(
                    color: isActive ? AppColors.textHi : AppColors.textDim,
                    fontWeight: isActive ? FontWeight.w700 : FontWeight.w500,
                  )),
            ),
          ),
        );
      }).toList(),
    );
  }

  // ── Clean stats LIST (label left / value right), like the reference ──
  Widget _statsCard(StockSeries? series, Map<String, dynamic> info) {
    final rows = <(String, String)>[];
    num? n(dynamic v) => v is num ? v : null;

    final pc = n(info['previousClose']);
    if (pc != null && pc != 0) rows.add(('Previous close', fmtMoney(pc)));
    if (series != null && series.vals.length >= 2) {
      final v = series.vals;
      rows.add(('Open', fmtMoney(v.first)));
      final lo = v.reduce((a, b) => a < b ? a : b);
      final hi = v.reduce((a, b) => a > b ? a : b);
      rows.add(('$_range range', '${fmtMoney(lo)} – ${fmtMoney(hi)}'));
    }
    final lo52 = n(info['fiftyTwoWeekLow']);
    final hi52 = n(info['fiftyTwoWeekHigh']);
    if (lo52 != null && hi52 != null && hi52 != 0) {
      rows.add(('52-week range', '${fmtMoney(lo52)} – ${fmtMoney(hi52)}'));
    }
    final vol = n(info['volume']);
    if (vol != null && vol != 0) rows.add(('Volume', _compact(vol)));
    final mcap = n(info['marketCap']);
    if (mcap != null && mcap != 0) rows.add(('Market cap', '\$${_compact(mcap)}'));
    final pe = n(info['trailingPE']);
    if (pe != null && pe != 0) rows.add(('P/E ratio', pe.toStringAsFixed(2)));
    final beta = n(info['beta']);
    if (beta != null && beta != 0) rows.add(('Beta', beta.toStringAsFixed(2)));

    if (rows.isEmpty) return const SizedBox.shrink();
    return GlassCard(
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          _sectionTitle('Key statistics'),
          const SizedBox(height: 6),
          for (var i = 0; i < rows.length; i++) ...[
            if (i > 0)
              Divider(height: 1, color: Colors.white.withValues(alpha: 0.05)),
            _statRow(rows[i].$1, rows[i].$2),
          ],
        ],
      ),
    );
  }

  Widget _statRow(String label, String value) => Padding(
        padding: const EdgeInsets.symmetric(vertical: 13),
        child: Row(
          children: [
            Text(label,
                style: AppTextStyles.body.copyWith(color: AppColors.textMuted)),
            const Spacer(),
            Text(value,
                style: AppTextStyles.bodyHi
                    .copyWith(fontWeight: FontWeight.w700)),
          ],
        ),
      );

  Widget _sectionTitle(String s) => Text(
        s.toUpperCase(),
        style: AppTextStyles.meta.copyWith(
          color: AppColors.textMuted,
          fontWeight: FontWeight.w700,
          letterSpacing: 1.0,
        ),
      );

  // ── Position card with diversity gauge + total P&L ──
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
          _sectionTitle('Your position'),
          const SizedBox(height: 16),
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
                    const SizedBox(height: 4),
                    Text(fmtPnl(p.unrealizedPnl),
                        style: AppTextStyles.valueLg.copyWith(color: color)),
                    const SizedBox(height: 2),
                    Text(fmtPct(p.unrealizedPnlPct),
                        style: AppTextStyles.micro.copyWith(
                            color: color, fontWeight: FontWeight.w700)),
                  ],
                ),
              ),
            ],
          ),
          const SizedBox(height: 4),
          Divider(height: 26, color: Colors.white.withValues(alpha: 0.06)),
          _statRow('Shares', qtyStr),
          Divider(height: 1, color: Colors.white.withValues(alpha: 0.05)),
          _statRow('Market value', fmtMoney(p.marketValue)),
          Divider(height: 1, color: Colors.white.withValues(alpha: 0.05)),
          _statRow('Average entry', fmtMoney(p.avgEntryPrice)),
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
          _sectionTitle('About'),
          if (tags.isNotEmpty) ...[
            const SizedBox(height: 12),
            Wrap(spacing: 8, runSpacing: 8, children: [
              for (final t in tags) _tag(t),
            ]),
          ],
          const SizedBox(height: 14),
          Text(summary,
              style: AppTextStyles.body
                  .copyWith(height: 1.5, color: AppColors.textMd),
              maxLines: 10,
              overflow: TextOverflow.ellipsis),
        ],
      ),
    );
  }

  Widget _tag(String label) => Container(
        padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 5),
        decoration: BoxDecoration(
          color: AppColors.fill(AppColors.primary),
          borderRadius: BorderRadius.circular(7),
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
          _sectionTitle('Order history'),
          const SizedBox(height: 6),
          if (bid == null)
            _ordersEmpty('No linked brokerage')
          else
            Consumer(builder: (context, ref, _) {
              final ordersAsync = ref.watch(
                stockOrdersProvider((brokerageId: bid, symbol: widget.symbol)),
              );
              return ordersAsync.when(
                loading: () => const Padding(
                  padding: EdgeInsets.symmetric(vertical: 16),
                  child: Center(
                      child: SizedBox(
                          width: 18,
                          height: 18,
                          child: CircularProgressIndicator(strokeWidth: 2))),
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

/// Circular allocation gauge: arc = this position's share of the portfolio,
/// with the percentage in the centre — the diversity indicator.
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
      width: 60,
      height: 60,
      child: Stack(
        alignment: Alignment.center,
        children: [
          CustomPaint(
            size: const Size(60, 60),
            painter:
                _GaugePainter(fraction: fraction.clamp(0.0, 1.0), color: color),
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
