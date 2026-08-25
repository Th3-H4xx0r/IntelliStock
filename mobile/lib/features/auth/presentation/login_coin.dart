import 'dart:math' as math;

import 'package:flutter/material.dart';

import '../../../core/theme/app_colors.dart';

/// A native 3D-style coin for the login sequence.
///
/// The former WebView-backed GLB renderer could expose an opaque canvas on
/// iOS, flashing a grey rectangle over the login surface. This scene is drawn
/// natively, so the entrance is immediate and remains composited with the app.
enum CoinPhase { idle, working, success }

class LoginCoin extends StatefulWidget {
  const LoginCoin({
    super.key,
    required this.phase,
    this.size = 322,
    this.onEntranceStarted,
  });

  final CoinPhase phase;
  final double size;
  final VoidCallback? onEntranceStarted;

  @override
  State<LoginCoin> createState() => _LoginCoinState();
}

class _LoginCoinState extends State<LoginCoin>
    with TickerProviderStateMixin, WidgetsBindingObserver {
  late final AnimationController _entrance;
  late final AnimationController _motion;
  bool _announced = false;

  @override
  void initState() {
    super.initState();
    WidgetsBinding.instance.addObserver(this);
    _entrance = AnimationController(
      vsync: this,
      duration: const Duration(milliseconds: 1450),
    );
    _motion = AnimationController(
      vsync: this,
      duration: const Duration(milliseconds: 3000),
    )..repeat();
    WidgetsBinding.instance.addPostFrameCallback((_) {
      if (!mounted) return;
      _entrance.forward();
      _announced = true;
      widget.onEntranceStarted?.call();
    });
  }

  @override
  void dispose() {
    WidgetsBinding.instance.removeObserver(this);
    _entrance.dispose();
    _motion.dispose();
    super.dispose();
  }

  @override
  void didChangeAppLifecycleState(AppLifecycleState state) {
    if (state == AppLifecycleState.resumed) {
      _motion.repeat();
    } else {
      _motion.stop();
    }
  }

  @override
  void didUpdateWidget(covariant LoginCoin oldWidget) {
    super.didUpdateWidget(oldWidget);
    if (_announced &&
        widget.phase == CoinPhase.idle &&
        oldWidget.phase == CoinPhase.working) {
      _entrance.forward(from: 0.78);
    }
  }

  @override
  Widget build(BuildContext context) {
    final diameter = widget.size * 0.62;
    return SizedBox(
      height: widget.size,
      width: double.infinity,
      child: AnimatedBuilder(
        animation: Listenable.merge([_entrance, _motion]),
        builder: (context, _) {
          final arrived = Curves.easeOutCubic.transform(_entrance.value);
          final idleWave = math.sin(_motion.value * math.pi * 2);
          final rotation = switch (widget.phase) {
            CoinPhase.idle => (1 - arrived) * math.pi * 4 + idleWave * 0.10,
            CoinPhase.working => _motion.value * math.pi * 2,
            CoinPhase.success => (1 - arrived) * math.pi * 3 + math.pi,
          };
          final lift = (1 - arrived) * 26 + idleWave * 5;
          final scale = widget.phase == CoinPhase.success ? 1.15 : 1.0;
          return Stack(
            alignment: Alignment.center,
            children: [
              Transform.translate(
                offset: Offset(0, diameter * 0.42 + lift * 0.25),
                child: Container(
                  width: diameter * 0.76,
                  height: diameter * 0.14,
                  decoration: BoxDecoration(
                    color: AppColors.primary.withValues(alpha: 0.18),
                    borderRadius: BorderRadius.circular(diameter),
                    boxShadow: [
                      BoxShadow(
                        color: AppColors.primary.withValues(alpha: 0.26),
                        blurRadius: 28,
                        spreadRadius: 12,
                      ),
                    ],
                  ),
                ),
              ),
              Transform.translate(
                offset: Offset(0, -lift),
                child: Transform(
                  alignment: Alignment.center,
                  transform: Matrix4.identity()
                    ..setEntry(3, 2, 0.0015)
                    ..rotateY(rotation)
                    ..rotateX(-0.12),
                  child: Transform.scale(
                    scale: scale,
                    child: _CoinFace(diameter: diameter),
                  ),
                ),
              ),
            ],
          );
        },
      ),
    );
  }
}

class _CoinFace extends StatelessWidget {
  const _CoinFace({required this.diameter});

  final double diameter;

  @override
  Widget build(BuildContext context) => Stack(
        alignment: Alignment.center,
        children: [
          Transform.translate(
            offset: const Offset(0, 10),
            child: Container(
              width: diameter,
              height: diameter,
              decoration: BoxDecoration(
                shape: BoxShape.circle,
                color: const Color(0xFF10071F),
                border: Border.all(color: const Color(0xFF5A3C91), width: 3),
              ),
            ),
          ),
          Container(
            width: diameter,
            height: diameter,
            decoration: BoxDecoration(
              shape: BoxShape.circle,
              gradient: const RadialGradient(
                center: Alignment(-0.35, -0.5),
                colors: [Color(0xFF3F2773), Color(0xFF1D1235), Color(0xFF0D081B)],
                stops: [0, 0.58, 1],
              ),
              border: Border.all(color: const Color(0xFFAF91FF), width: 2),
              boxShadow: [
                BoxShadow(
                  color: AppColors.primary.withValues(alpha: 0.24),
                  blurRadius: 24,
                  spreadRadius: 2,
                ),
              ],
            ),
            child: Center(
              child: Icon(
                Icons.show_chart_rounded,
                size: diameter * 0.40,
                color: const Color(0xFFC4AEFF),
              ),
            ),
          ),
        ],
      );
}
