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
    with TickerProviderStateMixin {
  static const double _flatH = 280;
  static const double _drillH = 320;

  int _selected = 0;
  double _dragAcc = 0;
  final List<_WedgeHit> _hits = [];

  late final AnimationController _drill = AnimationController(
    vsync: this,
    duration: const Duration(milliseconds: 620),
  );

  // Smooth ring rotation for drilled focus changes: when you swipe to another
  // sector while drilled, the ring rotates the new sector up to the top rather
  // than snapping. _rotNow lerps _rotFrom→_rotTo over _rotCtrl.
  late final AnimationController _rotCtrl = AnimationController(
    vsync: this,
    duration: const Duration(milliseconds: 380),
  );
  double _rotFrom = 0;
  double _rotTo = 0;

  // Continuous finger-tracking rotation while drilled: during a drag the ring
  // follows the finger (_rot), and on release it eases to the nearest sector.
  bool _dragging = false;
  double _rot = 0;
  static const double _rotPerPx = 0.009; // drag sensitivity (rad per px)

  double get _rotNow {
    if (_dragging) return _rot;
    final t = Curves.easeInOut.transform(_rotCtrl.value);
    return _rotFrom + (_rotTo - _rotFrom) * t;
  }

  /// Sector whose mid-angle is currently nearest the top, given rotation [rot].
  int _nearestTop(double rot) {
    var best = 0;
    var bestD = double.infinity;
    for (var i = 0; i < widget.slices.length; i++) {
      var d = (_natMid(i) + rot) % (2 * math.pi);
      if (d > math.pi) d -= 2 * math.pi;
      if (d < -math.pi) d += 2 * math.pi;
      if (d.abs() < bestD) {
        bestD = d.abs();
        best = i;
      }
    }
    return best;
  }

  /// Un-rotated mid-angle offset of sector [i] from the ring's start (-π/2).
  double _natMid(int i) {
    final total = widget.slices.fold<double>(0, (s, e) => s + e.pct);
    if (total <= 0) return 0;
    double off = 0;
    for (var j = 0; j < i; j++) {
      off += widget.slices[j].pct / total * 2 * math.pi;
    }
    return off + widget.slices[i].pct / total * math.pi;
  }

  /// Rotation that brings sector [i] to the top.
  double _targetRot(int i) => -_natMid(i);

  /// Pick the equivalent angle to [target] nearest [from] (shortest spin).
  double _shortestTo(double target, double from) {
    var t = target;
    while (t - from > math.pi) {
      t -= 2 * math.pi;
    }
    while (t - from < -math.pi) {
      t += 2 * math.pi;
    }
    return t;
  }

  @override
  void dispose() {
    _drill.dispose();
    _rotCtrl.dispose();
    super.dispose();
  }

  // Flat view: a drag just moves the highlighted sector (the flat ring doesn't
  // rotate). Drilled rotation is handled continuously in the drag callbacks.
  void _advanceFlat(int delta) {
    final n = widget.slices.length;
    if (n == 0) return;
    var ns = (_selected + delta) % n;
    if (ns < 0) ns += n;
    if (ns == _selected) return;
    HapticFeedback.selectionClick();
    setState(() => _selected = ns);
  }

  // Drilled drag: rotate the ring with the finger, live-selecting whatever
  // sector is nearest the top.
  void _dragRotate(double dx) {
    setState(() {
      _rot += dx * _rotPerPx;
      final ns = _nearestTop(_rot);
      if (ns != _selected) {
        _selected = ns;
        HapticFeedback.selectionClick();
      }
    });
  }

  // Drilled release: ease the nearest sector exactly to the top.
  void _settleRotation() {
    final target = _shortestTo(_targetRot(_selected), _rot);
    _dragging = false;
    setState(() {
      _rotFrom = _rot;
      _rotTo = target;
      _rot = target;
    });
    _rotCtrl.forward(from: 0);
  }

  void _drillInto(int i) {
    HapticFeedback.mediumImpact();
    final target = _targetRot(i);
    setState(() {
      _selected = i;
      _rotFrom = target;
      _rotTo = target;
      _rot = target;
    });
    // Settle the rotation controller so _rotNow == target; the drill controller
    // provides the easing as ringRot*drill sweeps the sector up while extruding.
    _rotCtrl.value = 1;
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
      animation: Listenable.merge([_drill, _rotCtrl]),
      builder: (context, _) {
        final d = Curves.easeInOutCubic.transform(
          widget.debugDrill ?? _drill.value,
        );
        final sel = _selected.clamp(0, slices.length - 1);
        final h = _flatH + (_drillH - _flatH) * d;
        return SizedBox(
          height: h,
          child: GestureDetector(
            behavior: HitTestBehavior.opaque,
            onTapDown: (e) => _onTapDown(e.localPosition),
            onHorizontalDragStart: (_) {
              _dragAcc = 0;
              if (_drill.value > 0.5) {
                _rot = _rotNow; // grab the current displayed rotation
                _dragging = true;
              }
            },
            onHorizontalDragUpdate: (e) {
              if (_dragging) {
                _dragRotate(e.delta.dx); // drilled: follow the finger
              } else {
                // Flat: gentle discrete highlight step (not snappy).
                _dragAcc += e.delta.dx;
                const step = 44.0;
                while (_dragAcc.abs() >= step) {
                  _advanceFlat(_dragAcc > 0 ? 1 : -1);
                  _dragAcc -= _dragAcc > 0 ? step : -step;
                }
              }
            },
            onHorizontalDragEnd: (_) {
              if (_dragging) {
                _settleRotation(); // ease nearest sector to the top
              }
              _dragAcc = 0;
            },
            child: Stack(
              children: [
                Positioned.fill(
                  child: RepaintBoundary(
                    child: CustomPaint(
                      painter: _RingPainter(
                        slices: slices,
                        selected: sel,
                        drill: d,
                        ringRot: _rotNow,
                        hits: _hits,
                      ),
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
                          Text(
                            'Allocation',
                            style: AppTextStyles.micro.copyWith(
                              color: AppColors.textMuted,
                            ),
                          ),
                          const SizedBox(height: 2),
                          RichText(
                            text: TextSpan(
                              children: [
                                TextSpan(
                                  text: '${slices[sel].sector}  ',
                                  style: AppTextStyles.h3.copyWith(
                                    color: AppColors.textHi,
                                  ),
                                ),
                                TextSpan(
                                  text: '${slices[sel].pct.round()}%',
                                  style: AppTextStyles.h3.copyWith(
                                    color: AppColors.primary,
                                  ),
                                ),
                              ],
                            ),
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
                            Text(
                              'Back',
                              style: AppTextStyles.micro.copyWith(
                                color: AppColors.textMd,
                              ),
                            ),
                            Icon(
                              Icons.keyboard_arrow_up,
                              size: 18,
                              color: AppColors.textMd,
                            ),
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
    required this.ringRot,
    required this.hits,
  });
  final List<SectorSlice> slices;
  final int selected;
  final double drill; // 0 flat .. 1 drilled
  final double ringRot; // animated ring rotation (radians)
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
    final sy = _lerp(1.0, 0.42); // flat = round top-down → tilted when drilled
    final ro = size.width * _lerp(0.33, 0.58);
    final ri = size.width * _lerp(0.205, 0.33);
    final wall = _lerp(
      0,
      64,
    ); // no extrusion flat → tall metallic blocks drilled
    final cx = size.width / 2;
    final cy = size.height * _lerp(0.5, 0.86);
    final c = Offset(cx, cy);

    // Ring rotation comes from the State (animated for smooth drilled focus
    // changes); only applied as we drill in.
    final rot = ringRot * drill;

    Offset onOval(double r, double ang, double yOff) =>
        Offset(c.dx + r * math.cos(ang), c.dy + r * sy * math.sin(ang) - yOff);

    Rect oval(double r, double yOff) => Rect.fromCenter(
      center: c.translate(0, -yOff),
      width: 2 * r,
      height: 2 * r * sy,
    );

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

    // Soft contact shadow (subtle when flat, deeper as the blocks rise).
    canvas.drawOval(
      Rect.fromCenter(
        center: c.translate(0, _lerp(4, 14)),
        width: 2 * ro * 0.96,
        height: 2 * ro * sy * 0.72,
      ),
      Paint()
        ..color = Colors.black.withValues(alpha: _lerp(0.28, 0.5))
        ..maskFilter = const MaskFilter.blur(BlurStyle.normal, 16),
    );

    // A single ~100% slice can't render as a ring — the hole/gap/inner-wall
    // heuristics assume several discrete wedges, so it came out as a hollow
    // shell floating over a detached floor. Draw it as a SOLID 3D disc instead.
    var dom = 0;
    for (var k = 1; k < slices.length; k++) {
      if (slices[k].pct > slices[dom].pct) dom = k;
    }
    final fullDisc = slices[dom].pct / total >= 0.995;
    if (fullDisc) {
      const b = 1.0;
      final hiC = Color.lerp(
        const Color(0xFF7C5CE6),
        const Color(0xFFEBE0FF),
        b,
      )!;
      final midC = Color.lerp(
        const Color(0xFF4A2C9E),
        const Color(0xFFB79BFF),
        b,
      )!;
      final loC = Color.lerp(
        const Color(0xFF24114F),
        const Color(0xFF7E55E6),
        b,
      )!;
      final discBounds = oval(ro, wall);
      // Cylinder side wall (front half, θ∈[0,π]) between top and bottom rims.
      if (wall > 1) {
        final w = Path();
        final t0 = onOval(ro, 0, wall);
        w.moveTo(t0.dx, t0.dy);
        w.arcTo(oval(ro, wall), 0, math.pi, false);
        final bot = onOval(ro, math.pi, 0);
        w.lineTo(bot.dx, bot.dy);
        w.arcTo(oval(ro, 0), math.pi, -math.pi, false);
        w.close();
        canvas.drawPath(
          w,
          Paint()
            ..isAntiAlias = true
            ..shader = topShade(
              midC,
              loC,
              Color.lerp(loC, Colors.black, 0.45)!,
              w.getBounds(),
            ),
        );
      }
      // Solid top disc (fills the hole → not hollow) with the metallic gradient.
      final disc = Path()..addOval(oval(ro, wall));
      canvas.drawPath(
        disc,
        Paint()
          ..isAntiAlias = true
          ..shader = LinearGradient(
            begin: Alignment.topCenter,
            end: Alignment.bottomCenter,
            colors: [Color.lerp(hiC, Colors.white, 0.24)!, hiC, midC, loC],
            stops: const [0.0, 0.20, 0.58, 1.0],
          ).createShader(discBounds),
      );
      canvas.drawPath(
        disc,
        Paint()
          ..isAntiAlias = true
          ..blendMode = BlendMode.plus
          ..shader = SweepGradient(
            startAngle: -math.pi / 2,
            endAngle: 3 * math.pi / 2,
            colors: [
              Colors.transparent,
              Colors.white.withValues(alpha: 0.20),
              Colors.transparent,
              Colors.white.withValues(alpha: 0.05),
              Colors.transparent,
              Colors.white.withValues(alpha: 0.13),
              Colors.transparent,
            ],
            stops: const [0.0, 0.10, 0.26, 0.5, 0.74, 0.90, 1.0],
          ).createShader(Rect.fromCircle(center: c, radius: ro)),
      );
      canvas.drawPath(
        disc,
        Paint()
          ..style = PaintingStyle.stroke
          ..strokeWidth = 1
          ..color = Colors.white.withValues(alpha: 0.18)
          ..isAntiAlias = true,
      );
      if (drill < 0.05) hits.add(_WedgeHit(dom, disc));
    }

    // Recessed floor inside the hole so you don't see through to the dark card
    // — this is what kills the "hollow shell" look. Drilled only (flat shows a
    // centre readout over the hole instead).
    if (!fullDisc && drill > 0.02) {
      final fa = drill.clamp(0.0, 1.0);
      final floor = oval(ri * 0.985, 2);
      canvas.drawOval(
        floor,
        Paint()
          ..isAntiAlias = true
          ..shader = RadialGradient(
            colors: [
              const Color(0xFF1A0C36).withValues(alpha: fa),
              const Color(0xFF0A0416).withValues(alpha: fa),
            ],
            stops: const [0.0, 1.0],
          ).createShader(floor),
      );
    }

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
    for (final i in (fullDisc ? const <int>[] : order)) {
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
      // Dashboard-violet metallic palette (keyed to AppColors.primary). Every
      // wedge is violet; brightness varies by size so neighbours read apart,
      // and the selected wedge is brightest. b: 0 (deep) .. 1 (bright).
      final n = slices.length;
      final double b = sel
          ? 1.0
          : (n <= 1 ? 0.58 : 0.30 + 0.42 * (1 - i / (n - 1)));
      final hiC = Color.lerp(
        const Color(0xFF7C5CE6),
        const Color(0xFFEBE0FF),
        b,
      )!;
      final midC = Color.lerp(
        const Color(0xFF4A2C9E),
        const Color(0xFFB79BFF),
        b,
      )!;
      final loC = Color.lerp(
        const Color(0xFF24114F),
        const Color(0xFF7E55E6),
        b,
      )!;

      if (segWall > 1) {
        // ── Inner wall (the hole side) ── only on back-leaning wedges, where
        // it's visible across the hole; a front wedge's inner wall is self-
        // occluded and would bleed into the hole, so skip it. This is what
        // makes the ring read as a SOLID band instead of a hollow shell.
        final midAng = (a0 + a1) / 2;
        if (math.sin(midAng) < 0.35) {
          final inner = Path();
          final it0 = onOval(ri, a0, segWall);
          inner.moveTo(it0.dx, it0.dy);
          inner.arcTo(oval(ri, segWall), a0, a1 - a0, false);
          final ib1 = onOval(ri, a1, 0);
          inner.lineTo(ib1.dx, ib1.dy);
          inner.arcTo(oval(ri, 0), a1, -(a1 - a0), false);
          inner.close();
          canvas.drawPath(
            inner,
            Paint()
              ..isAntiAlias = true
              ..shader = topShade(
                Color.lerp(midC, Colors.black, 0.30)!,
                Color.lerp(loC, Colors.black, 0.26)!,
                Color.lerp(loC, Colors.black, 0.55)!,
                inner.getBounds(),
              ),
          );
        }

        // ── Side caps (radial cut faces at each end) ── these close the block
        // so the gaps don't show an open "cut" / the wall fully drops down the
        // sides. Drawn before the outer wall + top so those sit on top.
        Path capPath(double a) {
          final to = onOval(ro, a, segWall); // top outer
          final ti = onOval(ri, a, segWall); // top inner
          final bi = onOval(ri, a, 0); // bottom inner
          final bo = onOval(ro, a, 0); // bottom outer
          return Path()
            ..moveTo(to.dx, to.dy)
            ..lineTo(ti.dx, ti.dy)
            ..lineTo(bi.dx, bi.dy)
            ..lineTo(bo.dx, bo.dy)
            ..close();
        }

        for (final a in [a0, a1]) {
          final cap = capPath(a);
          canvas.drawPath(
            cap,
            Paint()
              ..isAntiAlias = true
              ..shader = topShade(
                Color.lerp(midC, Colors.black, 0.28)!,
                Color.lerp(loC, Colors.black, 0.32)!,
                Color.lerp(loC, Colors.black, 0.55)!,
                cap.getBounds(),
              ),
          );
        }

        // ── Outer wall (the 3D side facing the viewer) ──
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
            ..shader = topShade(
              midC,
              loC,
              Color.lerp(loC, Colors.black, 0.45)!,
              wallPath.getBounds(),
            ),
        );
      }

      // ── Top face ── metallic 4-stop gradient: a bright specular sheen band
      // near the top edge then violet→deep, for a brushed-metal read.
      final top = sectorPath(a0, a1, segWall);
      canvas.drawPath(
        top,
        Paint()
          ..isAntiAlias = true
          ..shader = LinearGradient(
            begin: Alignment.topCenter,
            end: Alignment.bottomCenter,
            colors: [Color.lerp(hiC, Colors.white, 0.24)!, hiC, midC, loC],
            stops: const [0.0, 0.20, 0.58, 1.0],
          ).createShader(outerBounds),
      );
      // Metallic angular reflection: additive highlight bands fixed in screen
      // space (not rotated with the ring), so glints sweep across the metal as
      // it turns — the way curved brushed metal reflects a fixed environment.
      canvas.drawPath(
        top,
        Paint()
          ..isAntiAlias = true
          ..blendMode = BlendMode.plus
          ..shader = SweepGradient(
            startAngle: -math.pi / 2,
            endAngle: 3 * math.pi / 2,
            colors: [
              Colors.transparent,
              Colors.white.withValues(alpha: sel ? 0.22 : 0.14),
              Colors.transparent,
              Colors.white.withValues(alpha: 0.05),
              Colors.transparent,
              Colors.white.withValues(alpha: sel ? 0.15 : 0.10),
              Colors.transparent,
            ],
            stops: const [0.0, 0.10, 0.26, 0.5, 0.74, 0.90, 1.0],
          ).createShader(Rect.fromCircle(center: c, radius: ro)),
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
            color: AppColors.textHi.withValues(alpha: flatAlpha),
          ),
        ),
        textDirection: TextDirection.ltr,
      )..layout();
      pctTp.paint(
        canvas,
        Offset(cx, cy - wall) - Offset(pctTp.width / 2, pctTp.height / 2 + 9),
      );
      final nameTp = TextPainter(
        text: TextSpan(
          text: cs.sector,
          style: AppTextStyles.micro.copyWith(
            color: AppColors.textMuted.withValues(alpha: flatAlpha),
          ),
        ),
        textDirection: TextDirection.ltr,
        maxLines: 1,
        ellipsis: '…',
      )..layout(maxWidth: ri * 1.7);
      nameTp.paint(
        canvas,
        Offset(cx, cy - wall) -
            Offset(nameTp.width / 2, nameTp.height / 2 - 13),
      );
    }
  }

  @override
  bool shouldRepaint(_RingPainter old) =>
      old.selected != selected ||
      old.drill != drill ||
      old.ringRot != ringRot ||
      !identical(old.slices, slices);
}
