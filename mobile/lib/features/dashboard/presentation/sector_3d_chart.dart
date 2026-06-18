import 'dart:math' as math;

import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import '../../../core/theme/app_colors.dart';
import '../../../core/theme/app_text_styles.dart';
import '../application/portfolio_analytics.dart';

/// A smooth, metallic, subtly-3D allocation ring (Robinhood-style): each sector
/// is a smooth elliptical band lit from above (a single global gradient gives
/// the brushed-metal sheen), with gaps, a raised front rim for depth, a soft
/// drop shadow, % labels, and a white selection pointer. Swipe to snap to the
/// next/previous sector (with a haptic tick); tap a sector to select it.
class Sector3DChart extends StatefulWidget {
  const Sector3DChart({super.key, required this.slices});
  final List<SectorSlice> slices;

  @override
  State<Sector3DChart> createState() => _Sector3DChartState();
}

class _Sector3DChartState extends State<Sector3DChart> {
  static const double _dim = 232;
  int _selected = 0;
  double _dragAcc = 0;
  final List<_WedgeHit> _hits = [];

  void _advance(int delta) {
    final n = widget.slices.length;
    if (n == 0) return;
    var ns = (_selected + delta) % n;
    if (ns < 0) ns += n;
    if (ns == _selected) return;
    HapticFeedback.selectionClick();
    setState(() => _selected = ns);
  }

  void _onTapDown(Offset local) {
    final hits = [..._hits];
    for (final h in hits) {
      if (h.path.contains(local) && h.index != _selected) {
        HapticFeedback.selectionClick();
        setState(() => _selected = h.index);
        return;
      }
    }
  }

  @override
  Widget build(BuildContext context) {
    final slices = widget.slices;
    if (slices.isEmpty) return const SizedBox(height: 8);
    final sel = _selected.clamp(0, slices.length - 1);

    return Center(
      child: SizedBox(
        width: _dim,
        height: _dim,
        child: GestureDetector(
          behavior: HitTestBehavior.opaque,
          onTapDown: (d) => _onTapDown(d.localPosition),
          onHorizontalDragUpdate: (d) {
            // Swipe a little → snap to the next/prev sector (+haptic). Right =
            // forward (clockwise), left = back.
            _dragAcc += d.delta.dx;
            const step = 26.0;
            while (_dragAcc.abs() >= step) {
              _advance(_dragAcc > 0 ? 1 : -1);
              _dragAcc -= _dragAcc > 0 ? step : -step;
            }
          },
          onHorizontalDragEnd: (_) => _dragAcc = 0,
          child: RepaintBoundary(
            child: CustomPaint(
              size: const Size(_dim, _dim),
              painter: _RingPainter(slices: slices, selected: sel, hits: _hits),
            ),
          ),
        ),
      ),
    );
  }
}

class _WedgeHit {
  _WedgeHit(this.index, this.path);
  final int index;
  final Path path;
}

class _RingPainter extends CustomPainter {
  _RingPainter(
      {required this.slices, required this.selected, required this.hits});
  final List<SectorSlice> slices;
  final int selected;
  final List<_WedgeHit> hits;

  static const double _sy = 0.86; // vertical squash → subtle tilt
  static const double _depth = 11; // raised-rim thickness
  static const double _gap = 0.05; // radians between sectors

