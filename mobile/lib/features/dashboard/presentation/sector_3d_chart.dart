import 'dart:math' as math;

import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import '../../../core/theme/app_colors.dart';
import '../../../core/theme/app_text_styles.dart';
import '../application/portfolio_analytics.dart';

/// A smooth metallic allocation ring that DRILLS IN on tap: it starts flat
/// (lit-from-above brushed-metal ring with gaps, % labels, a selection pointer
/// and a centre readout), and when you tap a sector it animates — zooming,
/// tilting low and extruding into tall 3D metallic blocks with the focused
/// sector raised at the top, a "Growth / NAME %" header, and a "Back" affordance
/// to return. Swipe snaps to the next sector (haptic) in either state.
class Sector3DChart extends StatefulWidget {
  const Sector3DChart({super.key, required this.slices, this.debugDrill});
  final List<SectorSlice> slices;

  /// Test-only: force the drill animation value (0 flat .. 1 drilled).
  @visibleForTesting
  final double? debugDrill;

  @override
  State<Sector3DChart> createState() => _Sector3DChartState();
}

class _Sector3DChartState extends State<Sector3DChart>
    with SingleTickerProviderStateMixin {
  static const double _flatH = 232;
  static const double _drillH = 300;

  int _selected = 0;
  double _dragAcc = 0;
  final List<_WedgeHit> _hits = [];

  late final AnimationController _drill = AnimationController(
    vsync: this,
    duration: const Duration(milliseconds: 620),
  );

  @override
  void dispose() {
    _drill.dispose();
    super.dispose();
  }

  void _advance(int delta) {
    final n = widget.slices.length;
    if (n == 0) return;
    var ns = (_selected + delta) % n;
    if (ns < 0) ns += n;
    if (ns == _selected) return;
    HapticFeedback.selectionClick();
    setState(() => _selected = ns);
  }

  void _drillInto(int i) {
    HapticFeedback.mediumImpact();
    setState(() => _selected = i);
    _drill.forward();
  }

  void _back() {
    HapticFeedback.lightImpact();
    _drill.reverse();
  }

  void _onTapDown(Offset local) {
    if (_drill.value > 0.05) return; // taps in drilled view do nothing but Back
    final hits = [..._hits];
    for (final h in hits) {
      if (h.path.contains(local)) {
        _drillInto(h.index);
        return;
      }
    }
  }

  @override
  Widget build(BuildContext context) {
    final slices = widget.slices;
    if (slices.isEmpty) return const SizedBox(height: 8);

    return AnimatedBuilder(
      animation: _drill,
      builder: (context, _) {
        final d = Curves.easeInOutCubic
            .transform(widget.debugDrill ?? _drill.value);
        final sel = _selected.clamp(0, slices.length - 1);
        final h = _flatH + (_drillH - _flatH) * d;
        return SizedBox(
          height: h,
          child: GestureDetector(
            behavior: HitTestBehavior.opaque,
            onTapDown: (e) => _onTapDown(e.localPosition),
            onHorizontalDragUpdate: (e) {
              _dragAcc += e.delta.dx;
              const step = 26.0;
              while (_dragAcc.abs() >= step) {
                _advance(_dragAcc > 0 ? 1 : -1);
                _dragAcc -= _dragAcc > 0 ? step : -step;
              }
            },
            onHorizontalDragEnd: (_) => _dragAcc = 0,
            child: Stack(
              children: [
                Positioned.fill(
                  child: RepaintBoundary(
                    child: CustomPaint(
                      painter: _RingPainter(
                          slices: slices,
                          selected: sel,
                          drill: d,
                          hits: _hits),
                    ),
                  ),
                ),
                // Drill-in header: "Growth / SECTOR  X%"
                if (d > 0.05)
                  Positioned(
                    top: 6,
                    left: 0,
                    right: 0,
                    child: Opacity(
                      opacity: ((d - 0.2) / 0.8).clamp(0.0, 1.0),
                      child: Column(
                        children: [
                          Text('Allocation',
                              style: AppTextStyles.micro
                                  .copyWith(color: AppColors.textMuted)),
                          const SizedBox(height: 2),
                          RichText(
                            text: TextSpan(children: [
                              TextSpan(
                                  text: '${slices[sel].sector}  ',
                                  style: AppTextStyles.h3
                                      .copyWith(color: AppColors.textHi)),
                              TextSpan(
                                  text: '${slices[sel].pct.round()}%',
                                  style: AppTextStyles.h3
                                      .copyWith(color: AppColors.primary)),
                            ]),
                          ),
                        ],
                      ),
                    ),
                  ),
                // Drill-in "Back ^"
                if (d > 0.05)
                  Positioned(
                    bottom: 6,
                    left: 0,
                    right: 0,
                    child: Opacity(
                      opacity: ((d - 0.2) / 0.8).clamp(0.0, 1.0),
                      child: GestureDetector(
                        behavior: HitTestBehavior.opaque,
                        onTap: _back,
                        child: Column(
                          children: [
                            Text('Back',
                                style: AppTextStyles.micro
                                    .copyWith(color: AppColors.textMd)),
                            Icon(Icons.keyboard_arrow_up,
                                size: 18, color: AppColors.textMd),
                          ],
                        ),
                      ),
                    ),
                  ),
              ],
            ),
          ),
        );
      },
    );
  }
}

