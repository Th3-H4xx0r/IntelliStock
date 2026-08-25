import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import '../../../core/theme/app_colors.dart';
import '../../../core/theme/app_text_styles.dart';
import '../../../core/lock/app_lock_controller.dart';
import '../../../core/lock/biometric_service.dart';
import '../../../core/widgets/app_background.dart';
import '../../../core/widgets/auth_pill_button.dart';
import '../../../core/widgets/face_id_glyph.dart';
import '../../../core/widgets/glass_card.dart';
import '../../../core/widgets/material_symbols.dart';
import '../application/auth_controller.dart';
import 'login_coin.dart';

/// Full-screen secure login page for the native mobile app.
///
/// Route: /login
///
/// The router watches [sessionProvider] (a [ChangeNotifier]); on successful
/// login the session fires and GoRouter auto-redirects.  A `?redirect=` query
/// param is handled by the router's own redirect callback (which already
/// encodes the safe-redirect logic), so this screen needs no extra handling.
///
/// The form sits on the app's signature frosted glass, lit from behind by a
/// violet bloom — the same material the rest of the app is built from, and
/// what gives the page an anchor now that it carries no mark. Type scale and
/// pill are shared with the lock screen so the two read as one flow.
class LoginScreen extends ConsumerStatefulWidget {
  const LoginScreen({super.key, this.redirectPath});

  /// Decoded redirect path from the `?redirect=` query param (unused directly
  /// here — the router's redirect callback handles navigation).
  final String? redirectPath;

  @override
  ConsumerState<LoginScreen> createState() => _LoginScreenState();
}

/// The shared measure of the auth flow. Kept in step with the lock screen.
const double _columnWidth = 340;

/// How long the success sequence holds the screen before the router takes
/// over. Matches the model's `Success` clip (1.7s) with a beat to breathe.
const Duration _successHold = Duration(milliseconds: 1850);

class _LoginScreenState extends ConsumerState<LoginScreen> {
  final _usernameCtrl = TextEditingController();
  final _passwordCtrl = TextEditingController();
  bool _showPassword = false;
  String? _localError;

  /// Null until resolved. False hides the biometric row altogether —
  /// offering Face ID on a device that has none is a dead control.
  bool? _bioAvailable;
  String _bioMethod = 'Face ID';
  bool _bioIsFace = true;
  bool _bioBusy = false;

  @override
  void initState() {
    super.initState();
    _resolveBiometrics();
  }

  Future<void> _resolveBiometrics() async {
    try {
      final service = ref.read(biometricServiceProvider);
      final can = await service.canCheck();
      final types = await service.availableTypes();
      final joined = types.map((t) => t.toString()).join(',');
      final face = joined.contains('face');
      if (!mounted) return;
      setState(() {
        _bioAvailable = can;
        _bioIsFace = face;
        _bioMethod = face
            ? 'Face ID'
            : (joined.contains('fingerprint') ? 'Touch ID' : 'biometrics');
      });
    } catch (_) {
      if (mounted) setState(() => _bioAvailable = false);
    }
  }

