import 'dart:math' as math;

import 'package:flutter/material.dart';

/// The Face ID mark — four corner brackets around two eyes, a nose and a
/// smile. Drawn rather than imported: it is an Apple glyph, so it is in no
/// Material icon set, and stroking it here keeps it crisp at any size and lets
/// it inherit the app's violet.
class FaceIdGlyph extends StatelessWidget {
  const FaceIdGlyph({super.key, this.size = 26, this.color = Colors.white});

  final double size;
  final Color color;

  @override
  Widget build(BuildContext context) => SizedBox(
    width: size,
    height: size,
    child: CustomPaint(painter: _FaceIdPainter(color)),
  );
}

class _FaceIdPainter extends CustomPainter {
  const _FaceIdPainter(this.color);

  final Color color;

  @override
  void paint(Canvas canvas, Size size) {
    final s = size.shortestSide;
    final paint = Paint()
      ..color = color
      ..style = PaintingStyle.stroke
      ..strokeWidth = s * 0.075
      ..strokeCap = StrokeCap.round
      ..strokeJoin = StrokeJoin.round;

    final arm = s * 0.32; // how far each bracket runs along its edges
    final r = s * 0.19; // corner rounding
    final inset = paint.strokeWidth / 2;
    final lo = inset, hi = s - inset;

    // Four corner brackets, each an L with a rounded elbow.
    final brackets = Path()
      // top-left
      ..moveTo(lo, lo + arm)
      ..lineTo(lo, lo + r)
      ..arcToPoint(Offset(lo + r, lo), radius: Radius.circular(r))
      ..lineTo(lo + arm, lo)
      // top-right
      ..moveTo(hi - arm, lo)
      ..lineTo(hi - r, lo)
      ..arcToPoint(Offset(hi, lo + r), radius: Radius.circular(r))
      ..lineTo(hi, lo + arm)
      // bottom-right
      ..moveTo(hi, hi - arm)
      ..lineTo(hi, hi - r)
      ..arcToPoint(Offset(hi - r, hi), radius: Radius.circular(r))
      ..lineTo(hi - arm, hi)
      // bottom-left
      ..moveTo(lo + arm, hi)
      ..lineTo(lo + r, hi)
      ..arcToPoint(Offset(lo, hi - r), radius: Radius.circular(r))
      ..lineTo(lo, hi - arm);
    canvas.drawPath(brackets, paint);

    // The face is drawn a touch lighter than the brackets so the mark reads as
    // a frame around a face rather than one dense blob at this size.
    final face = Paint()
      ..color = color
      ..style = PaintingStyle.stroke
      ..strokeWidth = s * 0.068
      ..strokeCap = StrokeCap.round
      ..strokeJoin = StrokeJoin.round;

    // Eyes — set wide and high, clear of the nose.
    canvas.drawLine(
      Offset(s * 0.355, s * 0.335),
      Offset(s * 0.355, s * 0.435),
      face,
    );
    canvas.drawLine(
      Offset(s * 0.645, s * 0.335),
      Offset(s * 0.645, s * 0.435),
      face,
    );

    // Nose — a stem with a short flick to the right at its base.
    canvas.drawPath(
      Path()
        ..moveTo(s * 0.5, s * 0.375)
        ..lineTo(s * 0.5, s * 0.515)
        ..lineTo(s * 0.565, s * 0.515),
      face,
    );

    // Smile — the lower arc of a wide, shallow ellipse.
    canvas.drawArc(
      Rect.fromLTRB(s * 0.35, s * 0.5, s * 0.65, s * 0.71),
      math.pi * 0.10,
      math.pi * 0.80,
      false,
      face,
    );
  }

  @override
  bool shouldRepaint(covariant _FaceIdPainter old) => old.color != color;
}
