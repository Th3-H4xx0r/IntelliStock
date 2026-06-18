import 'dart:math' as math;

import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';
import 'package:url_launcher/url_launcher.dart';

import '../../../core/formatters/formatters.dart';
import '../../../core/theme/app_colors.dart';
import '../../../core/theme/app_text_styles.dart';
import '../../../core/widgets/glass_card.dart';
import '../../../core/widgets/material_symbols.dart';
import '../../../core/widgets/skeleton.dart';
import '../../stock/presentation/stock_screen.dart';
import '../application/dashboard_controller.dart';
import '../application/insights_controller.dart';
import '../application/portfolio_analytics.dart';
import '../application/selected_account_controller.dart';

/// The "Insights" + "Market" block beneath the portfolio section: today's
/// movers, day P&L, diversification, and sector allocation for the account,
/// plus real market data (major indices + sector performance). Scoped to the
/// selected account; renders nothing until one resolves.
class InsightsSection extends ConsumerWidget {
  const InsightsSection({super.key});

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final accounts = ref.watch(brokeragesProvider).valueOrNull;
    if (accounts == null || accounts.isEmpty) return const SizedBox.shrink();
    final selectedId = ref.watch(selectedAccountProvider);
    final id = (selectedId != null && accounts.any((a) => a.id == selectedId))
        ? selectedId
        : accounts.first.id;

    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Text('Insights', style: AppTextStyles.h3),
        const SizedBox(height: 14),
        _TodaysMoversStrip(brokerageId: id),
        Row(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Expanded(child: _DayPnlTile(brokerageId: id)),
            const SizedBox(width: 12),
            Expanded(child: _DiversificationTile(brokerageId: id)),
          ],
        ),
        const SizedBox(height: 12),
        _SectorAllocationCard(brokerageId: id),
        _RiskCard(brokerageId: id),
        const SizedBox(height: 20),
        Text('Market', style: AppTextStyles.h3),
        const SizedBox(height: 14),
        const _MarketIndicesCard(),
        const _SectorPerformanceCard(),
        _MarketMoversCard(brokerageId: id),
        _NexusMomentumCard(brokerageId: id),
        const _MarketNewsCard(),
      ],
    );
  }
}

// ── Today's movers ────────────────────────────────────────────────────────────

class _TodaysMoversStrip extends ConsumerWidget {
  const _TodaysMoversStrip({required this.brokerageId});
  final String brokerageId;

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final movers = ref.watch(todaysMoversProvider(brokerageId)).valueOrNull;
    if (movers == null || movers.isEmpty) return const SizedBox.shrink();
    return Padding(
      padding: const EdgeInsets.only(bottom: 12),
      child: SizedBox(
        height: 38,
        child: ListView.separated(
          scrollDirection: Axis.horizontal,
          itemCount: movers.length,
          separatorBuilder: (_, _) => const SizedBox(width: 8),
          itemBuilder: (_, i) => _MoverChip(mover: movers[i], brokerageId: brokerageId),
        ),
      ),
    );
  }
}

class _MoverChip extends StatelessWidget {
  const _MoverChip({required this.mover, required this.brokerageId});
  final Mover mover;
  final String brokerageId;

  @override
  Widget build(BuildContext context) {
    final up = mover.pct >= 0;
    final c = up ? AppColors.success : AppColors.danger;
    return GestureDetector(
      behavior: HitTestBehavior.opaque,
      onTap: () => context.push('/stock/${mover.symbol}',
          extra: StockScreenArgs(brokerageId: brokerageId)),
      child: Container(
        padding: const EdgeInsets.symmetric(horizontal: 11, vertical: 7),
        decoration: BoxDecoration(
          color: AppColors.fill(c),
          borderRadius: BorderRadius.circular(9),
          border: Border.all(color: c.withValues(alpha: 0.25)),
        ),
        child: Row(
          mainAxisSize: MainAxisSize.min,
          children: [
            Text(mover.symbol,
                style: AppTextStyles.micro
                    .copyWith(color: AppColors.textHi, fontWeight: FontWeight.w700)),
            const SizedBox(width: 6),
            Text('${up ? '▲' : '▼'} ${fmtPct(mover.pct)}',
                style: AppTextStyles.micro
                    .copyWith(color: c, fontWeight: FontWeight.w700)),
          ],
        ),
      ),
    );
  }
}

