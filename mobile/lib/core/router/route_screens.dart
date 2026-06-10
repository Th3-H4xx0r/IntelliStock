import 'package:flutter/material.dart';
import '../theme/app_text_styles.dart';
import '../widgets/app_background.dart';

/// Temporary placeholder used until each feature screen is wired in.
/// Integration replaces these references with the real feature screens.
class PlaceholderScreen extends StatelessWidget {
  const PlaceholderScreen(this.title, {super.key, this.standalone = false});

  final String title;
  final bool standalone;

  @override
  Widget build(BuildContext context) {
    final body = Center(child: Text(title, style: AppTextStyles.h2));
    if (!standalone) return body;
    return Scaffold(body: AppBackground(child: SafeArea(child: body)));
  }
}