  /// Turning it on authenticates the device owner first, so it works here
  /// without a session — the preference simply arms the lock for the next
  /// sign-in. The gate itself never rises without a session.
  Future<void> _toggleBiometricLock(bool want) async {
    if (_bioBusy) return;
    setState(() => _bioBusy = true);
    final lock = ref.read(appLockControllerProvider.notifier);
    var failed = false;
    if (want) {
      failed = !await lock.enable();
    } else {
      await lock.disable();
    }
    if (!mounted) return;
    setState(() {
      _bioBusy = false;
      if (failed) _localError = 'Could not turn on $_bioMethod.';
    });
  }

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
    // The hold keeps this screen mounted through the coin's flip; the
    // router redirects the moment the session lands.
    await ref
        .read(loginControllerProvider.notifier)
        .login(username, password, holdBeforeCommit: _successHold);
  }

  void _clearErrors() {
    if (_localError != null) setState(() => _localError = null);
    ref.read(loginControllerProvider.notifier).clearError();
  }

  @override
  Widget build(BuildContext context) {
    final loginState = ref.watch(loginControllerProvider);
    final errorMessage = loginState.errorMessage ?? _localError;
    final succeeded = loginState.succeeded;
    final busy = loginState.isLoading && !succeeded;
    final phase = succeeded
        ? CoinPhase.success
        : (busy ? CoinPhase.working : CoinPhase.idle);

    return Scaffold(
      backgroundColor: AppColors.canvas,
      // Tapping the background gives up focus, so the keyboard drops
      // instead of sitting there until Done is pressed.
      body: GestureDetector(
        behavior: HitTestBehavior.translucent,
        onTap: () => FocusScope.of(context).unfocus(),
        child: AppBackground(
          child: Stack(
            children: [
              // Ambient violet light behind the card. Without something to bloom
              // through, frosted glass over flat black is just a grey rectangle.
              const _CardBloom(),

              SafeArea(
                child: Padding(
                  padding: const EdgeInsets.fromLTRB(24, 24, 24, 20),
                  // Centred when it fits, scrolling once the keyboard is up.
                  child: LayoutBuilder(
                    builder: (context, constraints) => SingleChildScrollView(
                      // The success coin scales beyond its slot; without
                      // this the scroll view would crop it.
                      clipBehavior: Clip.none,
                      child: ConstrainedBox(
                        constraints: BoxConstraints(
                          minHeight: constraints.maxHeight,
                        ),
                        // Sits above centre: the coin needs headroom and the
                        // form reads better high on the screen than floating
                        // in the middle of it.
                        child: Align(
                          alignment: const Alignment(0, -0.42),
                          child: ConstrainedBox(
                            constraints: const BoxConstraints(
                              maxWidth: _columnWidth,
                            ),
                            child: Column(
                              mainAxisSize: MainAxisSize.min,
                              crossAxisAlignment: CrossAxisAlignment.stretch,
                              children: [
                                LoginCoin(phase: phase),
                                // Everything below the coin collapses on
                                // success. Because the column is centred,
                                // that lifts the coin to the middle of the
                                // screen without measuring anything.
                                AnimatedSize(
                                  duration: const Duration(milliseconds: 620),
                                  curve: Curves.easeOutCubic,
                                  alignment: Alignment.topCenter,
                                  child: succeeded
                                      ? const SizedBox(width: double.infinity)
                                      : Column(
                                          mainAxisSize: MainAxisSize.min,
                                          crossAxisAlignment:
                                              CrossAxisAlignment.stretch,
                                          children: [
                                            const SizedBox(height: 20),
                                            AnimatedOpacity(
                                              opacity: succeeded ? 0 : 1,
                                              duration: const Duration(
                                                milliseconds: 420,
                                              ),
                                              curve: Curves.easeOut,
                                              child: Text(
                                                'Welcome back',
                                                style: AppTextStyles.h1
                                                    .copyWith(
                                                      fontSize: 24,
                                                      height: 1.25,
                                                      fontWeight:
                                                          FontWeight.w600,
                                                      color: AppColors.textHi,
                                                      letterSpacing: -0.4,
                                                    ),
                                                textAlign: TextAlign.center,
                                              ),
                                            ),
                                            const SizedBox(height: 32),

                                            AnimatedOpacity(
                                              opacity: succeeded ? 0 : 1,
                                              duration: const Duration(
                                                milliseconds: 520,
                                              ),
                                              curve: Curves.easeOutCubic,
                                              child: AnimatedSlide(
                                                offset: succeeded
                                                    ? const Offset(0, 0.10)
                                                    : Offset.zero,
                                                duration: const Duration(
                                                  milliseconds: 520,
                                                ),
                                                curve: Curves.easeOutCubic,
                                                child: GlassCard(
                                                  frosted: true,
                                                  borderRadius: 26,
                                                  padding:
                                                      const EdgeInsets.fromLTRB(
                                                        18,
                                                        20,
                                                        18,
                                                        18,
                                                      ),
                                                  child: Column(
                                                    crossAxisAlignment:
                                                        CrossAxisAlignment
                                                            .stretch,
                                                    children: [
                                                      // Error banner (animated in/out).
                                                      AnimatedSwitcher(
                                                        duration:
                                                            const Duration(
                                                              milliseconds: 200,
                                                            ),
                                                        child:
                                                            errorMessage != null
                                                            ? _ErrorBanner(
                                                                key:
                                                                    const ValueKey(
                                                                      'err',
                                                                    ),
                                                                message:
                                                                    errorMessage,
                                                              )
                                                            : const SizedBox.shrink(
                                                                key: ValueKey(
                                                                  'no-err',
                                                                ),
                                                              ),
                                                      ),
                                                      if (errorMessage != null)
                                                        const SizedBox(
                                                          height: 14,
                                                        ),

                                                      // The placeholder carries the field name,
                                                      // so there are no labels above the inputs.
                                                      _AppTextField(
                                                        controller:
                                                            _usernameCtrl,
                                                        hint: 'Username',
                                                        prefixIcon: symbol(
                                                          'person',
                                                        ),
                                                        keyboardType:
                                                            TextInputType.text,
                                                        textInputAction:
                                                            TextInputAction
                                                                .next,
                                                        autofillHints: const [
                                                          AutofillHints
                                                              .username,
                                                        ],
                                                        enabled: !busy,
                                                        onChanged: (_) =>
                                                            _clearErrors(),
                                                      ),
                                                      const SizedBox(
                                                        height: 11,
                                                      ),
                                                      _AppTextField(
                                                        controller:
                                                            _passwordCtrl,
                                                        hint: 'Password',
                                                        prefixIcon: symbol(
                                                          'lock',
                                                        ),
                                                        obscureText:
                                                            !_showPassword,
                                                        textInputAction:
                                                            TextInputAction
                                                                .done,
                                                        autofillHints: const [
                                                          AutofillHints
                                                              .password,
                                                        ],
                                                        enabled: !busy,
                                                        onChanged: (_) =>
                                                            _clearErrors(),
                                                        onSubmitted: (_) =>
                                                            _submit(),
                                                        suffix: GestureDetector(
                                                          behavior:
                                                              HitTestBehavior
                                                                  .opaque,
                                                          onTap: busy
                                                              ? null
                                                              : () => setState(
                                                                  () => _showPassword =
                                                                      !_showPassword,
                                                                ),
                                                          child: Padding(
                                                            padding:
                                                                const EdgeInsets.all(
                                                                  4,
                                                                ),
                                                            child: Icon(
                                                              _showPassword
                                                                  ? symbol(
                                                                      'visibility_off',
                                                                    )
                                                                  : symbol(
                                                                      'visibility',
                                                                    ),
                                                              size: 19,
                                                              color: AppColors
                                                                  .textDim,
                                                            ),
                                                          ),
                                                        ),
                                                      ),
                                                      const SizedBox(
                                                        height: 18,
                                                      ),
                                                      AuthPillButton(
                                                        label: 'Sign In',
                                                        busyLabel:
                                                            'Signing in\u2026',
                                                        busy: busy,
                                                        // Slighter than the lock screen's
                                                        // unlock pill: this one sits inside
                                                        // a card, not alone on the page.
                                                        height: 52,
                                                        onPressed: _submit,
                                                      ),
                                                      if (_bioAvailable ==
                                                          true) ...[
                                                        const SizedBox(
                                                          height: 16,
                                                        ),
                                                        Divider(
                                                          height: 1,
                                                          color: AppColors
                                                              .primary
                                                              .withValues(
                                                                alpha: 0.26,
                                                              ),
                                                        ),
                                                        const SizedBox(
                                                          height: 12,
                                                        ),
                                                        _BiometricToggle(
                                                          method: _bioMethod,
                                                          isFace: _bioIsFace,
                                                          value: ref
                                                              .watch(
                                                                appLockControllerProvider,
                                                              )
                                                              .enabled,
                                                          busy:
                                                              _bioBusy || busy,
                                                          onChanged:
                                                              _toggleBiometricLock,
                                                        ),
                                                      ],
                                                    ],
                                                  ),
                                                ),
                                              ),
                                            ),
                                          ],
                                        ),
                                ),
                              ],
                            ),
                          ),
                        ),
                      ),
                    ),
                  ),
                ),
              ),
            ],
          ),
        ),
      ),
    );
  }
}