// ── Day P&L + Diversification tiles ─────────────────────────────────────────

class _DayPnlTile extends ConsumerWidget {
  const _DayPnlTile({required this.brokerageId});
  final String brokerageId;

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final async = ref.watch(dayChangeProvider(brokerageId));
    final d = async.valueOrNull;
    return GlassCard(
      frosted: true,
      padding: const EdgeInsets.all(14),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          _tileLabel('TODAY'),
          const SizedBox(height: 6),
          if (async.isLoading && d == null)
            const Skeleton(width: 90, height: 22, radius: 6)
          else if (d == null)
            Text('—', style: AppTextStyles.valueLg)
          else
            _DayPnlValue(d: d),
        ],
      ),
    );
  }
}

class _DayPnlValue extends StatelessWidget {
  const _DayPnlValue({required this.d});
  final DayChange d;

  @override
  Widget build(BuildContext context) {
    final up = d.abs >= 0;
    final c = up ? AppColors.success : AppColors.danger;
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        // Odometer roll on the live day-P&L value (updates every 5 s poll).
        TweenAnimationBuilder<double>(
          tween: Tween<double>(begin: 0, end: d.abs),
          duration: const Duration(milliseconds: 500),
          curve: Curves.easeOutCubic,
          builder: (_, v, _) =>
              Text(fmtPnl(v), style: AppTextStyles.valueLg.copyWith(color: c)),
        ),
        const SizedBox(height: 2),
        Row(
          children: [
            Icon(up ? symbol('trending_up') : symbol('trending_down'),
                size: 13, color: c),
            const SizedBox(width: 3),
            Text(fmtPct(d.pct),
                style: AppTextStyles.micro
                    .copyWith(color: c, fontWeight: FontWeight.w700)),
          ],
        ),
      ],
    );
  }
}

class _DiversificationTile extends ConsumerWidget {
  const _DiversificationTile({required this.brokerageId});
  final String brokerageId;

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final async = ref.watch(concentrationProvider(brokerageId));
    final s = async.valueOrNull;
    return GlassCard(
      frosted: true,
      padding: const EdgeInsets.all(14),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          _tileLabel('DIVERSIFICATION'),
          const SizedBox(height: 6),
          if (async.isLoading && s == null)
            const Skeleton(width: 70, height: 22, radius: 6)
          else if (s == null || s.isEmpty)
            Text('—', style: AppTextStyles.valueLg)
          else
            _DiversificationValue(s: s),
        ],
      ),
    );
  }
}

class _DiversificationValue extends StatelessWidget {
  const _DiversificationValue({required this.s});
  final ConcentrationStats s;

  @override
  Widget build(BuildContext context) {
    // Green when well-spread, amber mid, red when concentrated.
    final c = s.score >= 66
        ? AppColors.success
        : (s.score >= 33 ? const Color(0xFFFBBF24) : AppColors.danger);
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Row(
          crossAxisAlignment: CrossAxisAlignment.baseline,
          textBaseline: TextBaseline.alphabetic,
          children: [
            Text('${s.score}',
                style: AppTextStyles.valueLg.copyWith(color: c)),
            Text(' /100',
                style: AppTextStyles.micro.copyWith(color: AppColors.textFaint)),
          ],
        ),
        const SizedBox(height: 2),
        Text('Top ${s.topWeight.round()}% · ${s.count} holdings',
            maxLines: 1,
            overflow: TextOverflow.ellipsis,
            style: AppTextStyles.micro.copyWith(color: AppColors.textMuted)),
      ],
    );
  }
}

