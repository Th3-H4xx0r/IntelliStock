import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:syncfusion_flutter_charts/charts.dart';
import '../../../core/charts/chart_decorations.dart';
import '../../../core/charts/chart_geometry.dart';
import '../../../core/charts/scrub_controller.dart';
import '../../../core/polling/poller.dart';
import '../../../core/theme/app_colors.dart';
import '../../../core/theme/app_text_styles.dart';
import '../../../core/widgets/glass_card.dart';
import '../../../core/widgets/material_symbols.dart';
import '../../../core/widgets/skeleton.dart';
import '../../../core/formatters/formatters.dart';
import '../../../core/models/portfolio_history.dart';
import '../application/dashboard_controller.dart';
import '../data/dashboard_repository.dart';

// ── Range constants ───────────────────────────────────────────────────────────

const _ranges = ['1D', '1W', '1M', '3M', 'YTD', '1Y', 'ALL'];

// ── Per-account portfolio history state ──────────────────────────────────────

class _HistoryArgs {
  const _HistoryArgs(this.id, this.range);
  final String id;
  final String range;

  @override
  bool operator ==(Object other) =>
      other is _HistoryArgs && other.id == id && other.range == range;

  @override
  int get hashCode => Object.hash(id, range);
}

/// AutoDispose family provider for portfolio history (one per account + range).
final _historyProvider = AutoDisposeAsyncNotifierProviderFamily<
    _HistoryNotifier, PortfolioHistory, _HistoryArgs>(
  _HistoryNotifier.new,
);

class _HistoryNotifier
    extends AutoDisposeFamilyAsyncNotifier<PortfolioHistory, _HistoryArgs> {
  IntervalPoller? _poller;

  @override
  Future<PortfolioHistory> build(_HistoryArgs arg) async {
    final lifecycle = ref.read(appLifecycleProvider);
    final data = await _fetch(arg);
    // Stamp outside the build phase (Riverpod forbids mutating a provider
    // during another provider's build).
    Future.microtask(_stampUpdated);

    // Keep the value + curve live, like the live-trading screen. The 1D curve
    // grows continuously (incl. overnight); longer ranges barely move, so poll
    // those gently.
    _poller = IntervalPoller(
      fetch: () => _refresh(arg),
      interval: () => arg.range == '1D'
          ? const Duration(seconds: 5)
          : const Duration(seconds: 30),
    );
    if (lifecycle.isForeground) {
      _poller!.start();
    } else {
      _poller!.pause();
    }
    ref.listen(appLifecycleProvider, (_, next) {
      if (next.isForeground) {
        _poller?.resume();
      } else {
        _poller?.pause();
      }
    });
    ref.onDispose(() => _poller?.dispose());
    return data;
  }

  Future<PortfolioHistory> _fetch(_HistoryArgs arg) async {
    final h = await ref
        .read(dashboardRepositoryProvider)
        .portfolioHistory(arg.id, arg.range);
    // 1D is shown relative to the device's local midnight (overnight view).
    return arg.range == '1D' ? h.sinceLocalMidnight() : h;
  }

  Future<void> _refresh(_HistoryArgs arg) async {
    try {
      state = AsyncData(await _fetch(arg));
      _stampUpdated();
    } catch (_) {
      // keep the last good data on a transient poll failure
    }
  }

  void _stampUpdated() {
    // Guarded: the autoDispose notifier may already be gone on a late tick.
    try {
      ref.read(portfolioUpdatedAtProvider.notifier).state = DateTime.now();
    } catch (_) {/* provider disposed — ignore */}
  }
}

// ── Change % helper (pure, also tested) ──────────────────────────────────────

/// Compute absolute and percent change vs baseline (open_value or first point).
/// Returns (changeAbs, changePct) or (null, null) if not enough data.
(double?, double?) computeChange(
  PortfolioHistory history, {
  int? scrubIndex,
}) {
  if (history.isEmpty) return (null, null);

  final baseline = history.openValue ?? history.values.first;
  final double active;
  if (scrubIndex != null &&
      scrubIndex >= 0 &&
      scrubIndex < history.values.length) {
    active = history.values[scrubIndex];
  } else {
    active = history.currentValue ?? history.values.last;
  }

  final abs = active - baseline;
  if (baseline == 0) return (abs, null);
  final pct = (abs / baseline) * 100;
  return (abs, pct);
}

