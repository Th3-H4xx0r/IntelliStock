import 'package:flutter/material.dart';

import '../theme/app_colors.dart';
import '../theme/app_text_styles.dart';

/// The auth flow's action button: a near-black body inside a luminous
/// lavender rim, with a white label and a soft glow beneath.
///
/// Shared by the lock screen and the login screen so the two cannot drift.
/// [leading] is optional (the lock screen puts its Face ID glyph there); the
/// trailing chevron is on by default. Both ends reserve the same width, so the
/// label sits optically centred whether or not a leading glyph is present.
class AuthPillButton extends StatelessWidget {
  const AuthPillButton({
    super.key,
    required this.label,
    required this.onPressed,
    this.leading,
    this.showChevron = true,
    this.busy = false,
    this.busyLabel,
    this.height = 60,
  });

  final String label;
  final VoidCallback? onPressed;
  final Widget? leading;
  final bool showChevron;
  final bool busy;

  /// When set, [busy] swaps the label for a spinner and this text. When null,
  /// a busy button just dims — used where a system prompt is already on screen.
  final String? busyLabel;

  final double height;

  double get _radius => height / 2;

  /// Equal reserved width at both ends, so the label is centred on the pill
  /// rather than on the space left over between the glyph and the chevron.
  static const double _endBox = 26;

  /// The lit edge. Brighter than [AppColors.primary] because it is the only
  /// colour on the button — the body stays near-black.
  static const Color _rim = Color(0xFFB9A5F5);

  @override
  Widget build(BuildContext context) {
    final labelStyle = AppTextStyles.bodyHi.copyWith(
      fontSize: 16,
      fontWeight: FontWeight.w600,
      letterSpacing: -0.2,
      color: Colors.white,
    );

    return Opacity(
      opacity: busy ? 0.6 : 1,
      child: Container(
        height: height,
        decoration: BoxDecoration(
          // The colour lives in the RIM, not the fill. A tinted fill has to
          // be opaque (a translucent one takes its colour from whatever is
          // behind it, which went muddy over the login card) and an opaque
          // violet reads heavy. A near-black body inside a luminous lavender
          // edge stays crisp on any backdrop.
          gradient: const LinearGradient(
            begin: Alignment.topLeft,
            end: Alignment.bottomRight,
            colors: [Color(0xFF171029), Color(0xFF0C0716)],
          ),
          borderRadius: BorderRadius.circular(_radius),
          border: Border.all(color: _rim.withValues(alpha: 0.78), width: 1.2),
          boxShadow: busy
              ? null
              : [
                  BoxShadow(
                    color: AppColors.primary.withValues(alpha: 0.20),
                    blurRadius: 22,
                    spreadRadius: -8,
                    offset: const Offset(0, 6),
                  ),
                ],
        ),
        child: Material(
          color: Colors.transparent,
          borderRadius: BorderRadius.circular(_radius),
          child: InkWell(
            borderRadius: BorderRadius.circular(_radius),
            onTap: busy ? null : onPressed,
            child: Padding(
              padding: const EdgeInsets.symmetric(horizontal: 22),
              child: Row(
                children: [
                  SizedBox(width: _endBox, child: leading),
                  Expanded(
                    child: busy && busyLabel != null
                        ? Row(
                            mainAxisSize: MainAxisSize.min,
                            mainAxisAlignment: MainAxisAlignment.center,
                            children: [
                              SizedBox(
                                width: 16,
                                height: 16,
                                child: CircularProgressIndicator(
                                  strokeWidth: 2,
                                  color: _rim,
                                ),
                              ),
                              const SizedBox(width: 10),
                              Text(busyLabel!, style: labelStyle),
                            ],
                          )
                        : Text(
                            label,
                            textAlign: TextAlign.center,
                            style: labelStyle,
                          ),
                  ),
                  SizedBox(
                    width: _endBox,
                    child: showChevron
                        ? Align(
                            alignment: Alignment.centerRight,
                            child: Icon(
                              Icons.chevron_right,
                              size: 20,
                              color: _rim,
                            ),
                          )
                        : null,
                  ),
                ],
              ),
            ),
          ),
        ),
      ),
    );
  }
}