// ── Risk (volatility / drawdown / Sharpe from the equity curve) ──────────────

class _RiskCard extends ConsumerWidget {
  const _RiskCard({required this.brokerageId});
  final String brokerageId;

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final async = ref.watch(riskMetricsProvider(brokerageId));
    final r = async.valueOrNull;
    if (r != null && r.isEmpty) return const SizedBox.shrink();
    return Padding(
      padding: const EdgeInsets.only(bottom: 12),
      child: GlassCard(
        frosted: true,
        padding: const EdgeInsets.all(16),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            _tileLabel('RISK'),
            const SizedBox(height: 12),
            if (r == null)
              const Skeleton(height: 22, radius: 6)
            else
              Row(
                children: [
                  Expanded(
                      child: _riskMetric(
                          'Volatility', '${r.volatility.round()}%')),
                  Expanded(
                      child: _riskMetric(
                          'Max drawdown', '${r.maxDrawdown.round()}%')),
                  Expanded(
                      child: _riskMetric('Sharpe',
                          r.sharpe == null ? '—' : r.sharpe!.toStringAsFixed(2))),
                ],
              ),
          ],
        ),
      ),
    );
  }

  Widget _riskMetric(String label, String value) => Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Text(value,
              maxLines: 1,
              overflow: TextOverflow.ellipsis,
              style: AppTextStyles.value.copyWith(fontWeight: FontWeight.w700)),
          const SizedBox(height: 3),
          Text(label,
              maxLines: 1,
              overflow: TextOverflow.ellipsis,
              style: AppTextStyles.nano.copyWith(color: AppColors.textFaint)),
        ],
      );
}

// ── Sector allocation — metallic gradient donut (Robinhood-style) ────────────

class _SectorAllocationCard extends ConsumerWidget {
  const _SectorAllocationCard({required this.brokerageId});
  final String brokerageId;

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final async = ref.watch(sectorAllocationProvider(brokerageId));
    final slices = async.valueOrNull;
    // Hide entirely once loaded with nothing (e.g. all cash / no sectors).
    if (slices != null && slices.isEmpty) return const SizedBox.shrink();
    return Padding(
      padding: const EdgeInsets.only(bottom: 12),
      child: GlassCard(
        frosted: true,
        padding: const EdgeInsets.all(16),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            _tileLabel('SECTOR ALLOCATION'),
            const SizedBox(height: 8),
            if (slices == null)
              const Padding(
                padding: EdgeInsets.symmetric(vertical: 20),
                child: Center(child: Skeleton(width: 180, height: 180, radius: 90)),
              )
            else
              // Sorted desc by aggregateBySector, so index 0 is the largest.
              _SectorDonut(slices: slices),
          ],
        ),
      ),
    );
  }
}

/// An animated, tappable metallic donut: each sector is a graphite-gradient
/// segment; the selected one (largest by default) is a violet gradient. Tap a
/// segment to select it; the centre shows its name + %. Sweeps in on first paint.
class _SectorDonut extends StatefulWidget {
  const _SectorDonut({required this.slices});
  final List<SectorSlice> slices;

  @override
  State<_SectorDonut> createState() => _SectorDonutState();
}

