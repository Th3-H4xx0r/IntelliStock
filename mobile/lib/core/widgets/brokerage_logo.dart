import 'package:flutter/material.dart';
import 'package:flutter_svg/flutter_svg.dart';

import '../theme/app_colors.dart';
import 'material_symbols.dart';

/// The brand mark for a brokerage, drawn as a single-colour glyph.
///
/// Every mark is tinted with [color] (default [AppColors.primary]) via a
/// `srcIn` filter, so whatever the source SVG's own colours are, all three
/// brokerages render as one purple family. Whether a mark reads as an outline
/// or a solid silhouette is a property of the asset — supply outline SVGs for
/// outline glyphs.
///
/// If a brokerage has no asset yet — or the file is missing/malformed — this
/// falls back to the generic Material symbol it used before, so an absent
/// logo degrades quietly instead of throwing.
class BrokerageLogo extends StatelessWidget {
  const BrokerageLogo({
    super.key,
    required this.brokerageType,
    this.size = 16,
    this.color,
  });

  final String brokerageType;
  final double size;
  final Color? color;

  /// Asset path per brokerage type. Keep the keys lower-case — the API sends
  /// `brokerage_type` lower-case (`alpaca` / `kalshi` / `binanceus`).
  static const _assets = <String, String>{
    'alpaca': 'assets/brands/alpaca.svg',
    'kalshi': 'assets/brands/kalshi.svg',
    'binanceus': 'assets/brands/binanceus.svg',
  };

  /// The pre-logo Material symbol, still used when a brand asset is absent.
  static IconData fallbackIcon(String brokerageType) =>
      brokerageType.toLowerCase() == 'alpaca'
      ? symbol('show_chart')
      : symbol('savings');

  @override
  Widget build(BuildContext context) {
    final type = brokerageType.toLowerCase();
    final tint = color ?? AppColors.primary;
    final path = _assets[type];

    if (path == null) return _fallback(type, tint);

    // Probe the bundle first: SvgPicture.asset throws on a missing asset, and
    // the marks may not be checked in yet.
    return FutureBuilder<bool>(
      future: _exists(DefaultAssetBundle.of(context), path),
      builder: (context, snap) {
        if (snap.data != true) return _fallback(type, tint);
        return SvgPicture.asset(
          path,
          width: size,
          height: size,
          colorFilter: ColorFilter.mode(tint, BlendMode.srcIn),
          // The mark stands in for a labelled account row; the label carries
          // the name, so the glyph itself is decorative.
          excludeFromSemantics: true,
        );
      },
    );
  }

  Widget _fallback(String type, Color tint) =>
      Icon(fallbackIcon(type), color: tint, size: size);

  static Future<bool> _exists(AssetBundle bundle, String path) async {
    try {
      await bundle.load(path);
      return true;
    } catch (_) {
      return false;
    }
  }
}
