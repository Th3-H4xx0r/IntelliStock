import 'dart:math' as math;
import 'package:flutter/material.dart';

/// A lightweight animated particle network that mirrors the web login canvas.
///
/// Renders ~48 slowly drifting nodes in `rgba(167,139,250,0.28)` with
/// connecting lines between any pair closer than ~110 logical pixels (line
/// opacity scaled linearly by distance so they fade at the edges).
class ParticleField extends StatefulWidget {
  const ParticleField({super.key});

  @override
  State<ParticleField> createState() => _ParticleFieldState();
}

class _ParticleFieldState extends State<ParticleField>
    with SingleTickerProviderStateMixin {
  late final AnimationController _controller;
  late final List<_Particle> _particles;

  static const int _count = 48;
  static const double _connectDist = 110.0;

  @override
  void initState() {
    super.initState();
    // Seed particles; positions are relative [0..1] and expanded to canvas
    // size in the painter so resize is free.
    final rng = math.Random();
    _particles = List.generate(_count, (_) => _Particle.random(rng));

    _controller = AnimationController(
      vsync: this,
      duration: const Duration(seconds: 1),
    )..repeat();
  }

  @override
  void dispose() {
    _controller.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    return AnimatedBuilder(
      animation: _controller,
      builder: (_, _) {
        // Advance each particle by one frame (~16 ms at 60 fps).
        for (final p in _particles) {
          p.tick();
        }
        return CustomPaint(
          painter: _ParticlePainter(_particles, _connectDist),
          child: const SizedBox.expand(),
        );
      },
    );
  }
}

class _Particle {
  _Particle({
    required this.x,
    required this.y,
    required this.vx,
    required this.vy,
    required this.radius,
  });

  factory _Particle.random(math.Random rng) {
    return _Particle(
      x: rng.nextDouble(),
      y: rng.nextDouble(),
      // velocity in normalised units per frame (≈0.35 / 60 normalised)
      vx: (rng.nextDouble() - 0.5) * 0.35 / 60,
      vy: (rng.nextDouble() - 0.5) * 0.35 / 60,
      radius: rng.nextDouble() * 1.5 + 0.5,
    );
  }

  double x; // [0..1]
  double y; // [0..1]
  double vx;
  double vy;
  final double radius;

  void tick() {
    x += vx;
    y += vy;
    // Wrap around edges.
    if (x < 0) x += 1.0;
    if (x > 1) x -= 1.0;
    if (y < 0) y += 1.0;
    if (y > 1) y -= 1.0;
  }
}

class _ParticlePainter extends CustomPainter {
  _ParticlePainter(this.particles, this.connectDist);

  final List<_Particle> particles;
  final double connectDist;

  // Reuse paints to avoid per-frame allocations.
  final Paint _dotPaint = Paint()
    ..color = const Color.fromRGBO(167, 139, 250, 0.28);

  final Paint _linePaint = Paint()
    ..strokeWidth = 0.6
    ..style = PaintingStyle.stroke;

  @override
  void paint(Canvas canvas, Size size) {
    final w = size.width;
    final h = size.height;

    // Draw dots.
    for (final p in particles) {
      canvas.drawCircle(Offset(p.x * w, p.y * h), p.radius, _dotPaint);
    }

    // Draw connecting lines.
    final count = particles.length;
    for (var i = 0; i < count - 1; i++) {
      for (var j = i + 1; j < count; j++) {
        final pi = particles[i];
        final pj = particles[j];
        final dx = (pi.x - pj.x) * w;
        final dy = (pi.y - pj.y) * h;
        final dist = math.sqrt(dx * dx + dy * dy);
        if (dist < connectDist) {
          final alpha = (1.0 - dist / connectDist) * 0.10;
          _linePaint.color = Color.fromRGBO(167, 139, 250, alpha);
          canvas.drawLine(
            Offset(pi.x * w, pi.y * h),
            Offset(pj.x * w, pj.y * h),
            _linePaint,
          );
        }
      }
    }
  }

  @override
  bool shouldRepaint(_ParticlePainter oldDelegate) => true;
}