class _SectorDonutState extends State<_SectorDonut>
    with SingleTickerProviderStateMixin {
  late final AnimationController _ctrl = AnimationController(
    vsync: this,
    duration: const Duration(milliseconds: 1000),
  )..forward();
  int _selected = 0;

  @override
  void dispose() {
    _ctrl.dispose();
    super.dispose();
  }

  void _onTapDown(Offset local, double dim) {
    final center = Offset(dim / 2, dim / 2);
    final v = local - center;
    final dist = v.distance;
    final r = dim / 2;
    if (dist < r * 0.40 || dist > r) return; // outside the ring band
    var a = math.atan2(v.dy, v.dx) + math.pi / 2; // 0 at 12 o'clock, CW
    if (a < 0) a += 2 * math.pi;
    final total = widget.slices.fold<double>(0, (s, e) => s + e.pct);
    if (total <= 0) return;
    var acc = 0.0;
    for (var i = 0; i < widget.slices.length; i++) {
      final seg = widget.slices[i].pct / total * 2 * math.pi;
      if (a >= acc && a < acc + seg) {
        setState(() => _selected = i);
        return;
      }
      acc += seg;
    }
  }

  @override
  Widget build(BuildContext context) {
    final slices = widget.slices;
    final sel = _selected.clamp(0, slices.length - 1);
    const dim = 210.0;
    return Center(
      child: Padding(
        padding: const EdgeInsets.symmetric(vertical: 6),
        child: SizedBox(
          width: dim,
          height: dim,
          child: GestureDetector(
            behavior: HitTestBehavior.opaque,
            onTapDown: (d) => _onTapDown(d.localPosition, dim),
            child: AnimatedBuilder(
              animation: _ctrl,
              builder: (_, _) => CustomPaint(
                painter: _DonutPainter(
                  slices: slices,
                  selected: sel,
                  progress: Curves.easeOutCubic.transform(_ctrl.value),
                ),
                child: Center(
                  child: Column(
                    mainAxisSize: MainAxisSize.min,
                    children: [
                      Text('${slices[sel].pct.round()}%',
                          style: AppTextStyles.valueXl
                              .copyWith(fontWeight: FontWeight.w800)),
                      const SizedBox(height: 2),
                      Padding(
                        padding: const EdgeInsets.symmetric(horizontal: 34),
                        child: Text(slices[sel].sector,
                            maxLines: 2,
                            textAlign: TextAlign.center,
                            overflow: TextOverflow.ellipsis,
                            style: AppTextStyles.micro
                                .copyWith(color: AppColors.textMuted)),
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
}

class _DonutPainter extends CustomPainter {
  _DonutPainter(
      {required this.slices, required this.selected, required this.progress});
  final List<SectorSlice> slices;
  final int selected;
  final double progress;

  @override
  void paint(Canvas canvas, Size size) {
    final total = slices.fold<double>(0, (s, e) => s + e.pct);
    if (total <= 0) return;
    final center = size.center(Offset.zero);
    final r = size.width / 2;
    final stroke = r * 0.26;
    final ringR = r - stroke / 2 - 14; // leave room for the % labels outside
    final rect = Rect.fromCircle(center: center, radius: ringR);
    const gap = 0.05; // radians between segments

    var start = -math.pi / 2; // 12 o'clock
    for (var i = 0; i < slices.length; i++) {
      final full = slices[i].pct / total * 2 * math.pi;
      final sweep = (full - gap).clamp(0.0, full) * progress;
      if (sweep > 0.001) {
        final isSel = i == selected;
        // Metallic gradient: violet for the selected segment, graphite for the
        // rest. The looping 4th stop gives a subtle specular sheen.
        final light = isSel ? const Color(0xFFCBB6FF) : const Color(0xFF585866);
        final base = isSel ? const Color(0xFF9B6BFF) : const Color(0xFF35353F);
        final dark = isSel ? const Color(0xFF5B21B6) : const Color(0xFF17171E);
        final shader = SweepGradient(
          startAngle: start,
          endAngle: start + full,
          colors: [light, base, dark, light],
          stops: const [0.0, 0.4, 0.82, 1.0],
        ).createShader(rect);
        canvas.drawArc(
          rect,
          start + gap / 2,
          sweep,
          false,
          Paint()
            ..style = PaintingStyle.stroke
            ..strokeWidth = isSel ? stroke + 6 : stroke
            ..strokeCap = StrokeCap.round
            ..shader = shader,
        );
      }
      start += full;
    }

    // % labels just outside the ring at each segment's mid-angle (fade in late).
    if (progress > 0.55) {
      start = -math.pi / 2;
      final labelR = ringR + stroke / 2 + 12;
      for (var i = 0; i < slices.length; i++) {
        final full = slices[i].pct / total * 2 * math.pi;
        final mid = start + full / 2;
        final pos =
            center + Offset(math.cos(mid) * labelR, math.sin(mid) * labelR);
        final tp = TextPainter(
          text: TextSpan(
            text: '${slices[i].pct.round()}%',
            style: TextStyle(
              color: i == selected ? AppColors.primary : AppColors.textMuted,
              fontSize: 11,
              fontWeight: FontWeight.w700,
            ),
          ),
          textDirection: TextDirection.ltr,
        )..layout();
        tp.paint(canvas, pos - Offset(tp.width / 2, tp.height / 2));
        start += full;
      }
    }
  }

  @override
  bool shouldRepaint(_DonutPainter old) =>
      old.progress != progress ||
      old.selected != selected ||
      !identical(old.slices, slices);
}

// ── Market indices — horizontal sparkline cards (S&P / Nasdaq / Dow / RUT) ───

class _MarketIndicesCard extends ConsumerWidget {
  const _MarketIndicesCard();

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final quotes = ref.watch(marketIndicesProvider).valueOrNull;
    if (quotes != null && quotes.isEmpty) return const SizedBox.shrink();
    return Padding(
      padding: const EdgeInsets.only(bottom: 12),
      child: SizedBox(
        height: 150,
        child: quotes == null
            ? const Skeleton(height: 150, radius: 16)
            : ListView.separated(
                scrollDirection: Axis.horizontal,
                itemCount: quotes.length,
                separatorBuilder: (_, _) => const SizedBox(width: 12),
                itemBuilder: (_, i) => _IndexCard(quote: quotes[i]),
              ),
      ),
    );
  }
}

class _IndexCard extends StatelessWidget {
  const _IndexCard({required this.quote});
  final MarketQuote quote;

  @override
  Widget build(BuildContext context) {
    final up = quote.pct >= 0;
    final c = up ? AppColors.success : AppColors.danger;
    final level = quote.values.isNotEmpty ? quote.values.last : null;
    return SizedBox(
      width: 156,
      child: GlassCard(
        frosted: true,
        padding: const EdgeInsets.all(14),
        onTap: () => context.push('/stock/${quote.symbol}',
            extra: const StockScreenArgs()),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Text(quote.label,
                maxLines: 1,
                overflow: TextOverflow.ellipsis,
                style:
                    AppTextStyles.bodyHi.copyWith(fontWeight: FontWeight.w700)),
            const SizedBox(height: 10),
            SizedBox(
              height: 40,
              width: double.infinity,
              child: CustomPaint(
                painter: _IndexSparkPainter(values: quote.values, color: c),
              ),
            ),
            const Spacer(),
            Text(level != null ? _fmtLevel(level) : '—',
                maxLines: 1,
                overflow: TextOverflow.ellipsis,
                style:
                    AppTextStyles.value.copyWith(fontWeight: FontWeight.w800)),
            const SizedBox(height: 2),
            Text('${up ? '▲' : '▼'} ${fmtPct(quote.pct)}',
                maxLines: 1,
                overflow: TextOverflow.ellipsis,
                style: AppTextStyles.micro
                    .copyWith(color: c, fontWeight: FontWeight.w700)),
          ],
        ),
      ),
    );
  }

  /// Thousands-separated index level, 2 dp (e.g. 7,489.78).
  static String _fmtLevel(double v) {
    final s = v.toStringAsFixed(2);
    final parts = s.split('.');
    final intPart = parts[0];
    final buf = StringBuffer();
    for (var i = 0; i < intPart.length; i++) {
      if (i > 0 && (intPart.length - i) % 3 == 0) buf.write(',');
      buf.write(intPart[i]);
    }
    return '$buf.${parts[1]}';
  }
}

class _IndexSparkPainter extends CustomPainter {
  _IndexSparkPainter({required this.values, required this.color});
  final List<double> values;
  final Color color;

  @override
  void paint(Canvas canvas, Size size) {
    if (values.length < 2) return;
    var lo = values.first, hi = values.first;
    for (final v in values) {
      if (v < lo) lo = v;
      if (v > hi) hi = v;
    }
    final span = (hi - lo).abs() < 1e-9 ? 1.0 : (hi - lo);
    double px(int i) => size.width * i / (values.length - 1);
    double py(double v) =>
        size.height - ((v - lo) / span) * (size.height - 8) - 4;

    // Dashed baseline at the opening value.
    final baseY = py(values.first);
    final dash = Paint()
      ..color = AppColors.textFaint.withValues(alpha: 0.4)
      ..strokeWidth = 1;
    for (double x = 0; x < size.width; x += 6) {
      canvas.drawLine(Offset(x, baseY), Offset(x + 3, baseY), dash);
    }

    final path = Path()..moveTo(0, py(values.first));
    for (var i = 1; i < values.length; i++) {
      path.lineTo(px(i), py(values[i]));
    }
    canvas.drawPath(
      path,
      Paint()
        ..color = color
        ..style = PaintingStyle.stroke
        ..strokeWidth = 2
        ..strokeJoin = StrokeJoin.round
        ..strokeCap = StrokeCap.round,
    );
    final end = Offset(size.width, py(values.last));
    canvas.drawCircle(end, 5, Paint()..color = color.withValues(alpha: 0.22));
    canvas.drawCircle(end, 3, Paint()..color = color);
  }

  @override
  bool shouldRepaint(_IndexSparkPainter old) =>
      old.color != color || !identical(old.values, values);
}

// ── Sector performance (today's market sectors, ranked) ──────────────────────

class _SectorPerformanceCard extends ConsumerWidget {
  const _SectorPerformanceCard();

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final async = ref.watch(sectorPerformanceProvider);
    final quotes = async.valueOrNull;
    if (quotes != null && quotes.isEmpty) return const SizedBox.shrink();
    return Padding(
      padding: const EdgeInsets.only(bottom: 12),
      child: GlassCard(
        frosted: true,
        padding: const EdgeInsets.all(16),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            _tileLabel('SECTOR PERFORMANCE'),
            const SizedBox(height: 12),
            if (quotes == null)
              const Skeleton(height: 80, radius: 7)
            else
              for (final q in quotes) _SectorPerfRow(quote: q),
          ],
        ),
      ),
    );
  }
}

class _SectorPerfRow extends StatelessWidget {
  const _SectorPerfRow({required this.quote});
  final MarketQuote quote;

  @override
  Widget build(BuildContext context) {
    final up = quote.pct >= 0;
    final c = up ? AppColors.success : AppColors.danger;
    // Scale the bar against a nominal 3% daily move so typical moves are visible.
    final frac = (quote.pct.abs() / 3.0).clamp(0.0, 1.0);
    return Padding(
      padding: const EdgeInsets.symmetric(vertical: 5),
      child: Row(
        children: [
          SizedBox(
            width: 108,
            child: Text(quote.label,
                maxLines: 1,
                overflow: TextOverflow.ellipsis,
                style: AppTextStyles.micro.copyWith(color: AppColors.textMd)),
          ),
          Expanded(
            child: ClipRRect(
              borderRadius: BorderRadius.circular(3),
              child: LinearProgressIndicator(
                value: frac,
                minHeight: 5,
                backgroundColor: Colors.white.withValues(alpha: 0.06),
                valueColor: AlwaysStoppedAnimation<Color>(c),
              ),
            ),
          ),
          const SizedBox(width: 10),
          SizedBox(
            width: 58,
            child: Text(fmtPct(quote.pct),
                textAlign: TextAlign.right,
                style: AppTextStyles.micro
                    .copyWith(color: c, fontWeight: FontWeight.w700)),
          ),
        ],
      ),
    );
  }
}

// ── Market movers (account's Alpaca screener) ───────────────────────────────

class _MarketMoversCard extends ConsumerWidget {
  const _MarketMoversCard({required this.brokerageId});
  final String brokerageId;

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final async = ref.watch(marketMoversProvider(brokerageId));
    final data = async.valueOrNull;
    if (data != null && data.gainers.isEmpty && data.losers.isEmpty) {
      return const SizedBox.shrink();
    }
    return Padding(
      padding: const EdgeInsets.only(bottom: 12),
      child: GlassCard(
        frosted: true,
        padding: const EdgeInsets.all(16),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            _tileLabel('MARKET MOVERS'),
            const SizedBox(height: 12),
            if (data == null)
              const Skeleton(height: 70, radius: 7)
            else
              Row(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Expanded(
                      child: _MoverColumn(
                          title: 'Gainers',
                          movers: data.gainers,
                          brokerageId: brokerageId)),
                  const SizedBox(width: 12),
                  Expanded(
                      child: _MoverColumn(
                          title: 'Losers',
                          movers: data.losers,
                          brokerageId: brokerageId)),
                ],
              ),
          ],
        ),
      ),
    );
  }
}

class _MoverColumn extends StatelessWidget {
  const _MoverColumn(
      {required this.title, required this.movers, required this.brokerageId});
  final String title;
  final List<MarketMover> movers;
  final String brokerageId;

  @override
  Widget build(BuildContext context) {
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Text(title.toUpperCase(),
            style: AppTextStyles.nano.copyWith(color: AppColors.textFaint)),
        const SizedBox(height: 6),
        for (final m in movers.take(5))
          GestureDetector(
            behavior: HitTestBehavior.opaque,
            onTap: () => context.push('/stock/${m.symbol}',
                extra: const StockScreenArgs()),
            child: Padding(
              padding: const EdgeInsets.symmetric(vertical: 4),
              child: Row(
                children: [
                  Expanded(
                    child: Text(m.symbol,
                        maxLines: 1,
                        overflow: TextOverflow.ellipsis,
                        style: AppTextStyles.micro.copyWith(
                            color: AppColors.textHi,
                            fontWeight: FontWeight.w700)),
                  ),
                  Text(fmtPct(m.pct),
                      style: AppTextStyles.micro.copyWith(
                          color: (m.pct ?? 0) >= 0
                              ? AppColors.success
                              : AppColors.danger,
                          fontWeight: FontWeight.w700)),
                ],
              ),
            ),
          ),
      ],
    );
  }
}

