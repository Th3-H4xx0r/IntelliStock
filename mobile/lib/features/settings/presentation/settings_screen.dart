import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';
import '../../../core/lock/app_lock_controller.dart';
import '../../../core/lock/biometric_service.dart';
import '../../../core/network/api_config.dart';
import '../../../core/network/api_client.dart';
import '../../../core/network/session.dart';
import '../../../core/theme/app_colors.dart';
import '../../../core/theme/app_text_styles.dart';
import '../../../core/widgets/app_background.dart';
import '../../../core/widgets/app_toggle.dart';
import '../../../core/widgets/confirm_dialog.dart';
import '../../../core/widgets/glass_card.dart';
import '../../../core/widgets/material_symbols.dart';
import '../application/settings_controller.dart';

/// Full-screen settings page — pushed via `/settings`.
///
/// Scaffold + AppBackground (no shell nav). Sections are GlassCards.
class SettingsScreen extends ConsumerStatefulWidget {
  const SettingsScreen({super.key});

  @override
  ConsumerState<SettingsScreen> createState() => _SettingsScreenState();
}

class _SettingsScreenState extends ConsumerState<SettingsScreen> {
  bool _lockToggleBusy = false;

  // ── Biometric lock toggle ─────────────────────────────────────────────────

  Future<void> _handleLockToggle(bool newValue) async {
    if (_lockToggleBusy) return;
    setState(() => _lockToggleBusy = true);
    try {
      final ctrl = ref.read(settingsControllerProvider);
      if (newValue) {
        final ok = await ctrl.enableLock();
        if (!ok && mounted) {
          _showSnack('Biometric authentication failed or unavailable.');
        }
      } else {
        await ctrl.disableLock();
      }
    } finally {
      if (mounted) setState(() => _lockToggleBusy = false);
    }
  }

  // ── Timeout picker ────────────────────────────────────────────────────────

  Future<void> _showTimeoutPicker(BuildContext context) async {
    final current = ref.read(appLockControllerProvider).timeout;
    await showModalBottomSheet<void>(
      context: context,
      backgroundColor: AppColors.panel,
      shape: const RoundedRectangleBorder(
        borderRadius: BorderRadius.vertical(top: Radius.circular(20)),
      ),
      builder: (_) => _TimeoutPickerSheet(current: current),
    );
  }

  // ── Log out ───────────────────────────────────────────────────────────────

  Future<void> _handleLogout() async {
    if (!mounted) return;
    final confirmed = await showConfirmDialog(
      context,
      title: 'Log out',
      body: 'You will be signed out of IntelliStock. Your data stays on the server.',
      confirmLabel: 'Log out',
      confirmColor: AppColors.danger,
      icon: Icons.logout,
    );
    if (confirmed && mounted) {
      await ref.read(sessionProvider).clear();
    }
  }

  // ── Re-run onboarding ─────────────────────────────────────────────────────

  Future<void> _handleReRunOnboarding() async {
    if (!mounted) return;
    final confirmed = await showConfirmDialog(
      context,
      title: 'Re-run Onboarding',
      body: 'This will reset your onboarding state on the server. Continue?',
      confirmLabel: 'Reset & Re-run',
      confirmColor: AppColors.warning,
      icon: Icons.replay,
    );
    if (!confirmed || !mounted) return;

    try {
      await ref.read(apiClientProvider).post<dynamic>('/onboarding/reset');
      if (mounted) context.go('/onboarding');
    } catch (e) {
      if (mounted) _showSnack('Failed to reset onboarding: $e');
    }
  }

  void _showSnack(String msg) {
    if (!mounted) return;
    ScaffoldMessenger.of(context)
        .showSnackBar(SnackBar(content: Text(msg, style: AppTextStyles.body)));
  }

  // ── Build ─────────────────────────────────────────────────────────────────

