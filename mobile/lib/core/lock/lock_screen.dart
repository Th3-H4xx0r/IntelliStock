import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import '../network/session.dart';
import '../theme/app_colors.dart';
import '../theme/app_text_styles.dart';
import '../widgets/app_background.dart';
import '../widgets/app_button.dart';
import '../widgets/material_symbols.dart';
import 'app_lock_controller.dart';
import 'biometric_service.dart';

/// Full-screen lock gate shown when [AppLockState.locked] is true.
///
/// Behaviour:
///  • Auto-prompts Face ID/biometrics the moment it appears, and again whenever
///    the app returns to the foreground — so the user never has to tap to unlock
///    on a normal relaunch.
///  • If biometrics fail or are cancelled, it reveals fallback options:
///    "Unlock with Face ID" (retry), "Log in with password", and "Log out".
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

  @override
  void initState() {
    super.initState();
    WidgetsBinding.instance.addObserver(this);
    // Auto-prompt as soon as the lock screen is shown.
    WidgetsBinding.instance.addPostFrameCallback((_) => _attempt());
  }

  @override
  void dispose() {
    WidgetsBinding.instance.removeObserver(this);
    super.dispose();
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

  /// Leave the locked session via the username/password login (keeps the
  /// biometric-lock SETTING enabled for next launch).
  Future<void> _exitToLogin() async {
    final lockCtrl = ref.read(appLockControllerProvider.notifier);
    final session = ref.read(sessionProvider);
    lockCtrl.releaseLock(); // open the gate so the router can show /login
    await session.clear(); // → router redirects to /login
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      backgroundColor: AppColors.canvas,
      body: AppBackground(
        child: SafeArea(
          child: Center(
            child: Padding(
              padding: const EdgeInsets.symmetric(horizontal: 40),
              child: Column(
                mainAxisAlignment: MainAxisAlignment.center,
                children: [
                  _LogoMark(),
                  const SizedBox(height: 32),
                  Container(
                    width: 72,
                    height: 72,
                    decoration: BoxDecoration(
                      color: AppColors.fill(AppColors.primary),
                      shape: BoxShape.circle,
                      border: Border.all(
                          color: AppColors.stroke(AppColors.primary)),
                    ),
                    child: Icon(symbol('lock'),
                        size: 34, color: AppColors.primary),
                  ),
                  const SizedBox(height: 24),
                  Text(
                    'IntelliStock Locked',
                    style: AppTextStyles.h2.copyWith(color: AppColors.textHi),
                    textAlign: TextAlign.center,
                  ),
                  const SizedBox(height: 8),
                  Text(
                    _busy
                        ? 'Authenticating…'
                        : (_failed
                            ? 'Authentication failed. Choose an option below.'
                            : 'Verify your identity to continue.'),
                    style: AppTextStyles.body.copyWith(
                        color: _failed ? AppColors.danger : AppColors.textDim),
                    textAlign: TextAlign.center,
                  ),
                  const SizedBox(height: 32),

                  // Primary: (auto-runs) Unlock with Face ID — also a retry.
                  _BiometricUnlockButton(busy: _busy, onUnlock: _attempt),

                  // Fallback options once biometrics fail / are cancelled.
                  if (_failed) ...[
                    const SizedBox(height: 12),
                    SizedBox(
                      width: double.infinity,
                      child: AppButton.ghost(
                        label: 'Log in with password',
                        icon: symbol('person'),
                        onPressed: _busy ? null : _exitToLogin,
                      ),
                    ),
                    const SizedBox(height: 16),
                    TextButton(
                      onPressed: _busy ? null : _exitToLogin,
                      child: Text(
                        'Log out',
                        style: AppTextStyles.body
                            .copyWith(color: AppColors.textDim),
                      ),
                    ),
                  ],
                ],
              ),
            ),
          ),
        ),
      ),
    );
  }
}

// ── Logo mark ─────────────────────────────────────────────────────────────────

class _LogoMark extends StatelessWidget {
  @override
  Widget build(BuildContext context) {
    return Row(
      mainAxisSize: MainAxisSize.min,
      children: [
        Container(
          width: 32,
          height: 32,
          decoration: BoxDecoration(
            gradient: const LinearGradient(
              begin: Alignment.topLeft,
              end: Alignment.bottomRight,
              colors: [Color(0xFF7C3AED), Color(0xFFA78BFA)],
            ),
            borderRadius: BorderRadius.circular(8),
          ),
          child: const Center(
            child: Text('I',
                style: TextStyle(
                    color: Colors.white,
                    fontSize: 18,
                    fontWeight: FontWeight.w800)),
          ),
        ),
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

// ── Biometric unlock button (reads available types for the label) ─────────────

class _BiometricUnlockButton extends ConsumerWidget {
  const _BiometricUnlockButton({required this.busy, required this.onUnlock});

  final bool busy;
  final VoidCallback onUnlock;

  String _label(List<dynamic> types) {
    if (types.any((t) => t.toString().contains('face'))) {
      return 'Unlock with Face ID';
    }
    if (types.any((t) => t.toString().contains('fingerprint'))) {
      return 'Unlock with Touch ID';
    }
    return 'Unlock';
  }

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    return FutureBuilder<List<dynamic>>(
      future: ref.read(biometricServiceProvider).availableTypes(),
      builder: (_, snap) {
        return SizedBox(
          width: double.infinity,
          child: AppButton.primary(
            label: _label(snap.data ?? const []),
            icon: symbol('lock'),
            busy: busy,
            onPressed: busy ? null : onUnlock,
          ),
        );
      },
    );
  }
}