/// Find nearest data-point index by timestamp fraction across [0..1].
int nearestIndex(List<DateTime> timestamps, double fraction) {
  if (timestamps.isEmpty) return 0;
  if (timestamps.length == 1) return 0;
  final target = timestamps.first.millisecondsSinceEpoch +
      fraction *
          (timestamps.last.millisecondsSinceEpoch -
              timestamps.first.millisecondsSinceEpoch);
  var lo = 0;
  var hi = timestamps.length - 1;
  while (lo < hi) {
    final mid = (lo + hi) ~/ 2;
    if (timestamps[mid].millisecondsSinceEpoch < target) {
      lo = mid + 1;
    } else {
      hi = mid;
    }
  }
  if (lo <= 0) return 0;
  final prev = lo - 1;
  return (timestamps[lo].millisecondsSinceEpoch - target).abs() <
          (timestamps[prev].millisecondsSinceEpoch - target).abs()
      ? lo
      : prev;
}

// ── Main widget ───────────────────────────────────────────────────────────────

class PortfolioChart extends ConsumerStatefulWidget {
  const PortfolioChart({super.key, required this.account, this.hero = false});

  /// A [BrokerageAccount] from the dashboard brokerages list.
  final dynamic account; // BrokerageAccount from dashboard_repository.dart

  /// The primary account renders as a borderless hero (big balance on the
  /// gradient crown); secondary accounts render inside a clean surface card.
  final bool hero;

  @override
  ConsumerState<PortfolioChart> createState() => _PortfolioChartState();
}

class _PortfolioChartState extends ConsumerState<PortfolioChart>
    with AutomaticKeepAliveClientMixin {
  String _range = '1D';

  // The last successfully-loaded history. Kept so switching range only reloads
  // the chart — the headline value + P&L keep showing the previous data
  // instead of flashing a skeleton. Null only before the very first load.
  PortfolioHistory? _lastHistory;

  // Scrub state lives in a ValueNotifier so dragging repaints only the hairline
  // + header value, never the (expensive) Syncfusion chart — that's what made
  // the old setState-per-frame scrubber jank.
  final ScrubController _scrub = ScrubController();

  // Animate the chart only on first reveal — not every time it scrolls back
  // into view or polls.
  bool _animatedOnce = false;

  // Keep the chart alive while scrolled off-screen so it isn't recreated
  // (which would re-fetch and re-run the entry animation).
  @override
  bool get wantKeepAlive => true;

  @override
  void dispose() {
    _scrub.dispose();
    super.dispose();
  }

  _HistoryArgs get _args => _HistoryArgs(widget.account.id as String, _range);

  void _setRange(String r) {
    if (r == _range) return;
    _scrub.clear();
    setState(() => _range = r);
  }

  @override
  Widget build(BuildContext context) {
    super.build(context); // for AutomaticKeepAliveClientMixin
    final hero = widget.hero;
    final histAsync = ref.watch(_historyProvider(_args));

    // Cache the freshest data; render the value/P&L from it even while a range
    // switch is loading (skeleton the value only on the very first load).
    final current = histAsync.valueOrNull;
    if (current != null) _lastHistory = current;
    final valueHistory = current ?? _lastHistory;

    final content = Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Padding(
          // Hero aligns to the page gutter; cards pad inside their surface.
          padding: hero
              ? EdgeInsets.zero
              : const EdgeInsets.fromLTRB(16, 16, 16, 0),
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              // The hero's account identity is the dropdown selector above it,
              // so the in-card header is only drawn for secondary cards.
              if (!hero) ...[
                _CardHeader(account: widget.account),
                const SizedBox(height: 12),
              ],
              if (valueHistory != null)
                ValueListenableBuilder<ScrubSample?>(
                  valueListenable: _scrub,
                  builder: (_, sample, _) => _ValueRow(
                    history: valueHistory,
                    scrubIndex: sample?.index,
                    big: hero,
                  ),
                )
              else if (histAsync.hasError)
                Text(
                  histAsync.error.toString(),
                  style: AppTextStyles.micro.copyWith(color: AppColors.danger),
                )
              else
                _ValueSkeleton(big: hero),
              SizedBox(height: hero ? 16 : 12),
              _RangeTabs(active: _range, onSelect: _setRange),
            ],
          ),
        ),
        SizedBox(height: hero ? 22 : 8),
        histAsync.when(
          loading: () => const _ChartSkeleton(),
          error: (_, _) => const _ChartEmpty(message: 'Failed to load'),
          data: (history) {
            if (history.isEmpty) {
              return const _ChartEmpty(message: 'No data for this range');
            }
            // Play the entrance animation only the first time the chart
            // renders with data; flip the flag afterwards so scrolling away
            // and back (state kept alive) doesn't re-animate.
            final shouldAnimate = !_animatedOnce;
            if (shouldAnimate) {
              WidgetsBinding.instance.addPostFrameCallback(
                (_) => _animatedOnce = true,
              );
            }
            return _ChartArea(
              history: history,
              scrub: _scrub,
              range: _range,
              animate: shouldAnimate,
            );
          },
        ),
      ],
    );

    // Hero: borderless, sitting directly on the gradient crown.
    if (hero) return content;
    // Secondary accounts: a clean elevated surface card.
    return GlassCard(liquid: true, padding: EdgeInsets.zero, child: content);
  }
}