  @override
  Widget build(BuildContext context) {
    final lockState = ref.watch(appLockControllerProvider);
    final session = ref.watch(sessionProvider);
    final versionAsync = ref.watch(appVersionProvider);
    final biometricsAvailable =
        ref.watch(_biometricsAvailableProvider);

    return Scaffold(
      backgroundColor: AppColors.canvas,
      body: AppBackground(
        child: SafeArea(
          child: CustomScrollView(
            slivers: [
              // ── App bar ────────────────────────────────────────────────
              SliverAppBar(
                backgroundColor: Colors.transparent,
                elevation: 0,
                leading: IconButton(
                  icon: Icon(symbol('arrow_back'),
                      color: AppColors.textMuted, size: 22),
                  onPressed: () => Navigator.of(context).pop(),
                ),
                title: Text('Settings', style: AppTextStyles.h3),
                centerTitle: false,
              ),

              // ── Body ───────────────────────────────────────────────────
              SliverPadding(
                padding: const EdgeInsets.fromLTRB(16, 0, 16, 32),
                sliver: SliverList(
                  delegate: SliverChildListDelegate([
                    // ── Security section ─────────────────────────────────
                    _SectionLabel(label: 'Security'),
                    const SizedBox(height: 8),
                    GlassCard(
                      padding: EdgeInsets.zero,
                      child: Column(
                        children: [
                          // Biometric lock toggle
                          _SettingsRow(
                            icon: symbol('lock'),
                            iconColor: AppColors.primary,
                            title: 'Biometric Lock',
                            subtitle: biometricsAvailable.when(
                              data: (ok) => ok
                                  ? 'Lock the app when you leave'
                                  : 'No biometrics enrolled on this device',
                              loading: () => 'Checking…',
                              error: (_, _) => 'Unavailable',
                            ),
                            trailing: biometricsAvailable.when(
                              data: (ok) => _lockToggleBusy
                                  ? const SizedBox(
                                      width: 20,
                                      height: 20,
                                      child: CircularProgressIndicator(
                                          strokeWidth: 2),
                                    )
                                  : AppToggle(
                                      value: lockState.enabled,
                                      onChanged:
                                          ok ? _handleLockToggle : null,
                                    ),
                              loading: () => const SizedBox(
                                width: 20,
                                height: 20,
                                child:
                                    CircularProgressIndicator(strokeWidth: 2),
                              ),
                              error: (_, _) =>
                                  AppToggle(value: false, onChanged: null),
                            ),
                          ),
                          const _Divider(),
                          // Auto-lock timeout
                          _SettingsRow(
                            icon: symbol('schedule'),
                            iconColor: AppColors.info,
                            title: 'Auto-lock after',
                            subtitle: 'Time before the app locks in background',
                            trailing: GestureDetector(
                              onTap: lockState.enabled
                                  ? () => _showTimeoutPicker(context)
                                  : null,
                              child: Row(
                                mainAxisSize: MainAxisSize.min,
                                children: [
                                  Text(
                                    lockState.timeout.label,
                                    style: AppTextStyles.meta.copyWith(
                                      color: lockState.enabled
                                          ? AppColors.primary
                                          : AppColors.textFaint,
                                    ),
                                  ),
                                  const SizedBox(width: 4),
                                  Icon(
                                    symbol('expand_more'),
                                    size: 16,
                                    color: lockState.enabled
                                        ? AppColors.primary
                                        : AppColors.textFaint,
                                  ),
                                ],
                              ),
                            ),
                          ),
                          const _Divider(),
                          // Info row
                          _SettingsRow(
                            icon: symbol('check_circle'),
                            iconColor: AppColors.success,
                            title: 'Require unlock on launch',
                            subtitle:
                                'Always prompt when the app is opened fresh',
                            trailing: Container(
                              padding: const EdgeInsets.symmetric(
                                  horizontal: 8, vertical: 3),
                              decoration: BoxDecoration(
                                color: AppColors.fill(AppColors.success),
                                borderRadius: BorderRadius.circular(6),
                                border: Border.all(
                                    color:
                                        AppColors.stroke(AppColors.success)),
                              ),
                              child: Text(
                                lockState.enabled ? 'ON' : 'OFF',
                                style: AppTextStyles.nano.copyWith(
                                  color: lockState.enabled
                                      ? AppColors.success
                                      : AppColors.textFaint,
                                  fontWeight: FontWeight.w700,
                                ),
                              ),
                            ),
                          ),
                        ],
                      ),
                    ),
                    const SizedBox(height: 24),

                    // ── Account section ───────────────────────────────────
                    _SectionLabel(label: 'Account'),
                    const SizedBox(height: 8),
                    GlassCard(
                      padding: EdgeInsets.zero,
                      child: Column(
                        children: [
                          // Username (read-only)
                          _SettingsRow(
                            icon: symbol('person'),
                            iconColor: AppColors.textMuted,
                            title: 'Signed in as',
                            subtitle: session.username,
                            trailing: null,
                          ),
                          const _Divider(),
                          // Log out
                          _SettingsRow(
                            icon: symbol('logout'),
                            iconColor: AppColors.danger,
                            title: 'Log out',
                            subtitle: 'Sign out of your account',
                            trailing: Icon(symbol('arrow_forward'),
                                size: 16, color: AppColors.textDim),
                            onTap: _handleLogout,
                          ),
                          const _Divider(),
                          // Re-run onboarding
                          _SettingsRow(
                            icon: symbol('replay'),
                            iconColor: AppColors.warning,
                            title: 'Re-run Onboarding',
                            subtitle:
                                'Reset and walk through setup again',
                            trailing: Icon(symbol('arrow_forward'),
                                size: 16, color: AppColors.textDim),
                            onTap: _handleReRunOnboarding,
                          ),
                        ],
                      ),
                    ),
                    const SizedBox(height: 24),

                    // ── About section ─────────────────────────────────────
                    _SectionLabel(label: 'About'),
                    const SizedBox(height: 8),
                    GlassCard(
                      padding: EdgeInsets.zero,
                      child: Column(
                        children: [
                          // App version
                          _SettingsRow(
                            icon: symbol('bolt'),
                            iconColor: AppColors.primary,
                            title: 'Version',
                            subtitle: versionAsync.when(
                              data: (v) => v,
                              loading: () => '…',
                              error: (_, _) => 'Unknown',
                            ),
                            trailing: null,
                          ),
                          const _Divider(),
                          // Backend endpoint
                          _SettingsRow(
                            icon: symbol('database'),
                            iconColor: AppColors.info,
                            title: 'Backend',
                            subtitle: ApiConfig.baseUrl,
                            trailing: null,
                          ),
                          const _Divider(),
                          // Licenses
                          _SettingsRow(
                            icon: symbol('check'),
                            iconColor: AppColors.success,
                            title: 'Open-source licenses',
                            subtitle: 'Third-party package licenses',
                            trailing: Icon(symbol('arrow_forward'),
                                size: 16, color: AppColors.textDim),
                            onTap: () => showLicensePage(
                              context: context,
                              applicationName: 'IntelliStock',
                              applicationVersion: versionAsync.value ?? '',
                            ),
                          ),
                        ],
                      ),
                    ),
                  ]),
                ),
              ),
            ],
          ),
        ),
      ),
    );
  }
}

