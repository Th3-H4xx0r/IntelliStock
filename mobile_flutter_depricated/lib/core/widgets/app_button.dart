import 'package:flutter/material.dart';
import '../theme/app_colors.dart';
import '../theme/app_text_styles.dart';

enum _Variant { primary, ghost, semantic }

/// Button matching the web button system.
/// - primary: violet fill, dark text
/// - ghost: bordered, muted text
/// - semantic: colored text + 10% fill on press, 20% border
class AppButton extends StatelessWidget {
  const AppButton.primary({
    super.key,
    required this.label,
    this.icon,
    this.onPressed,
    this.busy = false,
    this.dense = false,
  })  : _variant = _Variant.primary,
        color = AppColors.primary;

  const AppButton.ghost({
    super.key,
    required this.label,
    this.icon,
    this.onPressed,
    this.busy = false,
    this.dense = false,
  })  : _variant = _Variant.ghost,
        color = AppColors.textMuted;

  const AppButton.semantic({
    super.key,
    required this.label,
    required this.color,
    this.icon,
    this.onPressed,
    this.busy = false,
    this.dense = false,
  }) : _variant = _Variant.semantic;

  final String label;
  final IconData? icon;
  final VoidCallback? onPressed;
  final bool busy;
  final bool dense;
  final Color color;
  final _Variant _variant;

  @override
  Widget build(BuildContext context) {
    final disabled = onPressed == null || busy;
    final pad = dense
        ? const EdgeInsets.symmetric(horizontal: 12, vertical: 8)
        : const EdgeInsets.symmetric(horizontal: 16, vertical: 12);

    late final Color bg;
    late final Color fg;
    late final BoxBorder? border;
    switch (_variant) {
      case _Variant.primary:
        bg = AppColors.primary;
        fg = AppColors.onPrimary;
        border = null;
      case _Variant.ghost:
        bg = Colors.transparent;
        fg = AppColors.textMuted;
        border = Border.all(color: AppColors.border);
      case _Variant.semantic:
        bg = AppColors.fill(color);
        fg = color;
        border = Border.all(color: AppColors.stroke(color));
    }

    return Opacity(
      opacity: disabled ? 0.4 : 1,
      child: Material(
        color: bg,
        borderRadius: BorderRadius.circular(8),
        child: InkWell(
          borderRadius: BorderRadius.circular(8),
          onTap: disabled ? null : onPressed,
          child: Container(
            padding: pad,
            decoration: BoxDecoration(
              borderRadius: BorderRadius.circular(8),
              border: border,
            ),
            child: Row(
              mainAxisSize: MainAxisSize.min,
              children: [
                if (busy)
                  SizedBox(
                    width: 16,
                    height: 16,
                    child: CircularProgressIndicator(strokeWidth: 2, color: fg),
                  )
                else if (icon != null)
                  Icon(icon, size: 18, color: fg),
                if (busy || icon != null) const SizedBox(width: 8),
                Text(
                  label,
                  style: AppTextStyles.cardTitle.copyWith(color: fg),
                ),
              ],
            ),
          ),
        ),
      ),
    );
  }
}
