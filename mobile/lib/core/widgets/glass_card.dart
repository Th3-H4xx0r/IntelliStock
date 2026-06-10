import 'dart:ui';
import 'package:flutter/material.dart';
import '../theme/app_colors.dart';

/// The signature translucent violet card used pervasively in the web UI
/// (`.glass-card`): gradient fill + 14px backdrop blur + lavender border.
class GlassCard extends StatelessWidget {
  const GlassCard({
    super.key,
    this.child,
    this.padding = const EdgeInsets.all(20),
    this.onTap,
    this.borderRadius = 16,
    this.borderColor,
  });

  final Widget? child;
  final EdgeInsets padding;
  final VoidCallback? onTap;
  final double borderRadius;
  final Color? borderColor;

  @override
  Widget build(BuildContext context) {
    final radius = BorderRadius.circular(borderRadius);
    final card = ClipRRect(
      borderRadius: radius,
      child: BackdropFilter(
        filter: ImageFilter.blur(sigmaX: 14, sigmaY: 14),
        child: Container(
          decoration: BoxDecoration(
            gradient: const LinearGradient(
              begin: Alignment.topCenter,
              end: Alignment.bottomCenter,
              colors: [AppColors.glassTop, AppColors.glassBottom],
            ),
            borderRadius: radius,
            border: Border.all(color: borderColor ?? AppColors.glassBorder),
          ),
          padding: padding,
          child: child,
        ),
      ),
    );

    if (onTap == null) return card;
    return Material(
      color: Colors.transparent,
      borderRadius: radius,
      child: InkWell(borderRadius: radius, onTap: onTap, child: card),
    );
  }
}
