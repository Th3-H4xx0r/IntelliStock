import 'package:flutter/foundation.dart';
import 'package:flutter/services.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'push_repository.dart';

/// Bridges the native iOS APNs registration to the backend.
///
/// The native `AppDelegate` (MethodChannel `intellistock/push`) requests push
/// authorization, registers for remote notifications, and pushes the hex device
/// token back to Dart via `onToken`. We forward it to `POST /push/devices`.
/// No Firebase. iOS-only; on other platforms `enable()` is a no-op.
class PushService {
  PushService(this._ref);

  final Ref _ref;
  static const MethodChannel _channel = MethodChannel('intellistock/push');
  bool _started = false;
  String? _appVersion;

  /// Debug builds talk to the APNs sandbox; release builds to production.
  String get _env => kReleaseMode ? 'prod' : 'sandbox';

  /// Ask iOS to register for push and start forwarding tokens. Idempotent —
  /// safe to call on every login / app start.
  Future<void> enable({String? appVersion}) async {
    if (defaultTargetPlatform != TargetPlatform.iOS) return;
    _appVersion = appVersion ?? _appVersion;
    if (!_started) {
      _started = true;
      _channel.setMethodCallHandler(_onNativeCall);
    }
    try {
      await _channel.invokeMethod('registerPush');
    } on PlatformException {
      // permission denied / unavailable — user can retry from settings.
    } on MissingPluginException {
      // channel not wired (non-iOS / tests) — no-op.
    }
  }

  Future<dynamic> _onNativeCall(MethodCall call) async {
    if (call.method == 'onToken') {
      final token = call.arguments as String?;
      if (token != null && token.isNotEmpty) {
        try {
          await _ref.read(pushRepositoryProvider).registerToken(
                token,
                env: _env,
                appVersion: _appVersion,
              );
        } catch (_) {
          // best-effort; iOS re-delivers the token on next launch.
        }
      }
    }
    return null;
  }
}

final pushServiceProvider = Provider<PushService>((ref) => PushService(ref));
