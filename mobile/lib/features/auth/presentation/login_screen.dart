import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import '../../../core/theme/app_colors.dart';
import '../../../core/theme/app_text_styles.dart';
import '../../../core/widgets/app_background.dart';
import '../../../core/widgets/glass_card.dart';
import '../../../core/widgets/material_symbols.dart';
import '../application/auth_controller.dart';
import 'particle_field.dart';

/// Full-screen login page, pixel-matching the web LoginView.vue.
///
/// Route: /login
///
/// The router watches [sessionProvider] (a [ChangeNotifier]); on successful
/// login the session fires and GoRouter auto-redirects.  A `?redirect=` query
/// param is handled by the router's own redirect callback (which already
/// encodes the safe-redirect logic), so this screen needs no extra handling.
class LoginScreen extends ConsumerStatefulWidget {
  const LoginScreen({super.key, this.redirectPath});

  /// Decoded redirect path from the `?redirect=` query param (unused directly
  /// here — the router's redirect callback handles navigation).
  final String? redirectPath;

  @override
  ConsumerState<LoginScreen> createState() => _LoginScreenState();
}

class _LoginScreenState extends ConsumerState<LoginScreen> {
  final _usernameCtrl = TextEditingController();
  final _passwordCtrl = TextEditingController();
  bool _showPassword = false;
  String? _localError;

  @override
  void dispose() {
    _usernameCtrl.dispose();
    _passwordCtrl.dispose();
    super.dispose();
  }

  Future<void> _submit() async {
    final username = _usernameCtrl.text.trim();
    final password = _passwordCtrl.text;

    // Local validation — mirrors the web "please enter username and password".
    if (username.isEmpty || password.isEmpty) {
      setState(() => _localError = 'Please enter your username and password.');
      return;
    }

    setState(() => _localError = null);
    await ref.read(loginControllerProvider.notifier).login(username, password);
    // On success GoRouter redirects automatically (sessionProvider notified).
  }

  void _clearErrors() {
    if (_localError != null) setState(() => _localError = null);
    ref.read(loginControllerProvider.notifier).clearError();
  }

  @override
  Widget build(BuildContext context) {
    final loginState = ref.watch(loginControllerProvider);
    final errorMessage = loginState.errorMessage ?? _localError;

    return Scaffold(
      backgroundColor: AppColors.canvas,
      body: AppBackground(
        child: SafeArea(
          child: Stack(
            fit: StackFit.expand,
            children: [
              // Particle canvas at 60% opacity behind everything.
              const Opacity(opacity: 0.6, child: ParticleField()),

              // Scrollable centred content.
              SingleChildScrollView(
                padding: const EdgeInsets.symmetric(horizontal: 24),
                child: Column(
                  children: [
                    const SizedBox(height: 64),

                    // ── "Private Access Only" badge + heading ─────────
                    _buildHeader(),

                    const SizedBox(height: 32),

                    // ── Glass form card ───────────────────────────────
                    Center(
                      child: ConstrainedBox(
                        constraints: const BoxConstraints(maxWidth: 360),
                        child: GlassCard(
                          borderRadius: 20,
                          padding: const EdgeInsets.all(28),
                          child: Column(
                            crossAxisAlignment: CrossAxisAlignment.stretch,
                            children: [
                              // Error banner (animated in/out).
                              AnimatedSwitcher(
                                duration: const Duration(milliseconds: 200),
                                child: errorMessage != null
                                    ? _ErrorBanner(
                                        key: const ValueKey('err'),
                                        message: errorMessage,
                                      )
                                    : const SizedBox.shrink(key: ValueKey('no-err')),
                              ),
                              if (errorMessage != null) const SizedBox(height: 16),

                              // Username.
                              const _FieldLabel(label: 'Username'),
                              const SizedBox(height: 6),
                              _AppTextField(
                                controller: _usernameCtrl,
                                hint: 'your username',
                                prefixIcon: symbol('person'),
                                keyboardType: TextInputType.text,
                                textInputAction: TextInputAction.next,
                                autofillHints: const [AutofillHints.username],
                                enabled: !loginState.isLoading,
                                onChanged: (_) => _clearErrors(),
                              ),

                              const SizedBox(height: 16),

                              // Password.
                              const _FieldLabel(label: 'Password'),
                              const SizedBox(height: 6),
                              _AppTextField(
                                controller: _passwordCtrl,
                                hint: '••••••••',
                                prefixIcon: symbol('lock'),
                                obscureText: !_showPassword,
                                textInputAction: TextInputAction.done,
                                autofillHints: const [AutofillHints.password],
                                enabled: !loginState.isLoading,
                                onChanged: (_) => _clearErrors(),
                                onSubmitted: (_) => _submit(),
                                suffix: GestureDetector(
                                  behavior: HitTestBehavior.opaque,
                                  onTap: loginState.isLoading
                                      ? null
                                      : () => setState(
                                            () => _showPassword = !_showPassword,
                                          ),
                                  child: Padding(
                                    padding: const EdgeInsets.all(4),
                                    child: Icon(
                                      _showPassword
                                          ? symbol('visibility_off')
                                          : symbol('visibility'),
                                      size: 20,
                                      color: AppColors.textFaint,
                                    ),
                                  ),
                                ),
                              ),

                              const SizedBox(height: 20),

                              // Full-width Sign In button.
                              _SignInButton(
                                busy: loginState.isLoading,
                                onPressed:
                                    loginState.isLoading ? null : _submit,
                              ),
                            ],
                          ),
                        ),
                      ),
                    ),

                    const SizedBox(height: 24),

                    // Footer.
                    Text(
                      'Access is by invitation only. Contact your administrator'
                      ' if you need an account.',
                      style: AppTextStyles.nano
                          .copyWith(color: AppColors.textFaint),
                      textAlign: TextAlign.center,
                    ),

                    const SizedBox(height: 48),
                  ],
                ),
              ),
            ],
          ),
        ),
      ),
    );
  }