/// Arms the biometric lock for the next sign-in. Lives here as well as in
/// Settings so it can be turned on before there is a session to protect.
class _BiometricToggle extends StatelessWidget {
  const _BiometricToggle({
    required this.method,
    required this.isFace,
    required this.value,
    required this.busy,
    required this.onChanged,
  });

  final String method;
  final bool isFace;
  final bool value;
  final bool busy;
  final ValueChanged<bool> onChanged;

  @override
  Widget build(BuildContext context) {
    return Opacity(
      opacity: busy ? 0.5 : 1,
      child: Row(
        children: [
          SizedBox(
            width: 26,
            child: isFace
                ? const FaceIdGlyph(size: 20, color: AppColors.primary)
                : const Icon(
                    Icons.fingerprint,
                    size: 21,
                    color: AppColors.primary,
                  ),
          ),
          const SizedBox(width: 10),
          Expanded(
            child: Text(
              'Unlock with $method',
              style: AppTextStyles.body.copyWith(
                fontSize: 14.5,
                color: AppColors.textHi,
              ),
            ),
          ),
          Transform.scale(
            scale: 0.82,
            child: Switch(
              value: value,
              onChanged: busy ? null : onChanged,
              activeThumbColor: AppColors.onPrimary,
              activeTrackColor: AppColors.primary,
              inactiveThumbColor: AppColors.textDim,
              inactiveTrackColor: AppColors.primary.withValues(alpha: 0.10),
              trackOutlineColor: WidgetStateProperty.all(
                AppColors.primary.withValues(alpha: 0.22),
              ),
            ),
          ),
        ],
      ),
    );
  }
}

