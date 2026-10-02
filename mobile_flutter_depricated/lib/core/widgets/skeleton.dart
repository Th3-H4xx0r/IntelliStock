import 'package:flutter/material.dart';

/// A shimmering placeholder block used for skeleton loading states.
///
/// Compose these to mirror a screen's real layout while data loads:
/// ```dart
/// Column(children: [
///   Skeleton(width: 160, height: 24),       // a title
///   SizedBox(height: 8),
///   Skeleton(height: 120, radius: 16),      // a card
/// ])
/// ```
class Skeleton extends StatefulWidget {
  const Skeleton({
    super.key,
    this.width,
    this.height = 14,
    this.radius = 8,
  });

  final double? width;
  final double height;
  final double radius;

  /// Convenience: a short text line.
  static Widget line({double width = double.infinity, double height = 12}) =>
      Skeleton(width: width == double.infinity ? null : width, height: height, radius: 6);

  /// Convenience: a circle (avatar / icon tile).
  static Widget circle(double size) =>
      Skeleton(width: size, height: size, radius: size / 2);

  @override
  State<Skeleton> createState() => _SkeletonState();
}

class _SkeletonState extends State<Skeleton>
    with SingleTickerProviderStateMixin {
  static const _base = Color(0xFF18112B);
  static const _highlight = Color(0xFF2A2142);

  late final AnimationController _ctrl = AnimationController(
    vsync: this,
    duration: const Duration(milliseconds: 1300),
  )..repeat();

  @override
  void dispose() {
    _ctrl.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    return AnimatedBuilder(
      animation: _ctrl,
      builder: (context, _) {
        final t = _ctrl.value * 2; // 0 → 2 sweeps the band left → right
        return Container(
          width: widget.width,
          height: widget.height,
          decoration: BoxDecoration(
            borderRadius: BorderRadius.circular(widget.radius),
            gradient: LinearGradient(
              colors: const [_base, _highlight, _base],
              stops: const [0.1, 0.5, 0.9],
              begin: Alignment(t - 1.0, 0),
              end: Alignment(t, 0),
            ),
          ),
        );
      },
    );
  }
}
