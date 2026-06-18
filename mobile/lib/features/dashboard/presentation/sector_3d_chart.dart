import 'dart:math' as math;

import 'package:flutter/material.dart';
import '../../../core/theme/app_colors.dart';
import '../../../core/theme/app_text_styles.dart';
import '../application/portfolio_analytics.dart';

/// A genuinely 3D, metallic, exploded ring chart drawn from scratch in
/// CustomPaint — extruded annular wedges with separated slices, projected
/// through a real 3D rotation + perspective, depth-sorted (painter's
/// algorithm), and lit with diffuse + Blinn-Phong specular so the metal sheen
/// slides across the surfaces as it rotates. Flat↔3D toggle; drag to spin;
/// tap a wedge to select it (hit-tested against the projected top faces).
class Sector3DChart extends StatefulWidget {
  const Sector3DChart({super.key, required this.slices});
  final List<SectorSlice> slices;

  @override
  State<Sector3DChart> createState() => _Sector3DChartState();
}

class _Sector3DChartState extends State<Sector3DChart>
    with TickerProviderStateMixin {
  static const double _dim = 232;
  static const double _maxTilt = 1.02; // radians (~58°) when fully 3D

  late final AnimationController _entry = AnimationController(
    vsync: this,
    duration: const Duration(milliseconds: 900),
  )..forward();
  late final AnimationController _tiltCtrl = AnimationController(
    vsync: this,
    duration: const Duration(milliseconds: 420),
    value: 1, // start in 3D
  );

  bool _is3d = true;
  double _spin = 0;
  int _selected = 0;

  // Populated by the painter each frame: per-wedge projected top-face path +
  // depth, for exact tap hit-testing.
  final List<_WedgeHit> _hits = [];

  @override
  void initState() {
    super.initState();
    _selected = _frontIndex();
  }

  @override
  void dispose() {
    _entry.dispose();
    _tiltCtrl.dispose();
    super.dispose();
  }

  void _set3d(bool v) {
    if (v == _is3d) return;
    setState(() {
      _is3d = v;
      if (v) _selected = _frontIndex();
    });
    v ? _tiltCtrl.forward() : _tiltCtrl.reverse();
  }

  /// Wedge currently rotated to the front (bottom, toward the viewer).
  int _frontIndex() {
    final total = widget.slices.fold<double>(0, (s, e) => s + e.pct);
    if (total <= 0) return 0;
    var start = -math.pi / 2;
    var best = 0;
    var bestDelta = double.infinity;
    for (var i = 0; i < widget.slices.length; i++) {
      final mid = start + widget.slices[i].pct / total * math.pi;
      var d = (mid + _spin - math.pi / 2) % (2 * math.pi);
      if (d < 0) d += 2 * math.pi;
      final delta = math.min(d, 2 * math.pi - d);
      if (delta < bestDelta) {
        bestDelta = delta;
        best = i;
      }
      start += widget.slices[i].pct / total * 2 * math.pi;
    }
    return best;
  }

  void _onTapDown(Offset local) {
    // Front-to-back: the visible (front) wedges have the greatest depth.
    final hits = [..._hits]..sort((a, b) => b.depth.compareTo(a.depth));
    for (final h in hits) {
      if (h.top.contains(local)) {
        setState(() => _selected = h.index);
        return;
      }
    }
  }

  @override
  Widget build(BuildContext context) {
    final slices = widget.slices;
    if (slices.isEmpty) return const SizedBox(height: 8); // guard clamp(0,-1)
    final sel = _selected.clamp(0, slices.length - 1);

    return Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: [
        Align(alignment: Alignment.centerRight, child: _toggle()),
        const SizedBox(height: 4),
        Center(
          child: SizedBox(
            width: _dim,
            height: _dim,
            child: GestureDetector(
              behavior: HitTestBehavior.opaque,
              onTapDown: (d) => _onTapDown(d.localPosition),
              onHorizontalDragUpdate: (d) {
                if (!_is3d) return;
                setState(() {
                  _spin += d.delta.dx * 0.012;
                  _selected = _frontIndex();
                });
              },
              child: RepaintBoundary(
                child: AnimatedBuilder(
                  animation: Listenable.merge([_entry, _tiltCtrl]),
                  builder: (_, _) {
                    final entry = Curves.easeOutCubic.transform(_entry.value);
                    final tilt =
                        Curves.easeInOut.transform(_tiltCtrl.value) * _maxTilt;
                    return CustomPaint(
                      size: const Size(_dim, _dim),
                      painter: _Sector3DPainter(
                        slices: slices,
                        selected: sel,
                        spin: _spin,
                        tilt: tilt,
                        entry: entry,
                        is3d: _is3d,
                        hits: _hits,
                      ),
                    );
                  },
                ),
              ),
            ),
          ),
        ),
        if (_is3d)
          Padding(
            padding: const EdgeInsets.only(top: 2),
            child: Text('Drag to rotate',
                textAlign: TextAlign.center,
                style: AppTextStyles.nano.copyWith(color: AppColors.textFaint)),
          ),
      ],
    );
  }

  Widget _toggle() => Container(
        padding: const EdgeInsets.all(3),
        decoration: BoxDecoration(
          color: Colors.white.withValues(alpha: 0.06),
          borderRadius: BorderRadius.circular(9),
        ),
        child: Row(
          mainAxisSize: MainAxisSize.min,
          children: [
            _toggleBtn(Icons.pie_chart_outline_rounded, !_is3d,
                () => _set3d(false)),
            _toggleBtn(Icons.threed_rotation, _is3d, () => _set3d(true)),
          ],
        ),
      );

  Widget _toggleBtn(IconData icon, bool active, VoidCallback onTap) =>
      GestureDetector(
        behavior: HitTestBehavior.opaque,
        onTap: onTap,
        child: AnimatedContainer(
          duration: const Duration(milliseconds: 160),
          padding: const EdgeInsets.symmetric(horizontal: 11, vertical: 5),
          decoration: BoxDecoration(
            color:
                active ? AppColors.fill(AppColors.primary) : Colors.transparent,
            borderRadius: BorderRadius.circular(7),
          ),
          child: Icon(icon,
              size: 16, color: active ? AppColors.primary : AppColors.textDim),
        ),
      );
}