// ── Sub-widgets ───────────────────────────────────────────────────────────────

class _CardHeader extends StatelessWidget {
  const _CardHeader({required this.account});
  final dynamic account;

  @override
  Widget build(BuildContext context) {
    final type = account.brokerageType as String? ?? '';
    final isPaper =
        type == 'alpaca' && (account.alpacaPaper as bool? ?? false);
    final label = type == 'alpaca'
        ? (isPaper ? 'Alpaca · Paper' : 'Alpaca')
        : 'Robinhood';
    final icon = type == 'alpaca' ? symbol('show_chart') : symbol('savings');
    final isActive = (account.status as String? ?? '') == 'active';
    final statusColor = isActive ? AppColors.success : AppColors.danger;

    return Row(
      children: [
        Expanded(
          child: Row(
            children: [
              Icon(icon, color: AppColors.primary, size: 16),
              const SizedBox(width: 6),
              Text(
                label.toUpperCase(),
                style: AppTextStyles.nano.copyWith(
                  color: AppColors.textDim,
                  fontWeight: FontWeight.w700,
                  letterSpacing: 1.0,
                ),
              ),
            ],
          ),
        ),
        Row(
          mainAxisSize: MainAxisSize.min,
          children: [
            Container(
              width: 6,
              height: 6,
              decoration:
                  BoxDecoration(shape: BoxShape.circle, color: statusColor),
            ),
            const SizedBox(width: 4),
            Text(
              account.status as String? ?? '',
              style: AppTextStyles.nano.copyWith(color: statusColor),
            ),
          ],
        ),
      ],
    );
  }
}

class _ValueRow extends StatelessWidget {
  const _ValueRow({required this.history, this.scrubIndex, this.big = false});
  final PortfolioHistory history;
  final int? scrubIndex;
  final bool big;

  @override
  Widget build(BuildContext context) {
    final activeValue = (scrubIndex != null &&
            scrubIndex! >= 0 &&
            scrubIndex! < history.values.length)
        ? history.values[scrubIndex!]
        : (history.currentValue ?? (history.values.isNotEmpty ? history.values.last : 0.0));

    final (changeAbs, changePct) =
        computeChange(history, scrubIndex: scrubIndex);
    final positive = (changeAbs ?? 0) >= 0;
    final changeColor = positive ? AppColors.success : AppColors.danger;

    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        // The hero value rolls like an odometer: 0 → value on first load, then
        // value → scrubbed/updated value on every change. Secondary cards just
        // snap to the value.
        if (big)
          TweenAnimationBuilder<double>(
            tween: Tween<double>(begin: 0, end: activeValue),
            duration: const Duration(milliseconds: 500),
            curve: Curves.easeOutCubic,
            builder: (_, v, _) =>
                Text(fmtMoney(v), style: AppTextStyles.valueHero),
          )
        else
          Text(fmtMoney(activeValue), style: AppTextStyles.valueLg),
        SizedBox(height: big ? 6 : 4),
        Row(
          children: [
            Icon(
              positive ? symbol('trending_up') : symbol('trending_down'),
              size: big ? 16 : 14,
              color: changeColor,
            ),
            const SizedBox(width: 4),
            Text(
              '${fmtPnl(changeAbs)} (${fmtPct(changePct)})',
              style: (big ? AppTextStyles.bodyHi : AppTextStyles.meta).copyWith(
                color: changeColor,
                fontWeight: FontWeight.w600,
              ),
            ),
          ],
        ),
      ],
    );
  }
}

