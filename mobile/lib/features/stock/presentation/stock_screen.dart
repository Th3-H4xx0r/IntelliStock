import 'dart:math' as math;

import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import '../../../core/charts/scrubbable_area_chart.dart';
import '../../../core/formatters/formatters.dart';
import '../../../core/theme/app_colors.dart';
import '../../../core/theme/app_text_styles.dart';
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
  // The range whose entry animation has already played. Lets the chart animate
  // once when a range first appears (or on a range switch) but NOT on every 10 s
  // live poll — which otherwise replayed the grow-in animation each tick.
  String? _chartAnimatedRange;

  @override
  void dispose() {
    _scrub.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    final histAsync =
        ref.watch(stockHistoryProvider((symbol: widget.symbol, range: _range)));
    final infoAsync = ref.watch(stockInfoProvider(widget.symbol));
    final info = infoAsync.valueOrNull ?? const <String, dynamic>{};
    // True only while the very first fetch is in flight (no data yet) — drives
    // the stats/about skeletons.
    final infoLoading = infoAsync.isLoading && infoAsync.valueOrNull == null;
    final series = histAsync.valueOrNull;
    final histLoading = histAsync.isLoading;
    final hasSummary = ((info['summary'] as String?) ?? '').trim().isNotEmpty;
    final topInset = MediaQuery.viewPaddingOf(context).top;

    // SingleChildScrollView + Column (not ListView) so every section stays
    // mounted while scrolling: the chart animates once, and the orders/bot
    // providers fetch once on init instead of re-firing each time a section
    // scrolls back into view.
    return Scaffold(
      backgroundColor: AppColors.canvas,
      body: Stack(
        children: [
          const _StockBackdrop(),
          SingleChildScrollView(
            padding: EdgeInsets.fromLTRB(20, topInset + 4, 20, 40),
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.stretch,
              children: [
                _topBar(),
                const SizedBox(height: 14),
                _header(series, info, loading: histLoading),
                const SizedBox(height: 18),
                _chartArea(series,
                    error: histAsync.hasError && series == null,
                    loading: histLoading),
                const SizedBox(height: 10),
                _rangeTabs(),
                if (widget.position != null) ...[
                  const SizedBox(height: 26),
                  _positionCard(widget.position!),
                ],
                const SizedBox(height: 28),
                _botCard(),
                const SizedBox(height: 28),
                if (infoLoading)
                  _statsSkeleton()
                else
                  _statsCard(series, info),
                if (infoLoading) ...[
                  const SizedBox(height: 30),
                  _aboutSkeleton(),
                ] else if (hasSummary) ...[
                  const SizedBox(height: 30),
                  _aboutCard(info),
                ],
                const SizedBox(height: 30),
                _ordersCard(),
              ],
            ),
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
  Widget _header(StockSeries? series, Map<String, dynamic> info,
      {required bool loading}) {
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
                      style: AppTextStyles.h2.copyWith(
                          fontWeight: FontWeight.w700,
                          height: 1.18,
                          letterSpacing: -0.2)),
                  const SizedBox(height: 4),
                  Text(widget.symbol,
                      style: AppTextStyles.micro.copyWith(
                          color: AppColors.textMd,
                          fontWeight: FontWeight.w600,
                          letterSpacing: 0.5)),
                ],
              ),
            ),
            const SizedBox(width: 12),
            // Price + change
            Column(
              crossAxisAlignment: CrossAxisAlignment.end,
              children: [
                if (ready)
                  TweenAnimationBuilder<double>(
                    tween: Tween<double>(begin: 0, end: shown),
                    duration: const Duration(milliseconds: 450),
                    curve: Curves.easeOutCubic,
                    builder: (_, v, _) => Text(fmtMoney(v),
                        style: AppTextStyles.valueXl
                            .copyWith(fontWeight: FontWeight.w800)),
                  )
                else if (loading)
                  Skeleton(width: 120, height: 28, radius: 8)
                else
                  Text('—',
                      style: AppTextStyles.valueXl
                          .copyWith(fontWeight: FontWeight.w800)),
                const SizedBox(height: 4),
                if (ready)
                  Text(
                    '${dAbs >= 0 ? '▲' : '▼'} ${fmtPnl(dAbs)}  ${fmtPct(dPct)}',
                    style: AppTextStyles.meta.copyWith(
                        fontSize: 15, color: c, fontWeight: FontWeight.w700),
                  )
                else if (loading)
                  Skeleton(width: 100, height: 16, radius: 5)
                else
                  Text('No price data',
                      style: AppTextStyles.micro
                          .copyWith(color: AppColors.textDim)),
              ],
            ),
          ],
        );
      },
    );
  }

  // ── Live, gapless chart (skeleton while loading; graceful no-data state) ──
  Widget _chartArea(StockSeries? series,
      {required bool error, required bool loading}) {
    if (series != null && series.vals.length >= 2) {
      final up = series.vals.last >= series.vals.first;
      // Animate only the first time this range's data appears; subsequent 10 s
      // poll updates redraw without replaying the grow-in animation.
      final animate = _chartAnimatedRange != _range;
      if (animate) {
        WidgetsBinding.instance.addPostFrameCallback((_) {
          if (mounted) _chartAnimatedRange = _range;
        });
      }
      return ScrubbableAreaChart(
        timestamps: series.ts,
        values: series.vals,
        lineColor: up ? AppColors.success : AppColors.danger,
        height: 280,
        indexed: true, // evenly-spaced points → no weekend/overnight gaps
        animate: animate,
        onScrub: (i) => _scrub.value = i,
      );
    }
    if (loading) {
      return const Padding(
        padding: EdgeInsets.symmetric(vertical: 6),
        child: Skeleton(height: 280, radius: 16),
      );
    }
    // Loaded but no usable series — illiquid/obscure tickers (e.g. warrants)
    // that the data source doesn't cover. Show a clear empty state, not a
    // forever-spinning skeleton.
    return SizedBox(
      height: 280,
      child: Center(
        child: Text(
          error
              ? "Couldn't load prices"
              : 'No chart data available for ${widget.symbol}',
          textAlign: TextAlign.center,
          style: AppTextStyles.micro.copyWith(
              color: error ? AppColors.danger : AppColors.textDim),
        ),
      ),
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
    final cells = <(String, String)>[];
    num? nv(dynamic v) => v is num ? v : null;
    void add(String l, dynamic raw, String Function(num) f) {
      final n = nv(raw);
      if (n != null && n != 0) cells.add((l, f(n)));
    }

    add('Prev close', info['previousClose'], fmtMoney);
    if (series != null && series.vals.length >= 2) {
      final v = series.vals;
      cells.add(('Open', fmtMoney(v.first)));
      cells.add(('$_range high', fmtMoney(v.reduce((a, b) => a > b ? a : b))));
      cells.add(('$_range low', fmtMoney(v.reduce((a, b) => a < b ? a : b))));
    }
    add('52W high', info['fiftyTwoWeekHigh'], fmtMoney);
    add('52W low', info['fiftyTwoWeekLow'], fmtMoney);
    add('Volume', info['volume'], _compact);
    add('Avg volume', info['averageVolume'], _compact);
    add('Market cap', info['marketCap'], (n) => '\$${_compact(n)}');
    add('P/E', info['trailingPE'], (n) => n.toStringAsFixed(2));
    add('Fwd P/E', info['forwardPE'], (n) => n.toStringAsFixed(2));
    add('Beta', info['beta'], (n) => n.toStringAsFixed(2));
    add('Analyst target', info['targetMeanPrice'], fmtMoney);

    if (cells.isEmpty) return const SizedBox.shrink();
    return Padding(
      padding: const EdgeInsets.only(top: 4),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          _sectionTitle('Key statistics'),
          const SizedBox(height: 16),
          for (var i = 0; i < cells.length; i += 3)
            Padding(
              padding: const EdgeInsets.only(bottom: 18),
              child: Row(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Expanded(child: _statCell(cells[i])),
                  const SizedBox(width: 14),
                  Expanded(
                      child: i + 1 < cells.length
                          ? _statCell(cells[i + 1])
                          : const SizedBox.shrink()),
                  const SizedBox(width: 14),
                  Expanded(
                      child: i + 2 < cells.length
                          ? _statCell(cells[i + 2])
                          : const SizedBox.shrink()),
                ],
              ),
            ),
        ],
      ),
    );
  }

  Widget _statCell((String, String) s) => Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Text(s.$1.toUpperCase(),
              maxLines: 1,
              overflow: TextOverflow.ellipsis,
              style: AppTextStyles.nano
                  .copyWith(color: AppColors.textFaint, letterSpacing: 0.2)),
          const SizedBox(height: 4),
          Text(s.$2,
              maxLines: 1,
              overflow: TextOverflow.ellipsis,
              style: AppTextStyles.value
                  .copyWith(fontWeight: FontWeight.w700, fontSize: 16)),
        ],
      );

  Widget _sectionTitle(String s) => Text(
        s.toUpperCase(),
        style: AppTextStyles.meta.copyWith(
          color: AppColors.textMuted,
          fontWeight: FontWeight.w700,
          letterSpacing: 1.0,
        ),
      );

  // ── Skeleton placeholders (shown while a section's data loads) ──
  Widget _statsSkeleton() => Padding(
        padding: const EdgeInsets.only(top: 4),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            _sectionTitle('Key statistics'),
            const SizedBox(height: 18),
            for (var r = 0; r < 3; r++)
              Padding(
                padding: const EdgeInsets.only(bottom: 18),
                child: Row(
                  children: [
                    for (var c = 0; c < 3; c++) ...[
                      if (c > 0) const SizedBox(width: 14),
                      const Expanded(
                        child: Column(
                          crossAxisAlignment: CrossAxisAlignment.start,
                          children: [
                            Skeleton(width: 46, height: 9, radius: 4),
                            SizedBox(height: 7),
                            Skeleton(width: 62, height: 15, radius: 5),
                          ],
                        ),
                      ),
                    ],
                  ],
                ),
              ),
          ],
        ),
      );

  Widget _aboutSkeleton() => Padding(
        padding: const EdgeInsets.only(top: 4),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            _sectionTitle('About'),
            const SizedBox(height: 14),
            const Skeleton(height: 12, radius: 5),
            const SizedBox(height: 9),
            const Skeleton(height: 12, radius: 5),
            const SizedBox(height: 9),
            const Skeleton(width: 220, height: 12, radius: 5),
          ],
        ),
      );

  Widget _botRowSkeleton() => Padding(
        padding: const EdgeInsets.symmetric(vertical: 10),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: const [
            Skeleton(width: 150, height: 13, radius: 5),
            SizedBox(height: 6),
            Skeleton(width: 90, height: 10, radius: 4),
            SizedBox(height: 7),
            Skeleton(height: 10, radius: 4),
          ],
        ),
      );

  Widget _orderRowSkeleton() => Padding(
        padding: const EdgeInsets.symmetric(vertical: 8),
        child: Row(
          children: [
            const Skeleton(width: 40, height: 18, radius: 5),
            const SizedBox(width: 10),
            const Expanded(
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Skeleton(width: 110, height: 13, radius: 5),
                  SizedBox(height: 5),
                  Skeleton(width: 78, height: 10, radius: 4),
                ],
              ),
            ),
            const SizedBox(width: 10),
            const Skeleton(width: 54, height: 13, radius: 5),
          ],
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

    return Padding(
      padding: const EdgeInsets.only(top: 4),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          _sectionTitle('Your position'),
          const SizedBox(height: 12),
          Row(
            children: [
              if (frac != null) ...[
                _DiversityGauge(fraction: frac, color: color, size: 46),
                const SizedBox(width: 14),
              ],
              Expanded(
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Text('TOTAL P&L',
                        style: AppTextStyles.nano
                            .copyWith(color: AppColors.textFaint)),
                    const SizedBox(height: 3),
                    Row(
                      crossAxisAlignment: CrossAxisAlignment.baseline,
                      textBaseline: TextBaseline.alphabetic,
                      children: [
                        Text(fmtPnl(p.unrealizedPnl),
                            style:
                                AppTextStyles.valueLg.copyWith(color: color)),
                        const SizedBox(width: 6),
                        Text(fmtPct(p.unrealizedPnlPct),
                            style: AppTextStyles.micro.copyWith(
                                color: color, fontWeight: FontWeight.w700)),
                      ],
                    ),
                  ],
                ),
              ),
              Column(
                crossAxisAlignment: CrossAxisAlignment.end,
                children: [
                  Text('VALUE',
                      style: AppTextStyles.nano
                          .copyWith(color: AppColors.textFaint)),
                  const SizedBox(height: 3),
                  Text(fmtMoney(p.marketValue),
                      style: AppTextStyles.value
                          .copyWith(fontWeight: FontWeight.w700)),
                ],
              ),
            ],
          ),
          const SizedBox(height: 10),
          Text('$qtyStr shares · avg ${fmtMoney(p.avgEntryPrice)}',
              style: AppTextStyles.micro.copyWith(color: AppColors.textMuted)),
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
    return Padding(
      padding: const EdgeInsets.only(top: 4),
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

  // ── Bot activity: the bot's own buy/sell decisions for this symbol + why ──
  Widget _botCard() {
    final bid = widget.brokerageId;
    return Padding(
      padding: const EdgeInsets.only(top: 4),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          _sectionTitle('Bot activity'),
          const SizedBox(height: 8),
          if (bid == null)
            _ordersEmpty('No linked brokerage')
          else
            Consumer(builder: (context, ref, _) {
              final async = ref.watch(
                stockBotActivityProvider(
                    (brokerageId: bid, symbol: widget.symbol)),
              );
              return async.when(
                loading: () => Column(
                  children: [for (var i = 0; i < 2; i++) _botRowSkeleton()],
                ),
                error: (_, _) => _ordersEmpty("Couldn't load bot activity"),
                data: (events) => events.isEmpty
                    ? _ordersEmpty(
                        'No bot trades logged yet for ${widget.symbol}')
                    : Column(children: [
                        for (final e in events) _BotEventRow(event: e),
                      ]),
              );
            }),
        ],
      ),
    );
  }

  // ── Orders ──
  Widget _ordersCard() {
    final bid = widget.brokerageId;
    return Padding(
      padding: const EdgeInsets.only(top: 4),
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
                loading: () => Column(
                  children: [for (var i = 0; i < 3; i++) _orderRowSkeleton()],
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

/// Violet crown matching the dashboard: a deep violet top that falls off to
/// canvas black by ~a third down, a soft diagonal sheen, and a lavender bloom —
/// static, so the content glides over it.
class _StockBackdrop extends StatelessWidget {
  const _StockBackdrop();

  @override
  Widget build(BuildContext context) {
    return Positioned.fill(
      child: IgnorePointer(
        child: Stack(
          children: [
            const Positioned.fill(
              child: DecoratedBox(
                decoration: BoxDecoration(
                  gradient: LinearGradient(
                    begin: Alignment.topCenter,
                    end: Alignment.bottomCenter,
                    colors: [
                      Color(0xFF5A28BE),
                      Color(0xFF34176E),
                      Color(0xFF150A2C),
                      Color(0xFF04040C),
                    ],
                    stops: [0.0, 0.10, 0.22, 0.36],
                  ),
                ),
              ),
            ),
            Positioned(
              top: 0,
              left: 0,
              right: 0,
              height: 320,
              child: DecoratedBox(
                decoration: BoxDecoration(
                  gradient: LinearGradient(
                    begin: Alignment.bottomLeft,
                    end: Alignment.topRight,
                    colors: [
                      Colors.white.withValues(alpha: 0.0),
                      Colors.white.withValues(alpha: 0.0),
                      Colors.white.withValues(alpha: 0.08),
                      Colors.white.withValues(alpha: 0.0),
                    ],
                    stops: const [0.0, 0.52, 0.70, 0.88],
                  ),
                ),
              ),
            ),
            Positioned(
              top: -70,
              right: -60,
              child: Container(
                width: 280,
                height: 280,
                decoration: BoxDecoration(
                  shape: BoxShape.circle,
                  gradient: RadialGradient(
                    colors: [
                      const Color(0xFFB794FF).withValues(alpha: 0.13),
                      const Color(0xFFB794FF).withValues(alpha: 0.0),
                    ],
                  ),
                ),
              ),
            ),
          ],
        ),
      ),
    );
  }
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
  const _DiversityGauge(
      {required this.fraction, required this.color, this.size = 60});
  final double fraction;
  final Color color;
  final double size;

  String get _label {
    final pct = fraction * 100;
    if (pct <= 0) return '0%';
    if (pct < 1) return '<1%';
    return '${pct.round()}%';
  }

  @override
  Widget build(BuildContext context) {
    return SizedBox(
      width: size,
      height: size,
      child: Stack(
        alignment: Alignment.center,
        children: [
          CustomPaint(
            size: Size(size, size),
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

/// One real bot buy/sell for this symbol: a side chip, the driving strategy +
/// when, the reasoning, and (if any) the price it acted on / an override tag.
class _BotEventRow extends StatelessWidget {
  const _BotEventRow({required this.event});
  final BotTradeEvent event;

  @override
  Widget build(BuildContext context) {
    final color = event.isBuy ? AppColors.success : AppColors.danger;
    final title = (event.strategy ?? '').trim().isNotEmpty
        ? event.strategy!.trim()
        : (event.isBuy ? 'Buy decision' : 'Sell decision');
    final reason = event.reason.trim();
    // Other strategies that voted the same way (excluding the primary driver
    // already shown as the title) — the "who else agreed" detail.
    final backers = event.contributors
        .map((c) => (c.strategy ?? '').trim())
        .where((s) => s.isNotEmpty && s != title)
        .toSet()
        .take(3)
        .toList();
    return Padding(
      padding: const EdgeInsets.symmetric(vertical: 10),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Row(
            children: [
              Container(
                padding: const EdgeInsets.symmetric(horizontal: 7, vertical: 3),
                decoration: BoxDecoration(
                  color: AppColors.fill(color),
                  borderRadius: BorderRadius.circular(5),
                ),
                child: Text(event.side.toUpperCase(),
                    style: AppTextStyles.nano
                        .copyWith(color: color, fontWeight: FontWeight.w800)),
              ),
              const SizedBox(width: 8),
              Expanded(
                child: Text(title,
                    maxLines: 1,
                    overflow: TextOverflow.ellipsis,
                    style: AppTextStyles.bodyHi
                        .copyWith(fontWeight: FontWeight.w600)),
              ),
              if (event.ts != null)
                Text(fmtRelative(event.ts),
                    style:
                        AppTextStyles.nano.copyWith(color: AppColors.textDim)),
            ],
          ),
          if (reason.isNotEmpty) ...[
            const SizedBox(height: 5),
            Text(reason,
                maxLines: 4,
                overflow: TextOverflow.ellipsis,
                style: AppTextStyles.body
                    .copyWith(color: AppColors.textMd, height: 1.4)),
          ],
          if (backers.isNotEmpty) ...[
            const SizedBox(height: 5),
            Text('Backed by ${backers.join(', ')}',
                maxLines: 1,
                overflow: TextOverflow.ellipsis,
                style: AppTextStyles.micro.copyWith(
                    color: AppColors.primary, fontWeight: FontWeight.w600)),
          ],
          if (event.price != null || event.overrideApplied) ...[
            const SizedBox(height: 6),
            Row(
              children: [
                if (event.price != null)
                  Text('@ ${fmtMoney(event.price!)}',
                      style: AppTextStyles.micro
                          .copyWith(color: AppColors.textMuted)),
                if (event.price != null && event.overrideApplied)
                  const SizedBox(width: 10),
                if (event.overrideApplied)
                  Container(
                    padding:
                        const EdgeInsets.symmetric(horizontal: 6, vertical: 2),
                    decoration: BoxDecoration(
                      color: AppColors.fill(AppColors.primary),
                      borderRadius: BorderRadius.circular(4),
                    ),
                    child: Text('OVERRIDDEN',
                        style: AppTextStyles.nano.copyWith(
                            color: AppColors.primary,
                            fontWeight: FontWeight.w700,
                            letterSpacing: 0.4)),
                  ),
              ],
            ),
          ],
        ],
      ),
    );
  }
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
