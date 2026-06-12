import 'dart:ui';
import 'package:flutter/material.dart';
import '../theme/app_colors.dart';

/// The signature translucent violet card used pervasively in the web UI
/// (`.glass-card`): gradient fill + 14px backdrop blur + lavender border.
///
/// Set [liquid] for the dashboard's clean "elevated surface" treatment — a
/// near-opaque dark card with a hairline light border, a whisper of a top
/// highlight, and a soft drop shadow for depth. It deliberately avoids the
/// frosted-glass blur so the dashboard reads like a modern fintech home
/// screen. The default ([liquid] = false) is unchanged from the web look and
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
    this.frosted = false,
  });

  final Widget? child;
  final EdgeInsets padding;
  final VoidCallback? onTap;
  final double borderRadius;
  final Color? borderColor;
  final bool liquid;

  /// Translucent "liquid glass": a heavy frosted blur with a white top sheen, a
  /// near-clear middle (the backdrop blooms through), and a bright light rim.
  /// Takes precedence over [liquid].
  final bool frosted;

  @override
  Widget build(BuildContext context) {
    final radius = BorderRadius.circular(borderRadius);

    // frosted → translucent liquid glass (heavy blur); liquid → near-opaque
    // flat surface (no blur); default → the web frosted-violet glass card.
    final Widget card;
    if (frosted) {
      card = ClipRRect(
        borderRadius: radius,
        child: BackdropFilter(
          filter: ImageFilter.blur(sigmaX: 24, sigmaY: 24),
          child: _frostedBody(radius),
        ),
      );
    } else if (liquid) {
      card = ClipRRect(borderRadius: radius, child: _liquidBody(radius));
    } else {
      card = ClipRRect(
        borderRadius: radius,
        child: BackdropFilter(
          filter: ImageFilter.blur(sigmaX: 14, sigmaY: 14),
          child: _plainBody(radius),
        ),
      );
    }

    // Elevated / glass cards float on a soft, neutral drop shadow for depth.
    final framed = (liquid || frosted)
        ? DecoratedBox(
            decoration: BoxDecoration(
              borderRadius: radius,
              boxShadow: [
                BoxShadow(
                  color: const Color(0xFF000000).withValues(alpha: 0.28),
                  blurRadius: 24,
                  spreadRadius: -6,
                  offset: const Offset(0, 12),
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

  Widget _frostedBody(BorderRadius radius) {
    return Container(
      decoration: BoxDecoration(
        // Translucent frosted glass: a white sheen up top melting into a
        // near-clear middle (the blurred backdrop reads through) and a soft
        // violet foot for legibility.
        gradient: const LinearGradient(
          begin: Alignment.topLeft,
          end: Alignment.bottomRight,
          colors: [
            Color(0x2EFFFFFF), // ~18% white sheen (top-left)
            Color(0x0FFFFFFF), // ~6% near-clear middle
            Color(0x40140E2A), // ~25% deep violet foot
          ],
          stops: [0.0, 0.5, 1.0],
        ),
        borderRadius: radius,
        border: Border.all(color: borderColor ?? const Color(0x33FFFFFF)),
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
                    Colors.white.withValues(alpha: 0.40),
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

  Widget _liquidBody(BorderRadius radius) {
    return Container(
      decoration: BoxDecoration(
        // Clean elevated surface: a near-opaque dark card, very subtly lighter
        // at the top, sitting a notch above the canvas — no frosted blur.
        gradient: const LinearGradient(
          begin: Alignment.topCenter,
          end: Alignment.bottomCenter,
          colors: [
            Color(0xF21A1530), // ~95% — slightly lifted top
            Color(0xF2120E22), // ~95% — deeper foot
          ],
        ),
        borderRadius: radius,
        border: Border.all(
          color: borderColor ?? const Color(0x14FFFFFF), // ~8% hairline rim
        ),
      ),
      child: Stack(
        children: [
          // A whisper of a highlight along the very top edge.
          Positioned(
            top: 0,
            left: 0,
            right: 0,
            child: Container(
              height: 1,
              decoration: BoxDecoration(
                gradient: LinearGradient(
                  colors: [
                    Colors.white.withValues(alpha: 0.0),
                    Colors.white.withValues(alpha: 0.10),
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