class _ValueSkeleton extends StatelessWidget {
  const _ValueSkeleton({this.big = false});
  final bool big;

  @override
  Widget build(BuildContext context) {
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Skeleton(width: big ? 200 : 120, height: big ? 40 : 24, radius: 6),
        const SizedBox(height: 6),
        Skeleton(width: big ? 120 : 80, height: 13, radius: 5),
      ],
    );
  }
}

class _RangeTabs extends StatelessWidget {
  const _RangeTabs({required this.active, required this.onSelect});
  final String active;
  final void Function(String) onSelect;

  @override
  Widget build(BuildContext context) {
    // A segmented control: a subtle track with the active range as a filled
    // pill — clean and modern, evenly spaced across the width.
    return Container(
      padding: const EdgeInsets.all(3),
      decoration: BoxDecoration(
        color: const Color(0x0FFFFFFF),
        borderRadius: BorderRadius.circular(10),
        border: Border.all(color: const Color(0x0DFFFFFF)),
      ),
      child: Row(
        children: _ranges.map((r) {
          final isActive = r == active;
          return Expanded(
            child: GestureDetector(
              behavior: HitTestBehavior.opaque,
              onTap: () => onSelect(r),
              child: AnimatedContainer(
                duration: const Duration(milliseconds: 160),
                curve: Curves.easeOut,
                padding: const EdgeInsets.symmetric(vertical: 7),
                decoration: BoxDecoration(
                  color:
                      isActive ? const Color(0x24FFFFFF) : Colors.transparent,
                  borderRadius: BorderRadius.circular(7),
                ),
                child: Text(
                  r,
                  textAlign: TextAlign.center,
                  style: AppTextStyles.micro.copyWith(
                    color: isActive ? AppColors.textHi : AppColors.textDim,
                    fontWeight: isActive ? FontWeight.w700 : FontWeight.w500,
                  ),
                ),
              ),
            ),
          );
        }).toList(),
      ),
    );
  }
}

class _ChartSkeleton extends StatelessWidget {
  const _ChartSkeleton();

  @override
  Widget build(BuildContext context) {
    return Padding(
      padding: const EdgeInsets.fromLTRB(4, 0, 4, 12),
      child: Skeleton(height: 224, radius: 8),
    );
  }
}

class _ChartEmpty extends StatelessWidget {
  const _ChartEmpty({required this.message});
  final String message;

  @override
  Widget build(BuildContext context) {
    return SizedBox(
      height: 224,
      child: Center(
        child: Column(
          mainAxisSize: MainAxisSize.min,
          children: [
            Icon(symbol('bar_chart_4_bars'),
                size: 32, color: AppColors.textFaint),
            const SizedBox(height: 8),
            Text(message,
                style:
                    AppTextStyles.micro.copyWith(color: AppColors.textFaint)),
          ],
        ),
      ),
    );
  }
}

class _ChartArea extends StatelessWidget {
  const _ChartArea({
    required this.history,
    required this.scrub,
    required this.range,
    required this.animate,
  });

  final PortfolioHistory history;
  final ScrubController scrub;
  final String range;

  /// Whether the area series should play its entrance animation. Only true on
  /// the chart's first build; suppressed afterwards so scrolling away and back
  /// doesn't re-animate.
  final bool animate;

  static const double _plotHeight = 224;
  static const double _dayMinutes = 24 * 60; // 1440

  bool get _isDay => range == '1D';

  /// Minutes since the point's own local midnight (used as the 1D x value).
  static double _minuteOfDay(DateTime ts) {
    final m = DateTime(ts.year, ts.month, ts.day);
    return ts.difference(m).inSeconds / 60.0;
  }

