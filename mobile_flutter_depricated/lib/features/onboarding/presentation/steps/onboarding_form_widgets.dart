import 'package:flutter/material.dart';
import '../../../../core/theme/app_colors.dart';
import '../../../../core/theme/app_text_styles.dart';

/// Shared form widgets reused across onboarding step forms.

/// A labelled text input styled to match the dark design system.
class OnboardingField extends StatelessWidget {
  const OnboardingField({
    super.key,
    required this.label,
    required this.hint,
    required this.controller,
    this.obscure = false,
    this.enabled = true,
    this.onChanged,
    this.errorText,
  });

  final String label;
  final String hint;
  final TextEditingController controller;
  final bool obscure;
  final bool enabled;
  final ValueChanged<String>? onChanged;
  final String? errorText;

  @override
  Widget build(BuildContext context) {
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Text(
          label,
          style: AppTextStyles.nano.copyWith(
            color: AppColors.textMuted,
            fontWeight: FontWeight.w500,
          ),
        ),
        const SizedBox(height: 6),
        TextField(
          controller: controller,
          enabled: enabled,
          obscureText: obscure,
          onChanged: onChanged,
          style: AppTextStyles.body.copyWith(color: AppColors.textHi),
          decoration: InputDecoration(
            hintText: hint,
            hintStyle:
                AppTextStyles.body.copyWith(color: AppColors.textFaint),
            errorText: errorText,
            errorStyle: AppTextStyles.nano.copyWith(color: AppColors.danger),
            contentPadding:
                const EdgeInsets.symmetric(horizontal: 12, vertical: 10),
            filled: true,
            fillColor: AppColors.surface,
            border: OutlineInputBorder(
              borderRadius: BorderRadius.circular(8),
              borderSide: const BorderSide(color: AppColors.border),
            ),
            enabledBorder: OutlineInputBorder(
              borderRadius: BorderRadius.circular(8),
              borderSide: BorderSide(
                color: errorText != null ? AppColors.danger : AppColors.border,
              ),
            ),
            focusedBorder: OutlineInputBorder(
              borderRadius: BorderRadius.circular(8),
              borderSide: BorderSide(
                color: errorText != null
                    ? AppColors.danger
                    : AppColors.primary,
                width: 1.5,
              ),
            ),
            disabledBorder: OutlineInputBorder(
              borderRadius: BorderRadius.circular(8),
              borderSide:
                  BorderSide(color: AppColors.border.withValues(alpha: 0.5)),
            ),
          ),
        ),
      ],
    );
  }
}

/// A success/error message banner shown after a form submission attempt.
class OnboardingMessageBanner extends StatelessWidget {
  const OnboardingMessageBanner({
    super.key,
    required this.message,
    required this.ok,
  });

  final String message;
  final bool ok;

  @override
  Widget build(BuildContext context) {
    final color = ok ? AppColors.success : AppColors.danger;
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 10),
      decoration: BoxDecoration(
        color: AppColors.fill(color),
        borderRadius: BorderRadius.circular(8),
        border: Border.all(color: AppColors.stroke(color)),
      ),
      child: Text(
        message,
        style: AppTextStyles.meta.copyWith(color: color),
      ),
    );
  }
}
