import 'package:flutter/material.dart';
import '../theme/app_colors.dart';

/// Pill toggle switch matching the web `h-5 w-9 rounded-full` switch.
class AppToggle extends StatelessWidget {
  const AppToggle({super.key, required this.value, this.onChanged});

  final bool value;
  final ValueChanged<bool>? onChanged;

  @override
  Widget build(BuildContext context) {
    return GestureDetector(
      onTap: onChanged == null ? null : () => onChanged!(!value),
      child: AnimatedContainer(
        duration: const Duration(milliseconds: 150),
        width: 36,
        height: 20,
        padding: const EdgeInsets.all(2),
        decoration: BoxDecoration(
          color: value ? AppColors.primary : AppColors.surface,
          borderRadius: BorderRadius.circular(999),
          border: Border.all(
              color: value ? AppColors.primary : AppColors.border, width: 2),
        ),
        child: AnimatedAlign(
          duration: const Duration(milliseconds: 150),
          alignment: value ? Alignment.centerRight : Alignment.centerLeft,
          child: Container(
            width: 14,
            height: 14,
            decoration: const BoxDecoration(
                color: Colors.white, shape: BoxShape.circle),
          ),
        ),
      ),
    );
  }
}