  @override
  Widget build(BuildContext context) {
    final (changeAbs, _) = computeChange(history);
    final positive = (changeAbs ?? 0) >= 0;
    final lineColor = positive ? AppColors.success : AppColors.danger;

    final n = history.values.length;
    final bounds = paddedBounds(history.values);

    // 1D plots against a fixed full-day [0, 1440]-minute axis so the line fills
    // only the elapsed part of the day (a short morning line, like Robinhood's
    // overnight view). Other ranges plot edge-to-edge by index (gapless across
    // weekends/closed sessions). history.values stays the single source of
    // truth for both the curve and the scrubber, so the header value matches.
    final xs = _isDay
        ? [for (final t in history.timestamps) _minuteOfDay(t)]
        : [for (var i = 0; i < n; i++) i.toDouble()];

    final dataSource = [
      for (var i = 0; i < n; i++) _ChartPoint(x: xs[i], y: history.values[i]),
    ];

    final labels = _isDay
        ? [for (final h in [0, 6, 12, 18, 24]) hourAmPm(h)]
        : [
            for (final i in evenlySpacedLabelIndices(n, 4))
              formatChartDate(history.timestamps[i], range),
          ];

    // Map a drag x to a data index + the fraction to draw the hairline at.
    void onDrag(double localDx, double width) {
      if (n == 0 || width <= 0) return;
      final frac = (localDx / width).clamp(0.0, 1.0);
      if (_isDay) {
        final targetMin = frac * _dayMinutes;
        var idx = 0;
        var best = double.infinity;
        for (var i = 0; i < n; i++) {
          final d = (xs[i] - targetMin).abs();
          if (d < best) {
            best = d;
            idx = i;
          }
        }
        scrub.update(idx, (xs[idx] / _dayMinutes).clamp(0.0, 1.0));
      } else {
        final idx = fractionToIndex(frac, n);
        scrub.update(idx, indexToFraction(idx, n));
      }
    }

    final chart = SfCartesianChart(
      plotAreaBorderWidth: 0,
      margin: EdgeInsets.zero,
      primaryXAxis:
          _isDay ? edgeToEdgeRangeAxis(0, _dayMinutes) : edgeToEdgeIndexAxis(n),
      primaryYAxis: hiddenValueAxis(minimum: bounds.min, maximum: bounds.max),
      tooltipBehavior: TooltipBehavior(enable: false),
      series: <CartesianSeries<_ChartPoint, double>>[
        SplineAreaSeries<_ChartPoint, double>(
          dataSource: dataSource,
          splineType: SplineType.monotonic,
          animationDuration: animate ? 900 : 0,
          xValueMapper: (pt, _) => pt.x,
          yValueMapper: (pt, _) => pt.y,
          color: lineColor.withValues(alpha: 0.12),
          borderColor: lineColor,
          borderWidth: 2,
          gradient: LinearGradient(
            begin: Alignment.topCenter,
            end: Alignment.bottomCenter,
            colors: [
              lineColor.withValues(alpha: 0.22),
              lineColor.withValues(alpha: 0.0),
            ],
          ),
        ),
      ],
    );

    return Padding(
      padding: const EdgeInsets.symmetric(horizontal: 4),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          SizedBox(
            height: _plotHeight,
            child: LayoutBuilder(
              builder: (context, constraints) {
                final width = constraints.maxWidth;
                return Stack(
                  children: [
                    // The chart never repaints during a scrub: it isn't wrapped
                    // in the ValueListenableBuilder below, so notifier ticks
                    // don't reach it.
                    Positioned.fill(child: RepaintBoundary(child: chart)),
                    // Hairline + dot — repaints only on scrub.
                    Positioned.fill(
                      child: IgnorePointer(
                        child: ValueListenableBuilder<ScrubSample?>(
                          valueListenable: scrub,
                          builder: (_, sample, _) {
                            // Not scrubbing → a persistent dot marks the latest
                            // value at the end of the line (no hairline).
                            if (sample == null ||
                                sample.index < 0 ||
                                sample.index >= n) {
                              if (n == 0) return const SizedBox.shrink();
                              final endFrac =
                                  _isDay ? (xs.last / _dayMinutes) : 1.0;
                              // Persistent "current value" marker — pulses to
                              // signal the chart is live-updating.
                              return _PulsingEndDot(
                                fraction: endFrac.clamp(0.0, 1.0),
                                dotY: valueToY(history.values[n - 1],
                                    bounds.min, bounds.max, _plotHeight),
                                color: lineColor,
                              );
                            }
                            final dotY = valueToY(
                              history.values[sample.index],
                              bounds.min,
                              bounds.max,
                              _plotHeight,
                            );
                            return CustomPaint(
                              painter: ScrubPainter(
                                fraction: sample.fraction,
                                dotY: dotY,
                                color: lineColor,
                              ),
                            );
                          },
                        ),
                      ),
                    ),
                    // Gesture overlay.
                    Positioned.fill(
                      child: GestureDetector(
                        behavior: HitTestBehavior.translucent,
                        onHorizontalDragStart: (d) =>
                            onDrag(d.localPosition.dx, width),
                        onHorizontalDragUpdate: (d) =>
                            onDrag(d.localPosition.dx, width),
                        onHorizontalDragEnd: (_) => scrub.clear(),
                        onHorizontalDragCancel: scrub.clear,
                      ),
                    ),
                  ],
                );
              },
            ),
          ),
          ChartDateLabels(labels: labels),
          const SizedBox(height: 8),
        ],
      ),
    );
  }
}