// ── Nexus momentum (the bot strategy's ranked picks for this account) ────────

class _NexusMomentumCard extends ConsumerWidget {
  const _NexusMomentumCard({required this.brokerageId});
  final String brokerageId;

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final picks = ref.watch(nexusMomentumProvider(brokerageId)).valueOrNull;
    // Only the nexus strategy with momentum enabled produces this — hide
    // entirely otherwise.
    if (picks == null || picks.isEmpty) return const SizedBox.shrink();
    final maxScore = picks
        .map((p) => p.score.abs())
        .fold<double>(0, (a, b) => a > b ? a : b);
    return Padding(
      padding: const EdgeInsets.only(bottom: 12),
      child: GlassCard(
        frosted: true,
        padding: const EdgeInsets.all(16),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Row(
              children: [
                Icon(symbol('bolt'), size: 15, color: AppColors.primary),
                const SizedBox(width: 6),
                _tileLabel('NEXUS MOMENTUM'),
              ],
            ),
            const SizedBox(height: 10),
            for (final p in picks.take(10))
              GestureDetector(
                behavior: HitTestBehavior.opaque,
                onTap: () => context.push('/stock/${p.symbol}',
                    extra: StockScreenArgs(brokerageId: brokerageId)),
                child: Padding(
                  padding: const EdgeInsets.symmetric(vertical: 5),
                  child: Row(
                    children: [
                      SizedBox(
                        width: 64,
                        child: Text(p.symbol,
                            maxLines: 1,
                            overflow: TextOverflow.ellipsis,
                            style: AppTextStyles.micro.copyWith(
                                color: AppColors.textHi,
                                fontWeight: FontWeight.w700)),
                      ),
                      Expanded(
                        child: ClipRRect(
                          borderRadius: BorderRadius.circular(3),
                          child: LinearProgressIndicator(
                            value: maxScore > 0
                                ? (p.score.abs() / maxScore).clamp(0.0, 1.0)
                                : 0.0,
                            minHeight: 5,
                            backgroundColor:
                                Colors.white.withValues(alpha: 0.06),
                            valueColor: const AlwaysStoppedAnimation<Color>(
                                AppColors.primary),
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
    );
  }
}

// ── Market news (Google News headlines) ──────────────────────────────────────

class _MarketNewsCard extends ConsumerWidget {
  const _MarketNewsCard();

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final async = ref.watch(marketNewsProvider);
    final articles = async.valueOrNull;
    if (articles != null && articles.isEmpty) return const SizedBox.shrink();
    return Padding(
      padding: const EdgeInsets.only(bottom: 12),
      child: GlassCard(
        frosted: true,
        padding: const EdgeInsets.all(16),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            _tileLabel('MARKET NEWS'),
            const SizedBox(height: 8),
            if (articles == null)
              const Skeleton(height: 60, radius: 7)
            else
              for (final a in articles.take(6))
                GestureDetector(
                  behavior: HitTestBehavior.opaque,
                  onTap: () => _openInAppBrowser(a.url),
                  child: Padding(
                    padding: const EdgeInsets.symmetric(vertical: 7),
                    child: Row(
                      crossAxisAlignment: CrossAxisAlignment.start,
                      children: [
                        Expanded(
                          child: Column(
                            crossAxisAlignment: CrossAxisAlignment.start,
                            children: [
                              Text(a.title,
                                  maxLines: 2,
                                  overflow: TextOverflow.ellipsis,
                                  style: AppTextStyles.bodyHi.copyWith(
                                      fontWeight: FontWeight.w600, height: 1.3)),
                              const SizedBox(height: 3),
                              Text(
                                [
                                  if (a.source.isNotEmpty) a.source,
                                  if (a.publishedAt != null)
                                    fmtRelative(a.publishedAt),
                                ].join('  ·  '),
                                style: AppTextStyles.nano
                                    .copyWith(color: AppColors.textDim),
                              ),
                            ],
                          ),
                        ),
                        const SizedBox(width: 8),
                        Padding(
                          padding: const EdgeInsets.only(top: 2),
                          child: Icon(symbol('arrow_forward'),
                              size: 13, color: AppColors.textFaint),
                        ),
                      ],
                    ),
                  ),
                ),
          ],
        ),
      ),
    );
  }
}

// ── Shared bits ───────────────────────────────────────────────────────────────

/// Open a URL in an embedded in-app browser (SFSafariViewController on iOS).
/// Best-effort — silently no-ops on a bad/empty URL or launch failure.
Future<void> _openInAppBrowser(String url) async {
  final u = url.trim();
  if (u.isEmpty) return;
  final uri = Uri.tryParse(u);
  if (uri == null || !(uri.scheme == 'http' || uri.scheme == 'https')) return;
  try {
    await launchUrl(uri, mode: LaunchMode.inAppBrowserView);
  } catch (_) {
    try {
      await launchUrl(uri);
    } catch (_) {/* give up silently */}
  }
}

Widget _tileLabel(String s) => Text(
      s,
      style: AppTextStyles.nano.copyWith(
        color: AppColors.textFaint,
        fontWeight: FontWeight.w700,
        letterSpacing: 0.8,
      ),
    );