  Widget _buildHeader() {
    return Column(
      children: [
        // Lock pill.
        Container(
          padding:
              const EdgeInsets.symmetric(horizontal: 16, vertical: 6),
          decoration: BoxDecoration(
            color: AppColors.surface.withValues(alpha: 0.6),
            borderRadius: BorderRadius.circular(99),
            border: Border.all(color: AppColors.border),
          ),
          child: Row(
            mainAxisSize: MainAxisSize.min,
            children: [
              Icon(symbol('lock'), color: AppColors.primary, size: 16),
              const SizedBox(width: 6),
              Text(
                'PRIVATE ACCESS ONLY',
                style: AppTextStyles.micro.copyWith(
                  color: AppColors.textMuted,
                  fontWeight: FontWeight.w700,
                  letterSpacing: 1.4,
                ),
              ),
            ],
          ),
        ),
        const SizedBox(height: 20),
        Text('Welcome back.', style: AppTextStyles.h1),
        const SizedBox(height: 8),
        Text(
          'IntelliStock is invite-only. Sign in with your credentials.',
          style: AppTextStyles.body.copyWith(color: AppColors.textMuted),
          textAlign: TextAlign.center,
        ),
      ],
    );
  }
}

// ── Sub-widgets ───────────────────────────────────────────────────────────────

class _FieldLabel extends StatelessWidget {
  const _FieldLabel({required this.label});
  final String label;

  @override
  Widget build(BuildContext context) {
    return Text(
      label.toUpperCase(),
      style: AppTextStyles.micro.copyWith(
        color: AppColors.textMuted,
        fontWeight: FontWeight.w600,
        letterSpacing: 1.2,
      ),
    );
  }
}

/// Styled text field matching the web's dark rounded inputs.
class _AppTextField extends StatelessWidget {
  const _AppTextField({
    required this.controller,
    required this.hint,
    required this.prefixIcon,
    this.obscureText = false,
    this.keyboardType,
    this.textInputAction,
    this.autofillHints,
    this.enabled = true,
    this.onChanged,
    this.onSubmitted,
    this.suffix,
  });

  final TextEditingController controller;
  final String hint;
  final IconData prefixIcon;
  final bool obscureText;
  final TextInputType? keyboardType;
  final TextInputAction? textInputAction;
  final Iterable<String>? autofillHints;
  final bool enabled;
  final ValueChanged<String>? onChanged;
  final ValueChanged<String>? onSubmitted;
  final Widget? suffix;