/// A soft violet disc sitting behind the card, a little above centre so the
/// light falls across the title as well as the glass.
class _CardBloom extends StatelessWidget {
  const _CardBloom();

  @override
  Widget build(BuildContext context) {
    return IgnorePointer(
      child: Align(
        alignment: const Alignment(0, -0.18),
        child: Container(
          width: 460,
          height: 460,
          decoration: BoxDecoration(
            shape: BoxShape.circle,
            gradient: RadialGradient(
              colors: [
                AppColors.primary.withValues(alpha: 0.17),
                AppColors.primary.withValues(alpha: 0.05),
                AppColors.primary.withValues(alpha: 0),
              ],
              stops: const [0.0, 0.55, 1.0],
            ),
          ),
        ),
      ),
    );
  }
}

/// An input well: darker than the glass it sits on, so it reads as somewhere
/// to type rather than as another panel. Squarer than the pill below it.
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

  static const double _radius = 15;

  @override
  Widget build(BuildContext context) {
    final restingBorder = OutlineInputBorder(
      borderRadius: BorderRadius.circular(_radius),
      borderSide: BorderSide(color: AppColors.primary.withValues(alpha: 0.16)),
    );

    return Opacity(
      opacity: enabled ? 1.0 : 0.5,
      child: TextField(
        controller: controller,
        obscureText: obscureText,
        keyboardType: keyboardType,
        textInputAction: textInputAction,
        autofillHints: autofillHints,
        enabled: enabled,
        onChanged: onChanged,
        onSubmitted: onSubmitted,
        style: AppTextStyles.body.copyWith(
          fontSize: 15.5,
          color: AppColors.textHi,
        ),
        decoration: InputDecoration(
          filled: true,
          // Sunk below the glass rather than tinted like it.
          fillColor: const Color(0xFF07050F).withValues(alpha: 0.66),
          hintText: hint,
          hintStyle: AppTextStyles.body.copyWith(
            fontSize: 15.5,
            color: AppColors.textDim,
          ),
          prefixIcon: Padding(
            padding: const EdgeInsets.only(left: 15, right: 11),
            child: Icon(prefixIcon, size: 19, color: AppColors.textDim),
          ),
          prefixIconConstraints: const BoxConstraints(
            minWidth: 0,
            minHeight: 0,
          ),
          suffixIcon: suffix != null
              ? Padding(
                  padding: const EdgeInsets.only(right: 14),
                  child: suffix,
                )
              : null,
          suffixIconConstraints: const BoxConstraints(
            minWidth: 0,
            minHeight: 0,
          ),
          border: restingBorder,
          enabledBorder: restingBorder,
          disabledBorder: restingBorder,
          focusedBorder: OutlineInputBorder(
            borderRadius: BorderRadius.circular(_radius),
            borderSide: BorderSide(
              color: AppColors.primary.withValues(alpha: 0.66),
              width: 1.4,
            ),
          ),
          contentPadding: const EdgeInsets.symmetric(
            horizontal: 15,
            vertical: 16,
          ),
        ),
      ),
    );
  }
}

class _ErrorBanner extends StatelessWidget {
  const _ErrorBanner({super.key, required this.message});
  final String message;

  @override
  Widget build(BuildContext context) {
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 13, vertical: 11),
      decoration: BoxDecoration(
        color: AppColors.fill(AppColors.danger),
        borderRadius: BorderRadius.circular(12),
        border: Border.all(color: AppColors.stroke(AppColors.danger)),
      ),
      child: Row(
        children: [
          Icon(symbol('error'), size: 17, color: AppColors.danger),
          const SizedBox(width: 9),
          Expanded(
            child: Text(
              message,
              style: AppTextStyles.micro.copyWith(color: AppColors.danger),
            ),
          ),
        ],
      ),
    );
  }
}