// ── 3D math ──────────────────────────────────────────────────────────────────

class _V3 {
  const _V3(this.x, this.y, this.z);
  final double x, y, z;
}

/// Rotate around Z (spin) then X (tilt) — view-space orientation.
_V3 _rot(_V3 p, double cs, double sn, double ct, double st) {
  final x1 = p.x * cs - p.y * sn;
  final y1 = p.x * sn + p.y * cs;
  final z1 = p.z;
  final y2 = y1 * ct - z1 * st;
  final z2 = y1 * st + z1 * ct;
  return _V3(x1, y2, z2);
}

class _Face {
  _Face(this.pts, this.color, this.depth);
  final List<Offset> pts;
  final Color color;
  final double depth;
}

class _WedgeHit {
  _WedgeHit(this.index, this.top, this.depth);
  final int index;
  final Path top;
  final double depth;
}

class _Sector3DPainter extends CustomPainter {
  _Sector3DPainter({
    required this.slices,
    required this.selected,
    required this.spin,
    required this.tilt,
    required this.entry,
    required this.is3d,
    required this.hits,
  });

  final List<SectorSlice> slices;
  final int selected;
  final double spin;
  final double tilt;
  final double entry; // 0..1 grow-in
  final bool is3d;
  final List<_WedgeHit> hits;

  // Directional light (view space): from the camera side, angled to match the
  // +X tilt so the (most-visible) top faces are well lit — light dir aligns
  // with the tilted top normal (0, -sin tilt, cos tilt) plus a left bias.
  static const _lx = -0.35, _ly = -0.42, _lz = 0.84;