class _WedgeHit {
  _WedgeHit(this.index, this.path);
  final int index;
  final Path path;
}

class _RingPainter extends CustomPainter {
  _RingPainter({
    required this.slices,
    required this.selected,
    required this.drill,
    required this.hits,
  });
  final List<SectorSlice> slices;
  final int selected;
  final double drill; // 0 flat .. 1 drilled
  final List<_WedgeHit> hits;

  static const double _gap = 0.05;

  double _lerp(double a, double b) => a + (b - a) * drill;

  @override
  void paint(Canvas canvas, Size size) {
    hits.clear();
    final total = slices.fold<double>(0, (s, e) => s + e.pct);
    if (total <= 0) return;

    // Lerped geometry: flat ring → drilled (bigger, lower-tilt, taller walls,
    // centre pushed down so the raised focused block sits up top).
    final sy = _lerp(0.86, 0.42);
    final ro = size.width * _lerp(0.435, 0.62);
    final ri = size.width * _lerp(0.275, 0.40);
    final wall = _lerp(11, 66); // extrusion height
    final cx = size.width / 2;
    final cy = size.height * _lerp(0.5, 0.86);
    final c = Offset(cx, cy);

    // Rotate so the focused sector swings to the top (-pi/2) as we drill in.
    var fStart = -math.pi / 2;
    for (var i = 0; i < selected; i++) {
      fStart += slices[i].pct / total * 2 * math.pi;
    }
    final focusMid = fStart + slices[selected].pct / total * math.pi;
    var toTop = (-math.pi / 2) - focusMid;
    toTop = math.atan2(math.sin(toTop), math.cos(toTop)); // shortest
    final rot = toTop * drill;

    Offset onOval(double r, double ang, double yOff) =>
        Offset(c.dx + r * math.cos(ang), c.dy + r * sy * math.sin(ang) - yOff);

    Rect oval(double r, double yOff) =>
        Rect.fromCenter(center: c.translate(0, -yOff), width: 2 * r, height: 2 * r * sy);

    Path sectorPath(double a0, double a1, double yOff) {
      final outer = oval(ro, yOff);
      final inner = oval(ri, yOff);
      final p = Path();
      final o0 = onOval(ro, a0, yOff);
      p.moveTo(o0.dx, o0.dy);
      p.arcTo(outer, a0, a1 - a0, false);
      final i1 = onOval(ri, a1, yOff);
      p.lineTo(i1.dx, i1.dy);
      p.arcTo(inner, a1, -(a1 - a0), false);
      p.close();
      return p;
    }

    // Metallic gradient builders (lit from above).
    Shader topShade(Color hi, Color mid, Color lo, Rect r) => LinearGradient(
          begin: Alignment.topCenter,
          end: Alignment.bottomCenter,
          colors: [hi, mid, lo],
          stops: const [0.0, 0.5, 1.0],
        ).createShader(r);

    // Soft contact shadow.
    canvas.drawOval(
      Rect.fromCenter(
          center: c.translate(0, 12),
          width: 2 * ro * 0.96,
          height: 2 * ro * sy * 0.72),
      Paint()
        ..color = Colors.black.withValues(alpha: 0.5)
        ..maskFilter = const MaskFilter.blur(BlurStyle.normal, 16),
    );

    // Build sector angle ranges (rotated), with depth ordering back→front.
    final order = <int>[];
    for (var i = 0; i < slices.length; i++) {
      order.add(i);
    }
    double midOf(int i) {
      var s = -math.pi / 2 + rot;
      for (var j = 0; j < i; j++) {
        s += slices[j].pct / total * 2 * math.pi;
      }
      return s + slices[i].pct / total * math.pi;
    }
    // Front (sin near +1) drawn last.
    order.sort((a, b) => math.sin(midOf(a)).compareTo(math.sin(midOf(b))));

    final outerBounds = oval(ro, wall);
    for (final i in order) {
      var s = -math.pi / 2 + rot;
      for (var j = 0; j < i; j++) {
        s += slices[j].pct / total * 2 * math.pi;
      }
      final full = slices[i].pct / total * 2 * math.pi;
      final a0 = s + _gap / 2;
      final a1 = s + full - _gap / 2;
      if (a1 <= a0) continue;
      final sel = i == selected;
      // Focused block rises higher when drilled.
      final segWall = wall + (sel ? _lerp(0, 38) : 0);
      // Colour: selected is always violet; others are graphite when flat and
      // lerp to violet as we drill (all-violet drilled, focused brightest).
      final Color hiC, midC, loC;
      if (sel) {
        hiC = const Color(0xFFE7D6FF);
        midC = const Color(0xFFA374FF);
        loC = const Color(0xFF6A2DBE);
      } else {
        hiC = Color.lerp(
            const Color(0xFF8C8C9C), const Color(0xFFC4A8FF), drill)!;
        midC = Color.lerp(
            const Color(0xFF45454F), const Color(0xFF7E50E0), drill)!;
        loC = Color.lerp(
            const Color(0xFF1B1B22), const Color(0xFF45228C), drill)!;
      }

      // ── Outer wall (the 3D side facing the viewer) ──
      if (segWall > 1) {
        final wallPath = Path();
        final t0 = onOval(ro, a0, segWall);
        wallPath.moveTo(t0.dx, t0.dy);
        wallPath.arcTo(oval(ro, segWall), a0, a1 - a0, false);
        final b1 = onOval(ro, a1, 0);
        wallPath.lineTo(b1.dx, b1.dy);
        wallPath.arcTo(oval(ro, 0), a1, -(a1 - a0), false);
        wallPath.close();
        canvas.drawPath(
          wallPath,
          Paint()
            ..isAntiAlias = true
            ..shader = topShade(midC, loC,
                Color.lerp(loC, Colors.black, 0.45)!, wallPath.getBounds()),
        );
      }

      // ── Top face ──
      final top = sectorPath(a0, a1, segWall);
      canvas.drawPath(
        top,
        Paint()
          ..isAntiAlias = true
          ..shader = topShade(hiC, midC, loC, outerBounds),
      );
      // Bright top-edge highlight for the metallic read.
      canvas.drawPath(
        top,
        Paint()
          ..style = PaintingStyle.stroke
          ..strokeWidth = 1
          ..color = Colors.white.withValues(alpha: sel ? 0.22 : 0.10)
          ..isAntiAlias = true,
      );
      // Hit-test against the flat top face (drill==0) only.
      if (drill < 0.05) hits.add(_WedgeHit(i, top));
    }

    // ── Flat-only chrome: % labels, pointer, centre readout (fade with drill) ──
    final flatAlpha = (1 - drill * 2).clamp(0.0, 1.0);
    if (flatAlpha > 0.02) {
      var s = -math.pi / 2 + rot;
      final lro = ro + 14;
      for (var i = 0; i < slices.length; i++) {
        final full = slices[i].pct / total * 2 * math.pi;
        final mid = s + full / 2;
        s += full;
        final pos = onOval(lro, mid, wall);
        final tp = TextPainter(
          text: TextSpan(
            text: '${slices[i].pct.round()}%',
            style: TextStyle(
              color: (i == selected ? AppColors.primary : AppColors.textMuted)
                  .withValues(alpha: flatAlpha),
              fontSize: 11,
              fontWeight: FontWeight.w700,
            ),
          ),
          textDirection: TextDirection.ltr,
        )..layout();
        tp.paint(canvas, pos - Offset(tp.width / 2, tp.height / 2));
      }
      // Centre readout.
      final cs = slices[selected];
      final pctTp = TextPainter(
        text: TextSpan(
            text: '${cs.pct.round()}%',
            style: AppTextStyles.valueXl.copyWith(
                fontWeight: FontWeight.w800,
                color: AppColors.textHi.withValues(alpha: flatAlpha))),
        textDirection: TextDirection.ltr,
      )..layout();
      pctTp.paint(
          canvas, Offset(cx, cy - wall) - Offset(pctTp.width / 2, pctTp.height / 2 + 9));
      final nameTp = TextPainter(
        text: TextSpan(
            text: cs.sector,
            style: AppTextStyles.micro
                .copyWith(color: AppColors.textMuted.withValues(alpha: flatAlpha))),
        textDirection: TextDirection.ltr,
        maxLines: 1,
        ellipsis: '…',
      )..layout(maxWidth: ri * 1.7);
      nameTp.paint(canvas,
          Offset(cx, cy - wall) - Offset(nameTp.width / 2, nameTp.height / 2 - 13));
    }
  }

  @override
  bool shouldRepaint(_RingPainter old) =>
      old.selected != selected ||
      old.drill != drill ||
      !identical(old.slices, slices);
}