  @override
  Widget build(BuildContext context) {
    return Opacity(
      opacity: enabled ? 1.0 : 0.5,
      child: Container(
        decoration: BoxDecoration(
          color: AppColors.surface.withValues(alpha: 0.7),
          borderRadius: BorderRadius.circular(12),
          border: Border.all(color: AppColors.border),
        ),
        child: TextField(
          controller: controller,
          obscureText: obscureText,
          keyboardType: keyboardType,
          textInputAction: textInputAction,
          autofillHints: autofillHints,
          enabled: enabled,
          onChanged: onChanged,
          onSubmitted: onSubmitted,
          style: AppTextStyles.body.copyWith(color: AppColors.textHi),
          decoration: InputDecoration(
            hintText: hint,
            hintStyle:
                AppTextStyles.body.copyWith(color: AppColors.textFaint),
            prefixIcon: Padding(
              padding: const EdgeInsets.only(left: 14, right: 8),
              child: Icon(prefixIcon, size: 20, color: AppColors.textFaint),
            ),
            prefixIconConstraints:
                const BoxConstraints(minWidth: 0, minHeight: 0),
            suffixIcon: suffix != null
                ? Padding(
                    padding: const EdgeInsets.only(right: 14),
                    child: suffix,
                  )
                : null,
            suffixIconConstraints:
                const BoxConstraints(minWidth: 0, minHeight: 0),
            border: InputBorder.none,
            contentPadding: const EdgeInsets.symmetric(
                horizontal: 14, vertical: 14),
          ),
        ),
      ),
    );
  }
}

/// Red error banner matching web `bg-red-500/10 border-red-500/20`.
class _ErrorBanner extends StatelessWidget {
  const _ErrorBanner({super.key, required this.message});
  final String message;

  @override
  Widget build(BuildContext context) {
    return Container(
      padding:
          const EdgeInsets.symmetric(horizontal: 14, vertical: 12),
      decoration: BoxDecoration(
        color: AppColors.fill(AppColors.danger),
        borderRadius: BorderRadius.circular(12),
        border: Border.all(color: AppColors.stroke(AppColors.danger)),
      ),
      child: Row(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          const Icon(Icons.error_outline, color: AppColors.danger, size: 18),
          const SizedBox(width: 10),
          Expanded(
            child: Text(
              message,
              style: AppTextStyles.body.copyWith(color: AppColors.danger),
            ),
          ),
        ],
      ),
    );
  }
}

/// Full-width violet Sign In button with busy spinner.
/// Uses Material+InkWell directly for full-width control (AppButton.primary
/// is MainAxisSize.min and we want width: double.infinity).
class _SignInButton extends StatelessWidget {
  const _SignInButton({required this.busy, this.onPressed});
  final bool busy;
  final VoidCallback? onPressed;

  @override
  Widget build(BuildContext context) {
    return Material(
      color: busy
          ? AppColors.primary.withValues(alpha: 0.6)
          : AppColors.primary,
      borderRadius: BorderRadius.circular(12),
      child: InkWell(
        borderRadius: BorderRadius.circular(12),
        onTap: onPressed,
        child: Padding(
          padding: const EdgeInsets.symmetric(vertical: 14),
          child: Row(
            mainAxisAlignment: MainAxisAlignment.center,
            children: [
              if (busy) ...[
                SizedBox(
                  width: 18,
                  height: 18,
                  child: CircularProgressIndicator(
                    strokeWidth: 2,
                    color: AppColors.onPrimary,
                  ),
                ),
                const SizedBox(width: 10),
                Text(
                  'Signing in…',
                  style: AppTextStyles.cardTitle
                      .copyWith(color: AppColors.onPrimary),
                ),
              ] else ...[
                Text(
                  'Sign In',
                  style: AppTextStyles.cardTitle
                      .copyWith(color: AppColors.onPrimary),
                ),
                const SizedBox(width: 8),
                Icon(Icons.arrow_forward,
                    size: 18, color: AppColors.onPrimary),
              ],
            ],
          ),
        ),
      ),
    );
  }
}