  @override
  void paint(Canvas canvas, Size size) {
    hits.clear();
    final total = slices.fold<double>(0, (s, e) => s + e.pct);
    if (total <= 0) return;

    final center = size.center(Offset.zero);
    final ro = size.width * 0.43; // outer radius
    final ri = size.width * 0.255; // inner (hole) radius
    final height = (is3d ? 30.0 : 3.0) * entry; // extrusion depth
    const explodeBase = 5.0;
    const explodeSel = 15.0;
    const cam = 1400.0;
    const k = 7; // arc samples per wedge

    final cs = math.cos(spin), sn = math.sin(spin);
    final ct = math.cos(tilt), st = math.sin(tilt);

    Offset project(_V3 v) {
      final f = cam / (cam - v.z);
      return Offset(center.dx + v.x * f, center.dy - v.y * f);
    }

    // Normalize + light a face given its LOCAL normal.
    double shadeFor(_V3 nLocal) {
      final n = _rot(nLocal, cs, sn, ct, st);
      final len = math.sqrt(n.x * n.x + n.y * n.y + n.z * n.z);
      if (len == 0) return 0.3;
      final nx = n.x / len, ny = n.y / len, nz = n.z / len;
      final diff = math.max(0.0, nx * _lx + ny * _ly + nz * _lz);
      return diff;
    }

    double specFor(_V3 nLocal) {
      final n = _rot(nLocal, cs, sn, ct, st);
      final len = math.sqrt(n.x * n.x + n.y * n.y + n.z * n.z);
      if (len == 0) return 0;
      final nx = n.x / len, ny = n.y / len, nz = n.z / len;
      // halfway between light and view(0,0,1)
      const hx = _lx, hy = _ly, hz = _lz + 1.0;
      final hl = math.sqrt(hx * hx + hy * hy + hz * hz);
      final d = math.max(0.0, (nx * hx + ny * hy + nz * hz) / hl);
      return math.pow(d, 38).toDouble(); // sharp, small highlight
    }

    Color metal(bool sel, _V3 nLocal) {
      // Cool-metal base, then ambient+diffuse with a controlled specular streak
      // (kept low so faces never blow out to white).
      final br = sel ? 0.52 : 0.30;
      final bg = sel ? 0.30 : 0.30;
      final bb = sel ? 0.74 : 0.37;
      final diff = shadeFor(nLocal);
      final spec = specFor(nLocal) * 0.22; // restrained so faces never go white
      double ch(double base) =>
          (base * (0.42 + 0.52 * diff) + spec).clamp(0.0, 1.0);
      return Color.fromARGB(255, (ch(br) * 255).round(), (ch(bg) * 255).round(),
          (ch(bb) * 255).round());
    }

    final faces = <_Face>[];
    var start = -math.pi / 2;
    for (var i = 0; i < slices.length; i++) {
      final full = slices[i].pct / total * 2 * math.pi;
      const gapAng = 0.055;
      final a0 = start + gapAng / 2;
      final a1 = start + full - gapAng / 2;
      start += full;
      if (a1 <= a0) continue;
      final sel = i == selected;
      final mid = (a0 + a1) / 2;
      final expl = (sel ? explodeSel : explodeBase) * (is3d ? 1.0 : 0.6) * entry;
      final ex = math.cos(mid) * expl, ey = math.sin(mid) * expl;

      // Sample angles.
      final angs = [for (var j = 0; j <= k; j++) a0 + (a1 - a0) * j / k];

      _V3 vtx(double r, double ang, double z) =>
          _V3(r * math.cos(ang) + ex, r * math.sin(ang) + ey, z);

      double depthOf(List<_V3> vs) {
        var d = 0.0;
        for (final v in vs) {
          d += _rot(v, cs, sn, ct, st).z;
        }
        return d / vs.length;
      }

      List<Offset> proj(List<_V3> vs) =>
          [for (final v in vs) project(_rot(v, cs, sn, ct, st))];

      // Top face (annular sector at z = height).
      final topLocal = <_V3>[
        for (final a in angs) vtx(ro, a, height),
        for (final a in angs.reversed) vtx(ri, a, height),
      ];
      final topPts = proj(topLocal);
      final topDepth = depthOf(topLocal);
      faces.add(_Face(topPts, metal(sel, const _V3(0, 0, 1)), topDepth));
      // Record for hit-testing.
      final topPath = Path()..addPolygon(topPts, true);
      hits.add(_WedgeHit(i, topPath, topDepth));

      if (height > 0.5) {
        // Outer wall quads.
        for (var j = 0; j < k; j++) {
          final aA = angs[j], aB = angs[j + 1];
          final quad = <_V3>[
            vtx(ro, aA, height),
            vtx(ro, aB, height),
            vtx(ro, aB, 0),
            vtx(ro, aA, 0),
          ];
          final am = (aA + aB) / 2;
          final n = _V3(math.cos(am), math.sin(am), 0);
          faces.add(
              _Face(proj(quad), metal(sel, n), depthOf(quad)));
        }
        // (Inner wall intentionally omitted — Robinhood-style hollow hole; it
        // also avoided a bright-slab artifact across the centre.)
        // Two radial cut caps.
        final capA = <_V3>[
          vtx(ri, a0, height),
          vtx(ro, a0, height),
          vtx(ro, a0, 0),
          vtx(ri, a0, 0),
        ];
        faces.add(_Face(proj(capA),
            metal(sel, _V3(math.sin(a0), -math.cos(a0), 0)), depthOf(capA)));
        final capB = <_V3>[
          vtx(ro, a1, height),
          vtx(ri, a1, height),
          vtx(ri, a1, 0),
          vtx(ro, a1, 0),
        ];
        faces.add(_Face(proj(capB),
            metal(sel, _V3(-math.sin(a1), math.cos(a1), 0)), depthOf(capB)));
      }
    }

    // Painter's algorithm: far (smaller depth) first.
    faces.sort((a, b) => a.depth.compareTo(b.depth));
    for (final f in faces) {
      canvas.drawPath(
        Path()..addPolygon(f.pts, true),
        Paint()
          ..color = f.color
          ..style = PaintingStyle.fill
          ..isAntiAlias = true,
      );
    }

    // Flat mode: % labels around the ring.
    if (!is3d && entry > 0.5) {
      start = -math.pi / 2;
      final labelR = ro + 16;
      for (var i = 0; i < slices.length; i++) {
        final full = slices[i].pct / total * 2 * math.pi;
        final mid = start + full / 2;
        start += full;
        final p = project(_rot(
            _V3(labelR * math.cos(mid), labelR * math.sin(mid), height),
            cs, sn, ct, st));
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
        tp.paint(canvas, p - Offset(tp.width / 2, tp.height / 2));
      }
    }

    // Centre readout (always flat, in the hole).
    final s = slices[selected.clamp(0, slices.length - 1)];
    final pctTp = TextPainter(
      text: TextSpan(
        text: '${s.pct.round()}%',
        style: AppTextStyles.valueXl.copyWith(fontWeight: FontWeight.w800),
      ),
      textDirection: TextDirection.ltr,
    )..layout();
    pctTp.paint(canvas, center - Offset(pctTp.width / 2, pctTp.height / 2 + 8));
    final nameTp = TextPainter(
      text: TextSpan(
        text: s.sector,
        style: AppTextStyles.micro.copyWith(color: AppColors.textMuted),
      ),
      textDirection: TextDirection.ltr,
      textAlign: TextAlign.center,
      maxLines: 1,
      ellipsis: '…',
    )..layout(maxWidth: ri * 1.7);
    nameTp.paint(
        canvas, center - Offset(nameTp.width / 2, nameTp.height / 2 - 14));
  }

  @override
  bool shouldRepaint(_Sector3DPainter old) =>
      old.spin != spin ||
      old.tilt != tilt ||
      old.entry != entry ||
      old.selected != selected ||
      old.is3d != is3d ||
      !identical(old.slices, slices);
}