// ── Private helper: check biometrics availability ─────────────────────────────

final _biometricsAvailableProvider = FutureProvider.autoDispose<bool>((ref) {
  return ref.read(biometricServiceProvider).canCheck();
});

// ── Timeout picker bottom sheet ───────────────────────────────────────────────

class _TimeoutPickerSheet extends ConsumerWidget {
  const _TimeoutPickerSheet({required this.current});
  final LockTimeout current;

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    return Padding(
      padding: const EdgeInsets.fromLTRB(20, 20, 20, 32),
      child: Column(
        mainAxisSize: MainAxisSize.min,
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Text('Auto-lock timeout', style: AppTextStyles.cardTitle),
          const SizedBox(height: 4),
          Text(
            'Lock the app after this much time in the background.',
            style: AppTextStyles.micro.copyWith(color: AppColors.textDim),
          ),
          const SizedBox(height: 16),
          ...LockTimeout.values.map((opt) => _TimeoutOption(
                option: opt,
                selected: opt == current,
                onTap: () async {
                  await ref
                      .read(appLockControllerProvider.notifier)
                      .setTimeout(opt);
                  if (context.mounted) Navigator.of(context).pop();
                },
              )),
        ],
      ),
    );
  }
}

class _TimeoutOption extends StatelessWidget {
  const _TimeoutOption({
    required this.option,
    required this.selected,
    required this.onTap,
  });

