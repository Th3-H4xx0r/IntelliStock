import 'dart:ui';
import 'package:flutter/material.dart';
import '../theme/app_colors.dart';

/// The signature translucent violet card used pervasively in the web UI
/// (`.glass-card`): gradient fill + 14px backdrop blur + lavender border.
///
/// Set [liquid] to render the heavier "liquid glass" treatment used on the
/// dashboard — a stronger frosted blur, a specular top-edge sheen, a brighter
/// violet border, and a soft violet drop-glow so the purple backdrop blooms
/// through. The default ([liquid] = false) is unchanged from the web look and
/// is what every non-dashboard screen uses.
class GlassCard extends StatelessWidget {
  const GlassCard({
    super.key,
    this.child,
    this.padding = const EdgeInsets.all(20),
    this.onTap,
    this.borderRadius = 16,
    this.borderColor,
    this.liquid = false,
  });

  final Widget? child;
  final EdgeInsets padding;
  final VoidCallback? onTap;
  final double borderRadius;
  final Color? borderColor;
  final bool liquid;

  @override
  Widget build(BuildContext context) {
    final radius = BorderRadius.circular(borderRadius);
    final card = ClipRRect(
      borderRadius: radius,
      child: BackdropFilter(
        filter: ImageFilter.blur(
          sigmaX: liquid ? 26 : 14,
          sigmaY: liquid ? 26 : 14,
        ),
        child: liquid ? _liquidBody(radius) : _plainBody(radius),
      ),
    );

    // Liquid cards float on a soft violet glow.
    final framed = liquid
        ? DecoratedBox(
            decoration: BoxDecoration(
              borderRadius: radius,
              boxShadow: [
                BoxShadow(
                  color: AppColors.primary.withValues(alpha: 0.12),
                  blurRadius: 34,
                  spreadRadius: -12,
                  offset: const Offset(0, 16),
                ),
              ],
            ),
            child: card,
          )
        : card;

    if (onTap == null) return framed;
    return Material(
      color: Colors.transparent,
      borderRadius: radius,
      child: InkWell(borderRadius: radius, onTap: onTap, child: framed),
    );
  }

  Widget _plainBody(BorderRadius radius) {
    return Container(
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
    );
  }

  Widget _liquidBody(BorderRadius radius) {
    return Container(
      decoration: BoxDecoration(
        // Diagonal wash with a white specular sheen in the top-left corner.
        gradient: const LinearGradient(
          begin: Alignment.topLeft,
          end: Alignment.bottomRight,
          colors: [
            Color(0x24FFFFFF), // ~14% white sheen
            AppColors.glassTop,
            AppColors.glassBottom,
          ],
          stops: [0.0, 0.32, 1.0],
        ),
        borderRadius: radius,
        border: Border.all(
          color: borderColor ?? const Color(0x3DBC9AFF), // ~24% violet edge
        ),
      ),
      child: Stack(
        children: [
          // Specular highlight along the very top edge — the glassy "lip".
          Positioned(
            top: 0,
            left: 0,
            right: 0,
            child: Container(
              height: 1.2,
              decoration: BoxDecoration(
                gradient: LinearGradient(
                  colors: [
                    Colors.white.withValues(alpha: 0.0),
                    Colors.white.withValues(alpha: 0.32),
                    Colors.white.withValues(alpha: 0.0),
                  ],
                ),
              ),
            ),
          ),
          Padding(padding: padding, child: child),
        ],
      ),
    );
  }
}
