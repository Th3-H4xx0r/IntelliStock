import 'package:flutter/material.dart';

/// Design-system colors, ported 1:1 from the web `tailwind.config.js` + `style.css`.
/// The app is dark-mode only (the web hardcodes `<html class="dark">`).
abstract class AppColors {
  // Brand / surfaces
  static const primary = Color(0xFFA78BFA); // violet
  static const onPrimary = Color(0xFF04040C); // dark text on violet buttons
  static const canvas = Color(0xFF04040C); // near-black scaffold base
  static const surface = Color(0xFF0F0A1C); // inputs / secondary surfaces
  static const border = Color(0xFF231A3D); // hairline borders
  static const panel = Color(0xFF0F1318); // modal panels
  static const panelAlt = Color(0xFF0D1117); // log / terminal panels

  // Text scale (Tailwind slate)
  static const textHi = Color(0xFFF1F5F9); // slate-100
  static const textMd = Color(0xFFCBD5E1); // slate-300
  static const textMuted = Color(0xFF94A3B8); // slate-400
  static const textDim = Color(0xFF64748B); // slate-500
  static const textFaint = Color(0xFF475569); // slate-600

  // Semantic (used at 10% fill / 20% border / 400-level text)
  static const success = Color(0xFF34D399); // emerald-400
  static const danger = Color(0xFFF87171); // red-400
  static const info = Color(0xFF38BDF8); // sky-400
  static const warning = Color(0xFFFBBF24); // amber-400
  static const teal = Color(0xFF2DD4BF);
  static const accent = primary;

  // Chart palette
  static const chartUp = Color(0xFF10B981);
  static const chartDown = Color(0xFFEF4444);
  static const chartLine = Color(0xFF38BDF8);
  static const chartLineAlt = Color(0xFF0EA5E9);
  static const chartGrid = Color(0xFF1E2535);
  static const chartAxis = Color(0xFF64748B);

  // Glass-card gradient stops + border
  static const glassTop = Color(0xC7170E2D); // rgba(23,14,45,0.78)
  static const glassBottom = Color(0xB30B0818); // rgba(11,8,24,0.70)
  static Color glassBorder = const Color(0xFFBC9AFF).withValues(alpha: 0.12);

  /// Semantic 10% fill (matches `bg-{color}-500/10`).
  static Color fill(Color c) => c.withValues(alpha: 0.10);

  /// Semantic 20% border (matches `border-{color}-500/20`).
  static Color stroke(Color c) => c.withValues(alpha: 0.20);
}
