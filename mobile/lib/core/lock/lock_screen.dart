import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import '../network/session.dart';
import '../theme/app_colors.dart';
import '../theme/app_text_styles.dart';
import '../widgets/app_background.dart';
import '../widgets/app_logo.dart';
import '../widgets/auth_pill_button.dart';
import '../widgets/face_id_glyph.dart';
import 'app_lock_controller.dart';
import 'biometric_service.dart';

/// Full-screen lock gate shown when [AppLockState.locked] is true.
///
/// Behaviour:
///  • Auto-prompts Face ID/biometrics the moment it appears, and again whenever
///    the app returns to the foreground — so the user never has to tap to unlock
///    on a normal relaunch.
///  • If biometrics fail or are cancelled, it offers a retry and log-out escape.
class LockScreen extends ConsumerStatefulWidget {
  const LockScreen({super.key});

  @override
  ConsumerState<LockScreen> createState() => _LockScreenState();
}

class _LockScreenState extends ConsumerState<LockScreen>
    with WidgetsBindingObserver {
  bool _busy = false;
  bool _failed = false;
  AppLifecycleState _prevLifecycle = AppLifecycleState.resumed;

  /// How this device authenticates — "Face ID", "Touch ID" or "biometrics".
  /// Names the button and the failure headline, so the copy never claims Face
  /// ID on a Touch ID phone. Defaults optimistically and corrects itself.
  String _method = 'Face ID';
  bool _isFace = true;

  @override
  void initState() {
    super.initState();
    WidgetsBinding.instance.addObserver(this);
    WidgetsBinding.instance.addPostFrameCallback((_) {
      // Resolve the label in the background; never delay the auto-prompt for it.
      _resolveMethod();
      _attempt();
    });
  }

  @override
  void dispose() {
    WidgetsBinding.instance.removeObserver(this);
    super.dispose();
  }

  Future<void> _resolveMethod() async {
    try {
      final types = await ref.read(biometricServiceProvider).availableTypes();
      final joined = types.map((t) => t.toString()).join(',');
      final face = joined.contains('face');
      final method = face
          ? 'Face ID'
          : (joined.contains('fingerprint') ? 'Touch ID' : 'biometrics');
      if (!mounted || (method == _method && face == _isFace)) return;
      setState(() {
        _method = method;
        _isFace = face;
      });
    } catch (_) {
      // Keep the optimistic default — this only drives copy, never access.
    }
  }

  @override
  void didChangeAppLifecycleState(AppLifecycleState state) {
    // Re-prompt automatically only when returning from a genuine background
    // (paused/hidden) — NOT from `inactive`, which the Face ID sheet itself
    // triggers (that would cause a prompt loop after a cancel).
    if (state == AppLifecycleState.resumed &&
        (_prevLifecycle == AppLifecycleState.paused ||
            _prevLifecycle == AppLifecycleState.hidden ||
            _prevLifecycle == AppLifecycleState.detached)) {
      if (ref.read(appLockControllerProvider).locked && !_busy) {
        _attempt();
      }
    }
    _prevLifecycle = state;
  }

  Future<void> _attempt() async {
    if (_busy) return;
    setState(() {
      _busy = true;
      _failed = false;
    });
    final ok = await ref.read(appLockControllerProvider.notifier).unlock();
    if (!mounted) return;
    setState(() {
      _busy = false;
      _failed = !ok; // on success the screen is torn down by the lock gate
    });
  }

  /// Log out of the locked session without changing the biometric-lock setting.
  Future<void> _exitToLogin() async {
    final lockCtrl = ref.read(appLockControllerProvider.notifier);
    final session = ref.read(sessionProvider);
    lockCtrl.releaseLock(); // open the gate so the router can show /login
    await session.clear(); // → router redirects to /login
  }

  String get _headline {
    if (_busy) return 'Authenticating…';
    if (_failed) return '$_method wasn\u2019t recognized.';
    return 'IntelliStock is locked.';
  }

  String get _subtitle {
    if (_busy) return 'Hold still.';
    if (_failed) return 'Try again.';
    return 'Verify your identity to continue.';
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      backgroundColor: AppColors.canvas,
      body: AppBackground(
        child: SafeArea(
          child: Padding(
            padding: const EdgeInsets.fromLTRB(24, 24, 24, 20),
            child: Stack(
              children: [
                Center(
                  // One measured column for the whole stack: the headline gets
                  // room to breathe and the button stops reaching the gutters.
                  child: ConstrainedBox(
                    constraints: const BoxConstraints(maxWidth: 328),
                    child: Column(
                      mainAxisSize: MainAxisSize.min,
                      crossAxisAlignment: CrossAxisAlignment.stretch,
                      children: [
                        const Center(child: _GlowingMark()),
                        const SizedBox(height: 32),
                        Text(
                          _headline,
                          style: AppTextStyles.h1.copyWith(
                            fontSize: 21,
                            height: 1.3,
                            fontWeight: FontWeight.w600,
                            color: AppColors.textHi,
                            letterSpacing: -0.2,
                          ),
                          textAlign: TextAlign.center,
                        ),
                        const SizedBox(height: 10),
                        Text(
                          _subtitle,
                          style: AppTextStyles.body.copyWith(
                            fontSize: 15,
                            color: AppColors.textDim,
                          ),
                          textAlign: TextAlign.center,
                        ),
                        const SizedBox(height: 42),
                        AuthPillButton(
                          key: const Key('face-id-login-button'),
                          label: 'Login with $_method',
                          leading: _isFace
                              ? const FaceIdGlyph(
                                  size: 23,
                                  color: AppColors.primary,
                                )
                              : const Icon(
                                  Icons.fingerprint,
                                  size: 23,
                                  color: AppColors.primary,
                                ),
                          busy: _busy,
                          onPressed: _attempt,
                        ),
                      ],
                    ),
                  ),
                ),

                // The escape hatch only appears once biometrics have actually
                // failed — on a normal relaunch the prompt just succeeds.
                if (_failed)
                  Align(
                    alignment: Alignment.bottomCenter,
                    child: TextButton(
                      onPressed: _busy ? null : _exitToLogin,
                      child: Text(
                        'Log out',
                        style: AppTextStyles.body.copyWith(
                          fontSize: 15,
                          color: AppColors.textDim,
                        ),
                      ),
                    ),
                  ),
              ],
            ),
          ),
        ),
      ),
    );
  }
}

// ── The app mark, lit from within ─────────────────────────────────────────────

/// The rounded app tile with a violet edge and a bloom behind it.
class _GlowingMark extends StatelessWidget {
  const _GlowingMark();

  static const double _size = 92;
  static const double _radius = 26;
  static const double _border = 1.3;

  @override
  Widget build(BuildContext context) {
    return Container(
      width: _size,
      height: _size,
      decoration: BoxDecoration(
        borderRadius: BorderRadius.circular(_radius),
        border: Border.all(
          color: AppColors.primary.withValues(alpha: 0.62),
          width: _border,
        ),
        boxShadow: [
          BoxShadow(
            color: AppColors.primary.withValues(alpha: 0.22),
            blurRadius: 44,
            spreadRadius: 4,
          ),
          BoxShadow(
            color: AppColors.primary.withValues(alpha: 0.30),
            blurRadius: 14,
          ),
        ],
      ),
      // Inset slightly so the artwork sits inside the violet edge rather than
      // butting against it.
      child: const Padding(
        padding: EdgeInsets.all(_border),
        child: AppLogo(size: _size - _border * 2, radius: _radius - _border),
      ),
    );
  }
}
