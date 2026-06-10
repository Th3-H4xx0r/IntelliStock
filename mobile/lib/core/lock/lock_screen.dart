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
/// The router gates the entire navigation tree behind this screen:
///   if (ref.watch(appLockControllerProvider).locked) return LockScreen();
///
/// Shows:
///  • branded dark background with logo + lock icon
///  • "Unlock with Face ID / Biometrics" primary button
///  • retry message on failure
///  • "Log out" text link after repeated failures (clears session)
class LockScreen extends ConsumerStatefulWidget {
  const LockScreen({super.key});

  @override
  ConsumerState<LockScreen> createState() => _LockScreenState();
}

class _LockScreenState extends ConsumerState<LockScreen> {
  bool _busy = false;
  bool _failed = false;
  int _failCount = 0;

  /// Threshold after which "Log out" appears.
  static const _failThreshold = 2;

  Future<void> _unlock() async {
    if (_busy) return;
    setState(() {
      _busy = true;
      _failed = false;
    });
    final ok =
        await ref.read(appLockControllerProvider.notifier).unlock();
    if (mounted) {
      setState(() {
        _busy = false;
        if (!ok) {
          _failed = true;
          _failCount++;
        }
      });
    }
  }

  Future<void> _logout() async {
    await ref.read(sessionProvider).clear();
    // The router's redirect logic will navigate to /login once session is
    // cleared; AppLockController.build() will disable lock on next app start.
    await ref.read(appLockControllerProvider.notifier).disable();
  }

  String _unlockLabel(List<dynamic> types) {
    // types is List<BiometricType> from local_auth
    final hasFace = types.any((t) => t.toString().contains('face'));
    if (hasFace) return 'Unlock with Face ID';
    final hasFingerprint =
        types.any((t) => t.toString().contains('fingerprint'));
    if (hasFingerprint) return 'Unlock with Touch ID';
    return 'Unlock with Biometrics';
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
                  // ── Logo ────────────────────────────────────────────────
                  _LogoMark(),
                  const SizedBox(height: 32),

                  // ── Lock icon ────────────────────────────────────────────
                  Container(
                    width: 72,
                    height: 72,
                    decoration: BoxDecoration(
                      color: AppColors.fill(AppColors.primary),
                      shape: BoxShape.circle,
                      border:
                          Border.all(color: AppColors.stroke(AppColors.primary)),
                    ),
                    child: Icon(
                      symbol('lock'),
                      size: 34,
                      color: AppColors.primary,
                    ),
                  ),
                  const SizedBox(height: 24),

                  // ── Title + subtitle ─────────────────────────────────────
                  Text(
                    'IntelliStock Locked',
                    style: AppTextStyles.h2
                        .copyWith(color: AppColors.textHi),
                    textAlign: TextAlign.center,
                  ),
                  const SizedBox(height: 8),
                  Text(
                    'Verify your identity to continue.',
                    style:
                        AppTextStyles.body.copyWith(color: AppColors.textDim),
                    textAlign: TextAlign.center,
                  ),
                  const SizedBox(height: 32),

                  // ── Failure message ──────────────────────────────────────
                  if (_failed) ...[
                    Container(
                      padding: const EdgeInsets.symmetric(
                          horizontal: 16, vertical: 10),
                      decoration: BoxDecoration(
                        color: AppColors.fill(AppColors.danger),
                        borderRadius: BorderRadius.circular(8),
                        border: Border.all(
                            color: AppColors.stroke(AppColors.danger)),
                      ),
                      child: Text(
                        'Authentication failed. Please try again.',
                        style: AppTextStyles.meta
                            .copyWith(color: AppColors.danger),
                        textAlign: TextAlign.center,
                      ),
                    ),
                    const SizedBox(height: 20),
                  ],

                  // ── Unlock button ────────────────────────────────────────
                  _BiometricUnlockButton(
                    busy: _busy,
                    onUnlock: _unlock,
                    labelBuilder: _unlockLabel,
                  ),

                  // ── Log out (after repeated failures) ────────────────────
                  if (_failCount >= _failThreshold) ...[
                    const SizedBox(height: 20),
                    TextButton(
                      onPressed: _logout,
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
            child: Text(
              'I',
              style: TextStyle(
                color: Colors.white,
                fontSize: 18,
                fontWeight: FontWeight.w800,
              ),
            ),
          ),
        ),
        const SizedBox(width: 10),
        Text(
          'IntelliStock',
          style: AppTextStyles.h2.copyWith(
            color: AppColors.textHi,
            letterSpacing: -0.3,
          ),
        ),
      ],
    );
  }
}

// ── Biometric unlock button (reads available types) ───────────────────────────

class _BiometricUnlockButton extends ConsumerWidget {
  const _BiometricUnlockButton({
    required this.busy,
    required this.onUnlock,
    required this.labelBuilder,
  });

  final bool busy;
  final VoidCallback onUnlock;
  final String Function(List<dynamic>) labelBuilder;

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    // FutureBuilder to get available biometric types for the label.
    return FutureBuilder<List<dynamic>>(
      future: ref.read(biometricServiceProvider).availableTypes(),
      builder: (_, snap) {
        final label = labelBuilder(snap.data ?? []);
        return SizedBox(
          width: double.infinity,
          child: AppButton.primary(
            label: label,
            icon: symbol('lock'),
            busy: busy,
            onPressed: busy ? null : onUnlock,
          ),
        );
      },
    );
  }
}