  @override
  void paint(Canvas canvas, Size size) {
    hits.clear();
    final total = slices.fold<double>(0, (s, e) => s + e.pct);
    if (total <= 0) return;

    final c = size.center(Offset.zero);
    final ro = size.width * 0.435;
    final ri = size.width * 0.275;
    final outer = Rect.fromCenter(center: c, width: 2 * ro, height: 2 * ro * _sy);
    final inner = Rect.fromCenter(center: c, width: 2 * ri, height: 2 * ri * _sy);

    // Soft contact shadow under the ring.
    canvas.drawOval(
      Rect.fromCenter(
          center: c.translate(0, _depth + 10),
          width: 2 * ro * 0.96,
          height: 2 * ro * _sy * 0.7),
      Paint()
        ..color = Colors.black.withValues(alpha: 0.45)
        ..maskFilter = const MaskFilter.blur(BlurStyle.normal, 14),
    );

    // Global "lit from above" metallic gradients over the whole ring bounds, so
    // the top of the ring is bright and the bottom falls into shadow — the same
    // sheen across every sector reads as one brushed-metal surface.
    final graphite = LinearGradient(
      begin: Alignment.topCenter,
      end: Alignment.bottomCenter,
      colors: const [Color(0xFF7B7B8A), Color(0xFF42424E), Color(0xFF1B1B22)],
      stops: const [0.0, 0.5, 1.0],
    ).createShader(outer);
    final graphiteRim = LinearGradient(
      begin: Alignment.topCenter,
      end: Alignment.bottomCenter,
      colors: const [Color(0xFF34343E), Color(0xFF0E0E14)],
    ).createShader(outer);
    final violet = LinearGradient(
      begin: Alignment.topCenter,
      end: Alignment.bottomCenter,
      colors: const [Color(0xFFD8C2FF), Color(0xFF8B5CF6), Color(0xFF4C1D95)],
      stops: const [0.0, 0.5, 1.0],
    ).createShader(outer);
    // ── Raised front rim (3D depth): the bottom half of the ring's outer edge,
    // extruded downward. Drawn first; the top faces sit on top of it. ──
    final rimTop = Path()..addArc(outer, 0, math.pi); // bottom semicircle (0→π)
    final rimPath = Path()
      ..addArc(outer, 0, math.pi)
      ..lineTo(c.dx - ro, c.dy + _depth) // down at the left end
      ..addArc(outer.shift(const Offset(0, _depth)), math.pi, -math.pi)
      ..close();
    // (rimTop kept implicit; rimPath is the filled wall band.)
    canvas.drawPath(
        rimPath, Paint()..shader = graphiteRim..isAntiAlias = true);
    rimTop.reset();

    // ── Top faces: smooth elliptical annular sectors, gapped, lit gradient. ──
    var start = -math.pi / 2;
    for (var i = 0; i < slices.length; i++) {
      final full = slices[i].pct / total * 2 * math.pi;
      final a0 = start + _gap / 2;
      final a1 = start + full - _gap / 2;
      start += full;
      if (a1 <= a0) continue;
      final sel = i == selected;
      final path = _sector(outer, inner, a0, a1);
      canvas.drawPath(
        path,
        Paint()
          ..shader = sel ? violet : graphite
          ..isAntiAlias = true,
      );
      // Crisp edge highlight so sectors read as separate metal pieces.
      canvas.drawPath(
        path,
        Paint()
          ..style = PaintingStyle.stroke
          ..strokeWidth = 1
          ..color = Colors.white.withValues(alpha: sel ? 0.16 : 0.07)
          ..isAntiAlias = true,
      );
      hits.add(_WedgeHit(i, path));
    }

    // ── % labels just outside each sector. ──
    start = -math.pi / 2;
    final lro = ro + 14;
    for (var i = 0; i < slices.length; i++) {
      final full = slices[i].pct / total * 2 * math.pi;
      final mid = start + full / 2;
      start += full;
      final pos = Offset(
          c.dx + math.cos(mid) * lro, c.dy + math.sin(mid) * lro * _sy);
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
    }

    // ── White selection pointer on the inner edge of the selected sector. ──
    _drawPointer(canvas, c, ri, total);

    // ── Centre readout. ──
    final s = slices[selected.clamp(0, slices.length - 1)];
    final pctTp = TextPainter(
      text: TextSpan(
          text: '${s.pct.round()}%',
          style: AppTextStyles.valueXl.copyWith(fontWeight: FontWeight.w800)),
      textDirection: TextDirection.ltr,
    )..layout();
    pctTp.paint(canvas, c - Offset(pctTp.width / 2, pctTp.height / 2 + 9));
    final nameTp = TextPainter(
      text: TextSpan(
          text: s.sector,
          style: AppTextStyles.micro.copyWith(color: AppColors.textMuted)),
      textDirection: TextDirection.ltr,
      maxLines: 1,
      ellipsis: '…',
    )..layout(maxWidth: ri * 1.7);
    nameTp.paint(canvas, c - Offset(nameTp.width / 2, nameTp.height / 2 - 13));
  }

  void _drawPointer(Canvas canvas, Offset c, double ri, double total) {
    var start = -math.pi / 2;
    for (var i = 0; i < selected; i++) {
      start += slices[i].pct / total * 2 * math.pi;
    }
    final mid = start + slices[selected].pct / total * math.pi;
    final px = c.dx + math.cos(mid) * (ri - 6);
    final py = c.dy + math.sin(mid) * (ri - 6) * _sy;
    final tri = Path()
      ..moveTo(px, py)
      ..lineTo(px - 5 * math.sin(mid), py + 5 * math.cos(mid) - 4)
      ..lineTo(px + 5 * math.sin(mid), py - 5 * math.cos(mid) - 4)
      ..close();
    canvas.drawPath(tri, Paint()..color = Colors.white.withValues(alpha: 0.92));
  }

  /// Smooth elliptical annular sector path (gaps handled by the caller's a0/a1).
  Path _sector(Rect outer, Rect inner, double a0, double a1) {
    final c = outer.center;
    final p = Path();
    final o0 = Offset(c.dx + outer.width / 2 * math.cos(a0),
        c.dy + outer.height / 2 * math.sin(a0));
    p.moveTo(o0.dx, o0.dy);
    p.arcTo(outer, a0, a1 - a0, false);
    final i1 = Offset(c.dx + inner.width / 2 * math.cos(a1),
        c.dy + inner.height / 2 * math.sin(a1));
    p.lineTo(i1.dx, i1.dy);
    p.arcTo(inner, a1, -(a1 - a0), false);
    p.close();
    return p;
  }

  @override
  bool shouldRepaint(_RingPainter old) =>
      old.selected != selected || !identical(old.slices, slices);
}
