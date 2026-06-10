import 'package:flutter/material.dart';
import 'package:google_fonts/google_fonts.dart';
import 'app_colors.dart';

/// Typography ported from the web (Inter, tabular figures for numbers).
abstract class AppTextStyles {
  static const _tabular = [FontFeature.tabularFigures()];

  static TextStyle _inter(
    double size, {
    FontWeight weight = FontWeight.w400,
    Color color = AppColors.textHi,
    double? height,
    double? letterSpacing,
    bool tabular = false,
  }) {
    return GoogleFonts.inter(
      fontSize: size,
      fontWeight: weight,
      color: color,
      height: height,
      letterSpacing: letterSpacing,
      fontFeatures: tabular ? _tabular : null,
    );
  }

  static TextStyle h1 = _inter(24, weight: FontWeight.w700);
  static TextStyle h2 = _inter(20, weight: FontWeight.w700);
  static TextStyle h3 = _inter(16, weight: FontWeight.w600);
  static TextStyle cardTitle = _inter(14, weight: FontWeight.w600);
  static TextStyle body = _inter(14, color: AppColors.textMd);
  static TextStyle bodyHi = _inter(14, color: AppColors.textHi);
  static TextStyle meta = _inter(12, color: AppColors.textMuted);
  static TextStyle micro = _inter(11, color: AppColors.textMuted);
  static TextStyle nano = _inter(10, color: AppColors.textMuted);

  /// Eyebrow label (uppercase, wide tracking, primary).
  static TextStyle eyebrow = _inter(
    12,
    weight: FontWeight.w700,
    color: AppColors.primary,
    letterSpacing: 1.6,
  );

  /// Big tabular value (portfolio equity etc.).
  static TextStyle valueLg = _inter(24, weight: FontWeight.w700, tabular: true);
  static TextStyle valueXl = _inter(30, weight: FontWeight.w800, tabular: true);
  static TextStyle value = _inter(16, weight: FontWeight.w600, tabular: true);

  static TextStyle mono(double size,
      {Color color = AppColors.textMd, FontWeight weight = FontWeight.w400}) {
    return GoogleFonts.jetBrainsMono(
      fontSize: size,
      color: color,
      fontWeight: weight,
      fontFeatures: _tabular,
    );
  }
}
