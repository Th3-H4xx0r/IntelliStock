import 'dart:async';

import 'package:google_fonts/google_fonts.dart';

/// Disable Google Fonts' runtime HTTP fetching in tests — otherwise text styles
/// that use `GoogleFonts.*` throw when the sandbox has no network. Text falls
/// back to the bundled test font (renders as boxes), which is fine for goldens.
Future<void> testExecutable(FutureOr<void> Function() testMain) async {
  GoogleFonts.config.allowRuntimeFetching = false;
  await testMain();
}
