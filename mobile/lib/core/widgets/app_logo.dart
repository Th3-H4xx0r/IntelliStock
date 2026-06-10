import 'package:flutter/material.dart';
import '../theme/app_colors.dart';
import '../theme/app_text_styles.dart';

/// The IntelliStock app mark (the neon line-chart icon), rendered from the
/// bundled `assets/app_logo.png`.
class AppLogo extends StatelessWidget {
  const AppLogo({super.key, this.size = 56, this.radius});

  final double size;
  final double? radius;

  @override
  Widget build(BuildContext context) {
    return ClipRRect(
      borderRadius: BorderRadius.circular(radius ?? size * 0.23),
      child: Image.asset(
        'assets/app_logo.png',
        width: size,
        height: size,
        fit: BoxFit.cover,
        filterQuality: FilterQuality.high,
      ),
    );
  }
}

/// The app mark + "IntelliStock" wordmark, laid out horizontally.
class AppWordmark extends StatelessWidget {
  const AppWordmark({super.key, this.iconSize = 34});

  final double iconSize;

  @override
  Widget build(BuildContext context) {
    return Row(
      mainAxisSize: MainAxisSize.min,
      children: [
        AppLogo(size: iconSize),
        const SizedBox(width: 10),
        Text(
          'IntelliStock',
          style: AppTextStyles.h2
              .copyWith(color: AppColors.textHi, letterSpacing: -0.3),
        ),
      ],
    );
  }
}
