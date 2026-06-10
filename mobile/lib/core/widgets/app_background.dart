import 'package:flutter/material.dart';

/// The app-wide backdrop: two soft purple radial glows over a vertical
/// near-black gradient, matching the web `.app-shell-root`.
class AppBackground extends StatelessWidget {
  const AppBackground({super.key, required this.child});

  final Widget child;

  @override
  Widget build(BuildContext context) {
    return DecoratedBox(
      decoration: const BoxDecoration(
        gradient: LinearGradient(
          begin: Alignment.topCenter,
          end: Alignment.bottomCenter,
          colors: [Color(0xFF010107), Color(0xFF02030A), Color(0xFF04040C)],
          stops: [0.0, 0.46, 1.0],
        ),
      ),
      child: Stack(
        children: [
          Positioned(
            top: -120,
            left: -100,
            child: _glow(const Color(0xFF7637F2).withValues(alpha: 0.14), 360),
          ),
          Positioned(
            bottom: -120,
            right: -100,
            child: _glow(const Color(0xFFD24EFF).withValues(alpha: 0.10), 320),
          ),
          child,
        ],
      ),
    );
  }

  Widget _glow(Color color, double size) {
    return IgnorePointer(
      child: Container(
        width: size,
        height: size,
        decoration: BoxDecoration(
          shape: BoxShape.circle,
          gradient: RadialGradient(colors: [color, color.withValues(alpha: 0)]),
        ),
      ),
    );
  }
}