class _ChartPoint {
  const _ChartPoint({required this.x, required this.y});
  final double x;
  final double y;
}

/// The end-of-line "current value" dot, with a soft halo that continuously
/// expands and fades — a live-pulse signaling the chart is updating in
/// real time. Kept in its own RepaintBoundary so the 60 fps pulse never
/// repaints the Syncfusion chart underneath. Drawn only when not scrubbing.
class _PulsingEndDot extends StatefulWidget {
  const _PulsingEndDot({
    required this.fraction,
    required this.dotY,
    required this.color,
  });

  final double fraction;
  final double dotY;
  final Color color;

  @override
  State<_PulsingEndDot> createState() => _PulsingEndDotState();
}

class _PulsingEndDotState extends State<_PulsingEndDot>
    with SingleTickerProviderStateMixin {
  late final AnimationController _ctrl = AnimationController(
    vsync: this,
    duration: const Duration(milliseconds: 1600),
  )..repeat();

  @override
  void dispose() {
    _ctrl.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    return RepaintBoundary(
      child: AnimatedBuilder(
        animation: _ctrl,
        builder: (_, _) => CustomPaint(
          painter: _PulsingDotPainter(
            fraction: widget.fraction,
            dotY: widget.dotY,
            color: widget.color,
            t: _ctrl.value,
          ),
        ),
      ),
    );
  }
}

class _PulsingDotPainter extends CustomPainter {
  _PulsingDotPainter({
    required this.fraction,
    required this.dotY,
    required this.color,
    required this.t,
  });

  final double fraction;
  final double dotY;
  final Color color;

  /// Animation phase 0..1, driving the expanding/fading halo.
  final double t;

  @override
  void paint(Canvas canvas, Size size) {
    final x = (size.width * fraction).clamp(0.0, size.width);
    final y = dotY.clamp(0.0, size.height);
    final center = Offset(x, y);

    // Expanding, fading halo (the live "ping"): 4 → 16 px as it fades out.
    final haloRadius = 4 + t * 12;
    final haloAlpha = (1 - t) * 0.45;
    canvas.drawCircle(
      center,
      haloRadius,
      Paint()..color = color.withValues(alpha: haloAlpha),
    );

    // Static soft glow + solid core + white ring — matches the old end dot.
    canvas.drawCircle(
        center, 7, Paint()..color = color.withValues(alpha: 0.18));
    canvas.drawCircle(center, 4, Paint()..color = color);
    canvas.drawCircle(
      center,
      4,
      Paint()
        ..color = Colors.white.withValues(alpha: 0.9)
        ..style = PaintingStyle.stroke
        ..strokeWidth = 1.5,
    );
  }

  @override
  bool shouldRepaint(_PulsingDotPainter old) =>
      old.t != t ||
      old.fraction != fraction ||
      old.dotY != dotY ||
      old.color != color;
}