  final LockTimeout option;
  final bool selected;
  final VoidCallback onTap;

  @override
  Widget build(BuildContext context) {
    return InkWell(
      onTap: onTap,
      borderRadius: BorderRadius.circular(8),
      child: Container(
        padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 14),
        decoration: BoxDecoration(
          color: selected
              ? AppColors.fill(AppColors.primary)
              : Colors.transparent,
          borderRadius: BorderRadius.circular(8),
          border: selected
              ? Border.all(color: AppColors.stroke(AppColors.primary))
              : null,
        ),
        child: Row(
          children: [
            Expanded(
              child: Text(option.label, style: AppTextStyles.bodyHi),
            ),
            if (selected)
              Icon(symbol('check'), size: 18, color: AppColors.primary),
          ],
        ),
      ),
    );
  }
}

// ── Reusable row layout ───────────────────────────────────────────────────────

class _SettingsRow extends StatelessWidget {
  const _SettingsRow({
    required this.icon,
    required this.iconColor,
    required this.title,
    required this.subtitle,
    required this.trailing,
    this.onTap,
  });

  final IconData icon;
  final Color iconColor;
  final String title;
  final String? subtitle;
  final Widget? trailing;
  final VoidCallback? onTap;

  @override
  Widget build(BuildContext context) {
    final row = Padding(
      padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 14),
      child: Row(
        children: [
          Container(
            width: 36,
            height: 36,
            decoration: BoxDecoration(
              color: AppColors.fill(iconColor),
              borderRadius: BorderRadius.circular(10),
              border: Border.all(color: AppColors.stroke(iconColor)),
            ),
            child: Icon(icon, size: 18, color: iconColor),
          ),
          const SizedBox(width: 12),
          Expanded(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Text(title, style: AppTextStyles.bodyHi),
                if (subtitle != null && subtitle!.isNotEmpty) ...[
                  const SizedBox(height: 2),
                  Text(
                    subtitle!,
                    style:
                        AppTextStyles.micro.copyWith(color: AppColors.textDim),
                  ),
                ],
              ],
            ),
          ),
          if (trailing != null) ...[
            const SizedBox(width: 8),
            trailing!,
          ],
        ],
      ),
    );

    if (onTap == null) return row;
    return InkWell(
      onTap: onTap,
      borderRadius: BorderRadius.circular(16),
      child: row,
    );
  }
}

class _SectionLabel extends StatelessWidget {
  const _SectionLabel({required this.label});
  final String label;

  @override
  Widget build(BuildContext context) {
    return Padding(
      padding: const EdgeInsets.only(left: 4, bottom: 2),
      child: Text(
        label.toUpperCase(),
        style: AppTextStyles.eyebrow,
      ),
    );
  }
}

class _Divider extends StatelessWidget {
  const _Divider();

  @override
  Widget build(BuildContext context) {
    return Container(
      height: 1,
      margin: const EdgeInsets.only(left: 64),
      color: AppColors.border,
    );
  }
}
