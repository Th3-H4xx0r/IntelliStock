import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';
import '../../../core/theme/app_colors.dart';
import '../../../core/theme/app_text_styles.dart';
import '../../../core/widgets/app_background.dart';
import '../../../core/widgets/app_button.dart';
import '../../../core/widgets/common_widgets.dart';
import '../../../core/widgets/confirm_dialog.dart';
import '../../../core/widgets/material_symbols.dart';
import '../application/onboarding_controller.dart';
import 'steps/step_about.dart';
import 'steps/step_add_model.dart';
import 'steps/step_complete.dart';
import 'steps/step_connect.dart';
import 'steps/step_create_instance.dart';
import 'steps/step_link_brokerage.dart';
import 'steps/step_welcome.dart';

/// Step metadata — label shown in the progress bar.
const _stepLabels = [
  'Welcome',
  'About',
  'Model',
  'Brokerage',
  'Instance',
  'Connect',
  'Done',
];

/// Whether the step at [index] can be skipped.
bool _isSkippable(int index) => index >= 2 && index <= 5;

/// Full-screen onboarding wizard.
///
/// The shell provides `AppBackground`, its own top-bar and progress header,
/// a controlled `PageView` with directional slide transitions, and a
/// Back / Skip / Next footer.
class OnboardingScreen extends ConsumerStatefulWidget {
  const OnboardingScreen({super.key});

  @override
  ConsumerState<OnboardingScreen> createState() => _OnboardingScreenState();
}

class _OnboardingScreenState extends ConsumerState<OnboardingScreen>
    with SingleTickerProviderStateMixin {
  late final PageController _pageCtrl;

  @override
  void initState() {
    super.initState();
    _pageCtrl = PageController();
    // Load counts on mount.
    Future.microtask(
      () => ref.read(onboardingControllerProvider.notifier).loadState(),
    );
  }

  @override
  void dispose() {
    _pageCtrl.dispose();
    super.dispose();
  }

  // ── Navigation helpers ──────────────────────────────────────────────────

  void _goTo(int index) {
    if (!_pageCtrl.hasClients) return;
    _pageCtrl.animateToPage(
      index,
      duration: const Duration(milliseconds: 350),
      curve: Curves.easeInOut,
    );
  }

  void _handleNext() {
    final notifier = ref.read(onboardingControllerProvider.notifier);
    final s = ref.read(onboardingControllerProvider);
    if (s.isLastStep) {
      _finish();
      return;
    }
    notifier.next();
    _goTo(s.stepIndex + 1);
  }

  void _handleBack() {
    final notifier = ref.read(onboardingControllerProvider.notifier);
    final s = ref.read(onboardingControllerProvider);
    if (s.isFirstStep) return;
    notifier.back();
    _goTo(s.stepIndex - 1);
  }

  void _handleSkip() {
    final notifier = ref.read(onboardingControllerProvider.notifier);
    final s = ref.read(onboardingControllerProvider);
    notifier.skip();
    _goTo(s.stepIndex + 1);
  }

  Future<void> _finish() async {
    final ok =
        await ref.read(onboardingControllerProvider.notifier).finish();
    if (ok && mounted) {
      context.go('/dashboard');
    }
  }

  Future<void> _handleExit() async {
    final confirmed = await showConfirmDialog(
      context,
      title: 'Exit onboarding?',
      body:
          'Your saved models, brokerages, and instances stay configured. '
          'You can re-run this flow later from the Settings screen.',
      confirmLabel: 'Exit',
      confirmColor: AppColors.warning,
      icon: Icons.exit_to_app_outlined,
    );
    if (confirmed && mounted) {
      context.go('/dashboard');
    }
  }

  // ── Build ───────────────────────────────────────────────────────────────

  @override
  Widget build(BuildContext context) {
    final state = ref.watch(onboardingControllerProvider);
    final stepIdx = state.stepIndex;
    final isLast = state.isLastStep;
    final isFirst = state.isFirstStep;
    final busy = state.busy;

    final nextLabel = isLast ? 'Open dashboard' : 'Next';
    final canSkip = _isSkippable(stepIdx);

    return Scaffold(
      backgroundColor: AppColors.canvas,
      body: AppBackground(
        child: SafeArea(
          child: Column(
            children: [
              // ── Top bar ────────────────────────────────────────────────
              Padding(
                padding: const EdgeInsets.symmetric(
                    horizontal: 20, vertical: 16),
                child: Row(
                  children: [
                    // Logo tile + title.
                    IconTile(
                      icon: symbol('auto_awesome'),
                      color: AppColors.primary,
                      size: 36,
                    ),
                    const SizedBox(width: 10),
                    Column(
                      crossAxisAlignment: CrossAxisAlignment.start,
                      children: [
                        Text(
                          'INTELLISTOCK',
                          style: AppTextStyles.nano.copyWith(
                            color: AppColors.textDim,
                            letterSpacing: 1.8,
                            fontWeight: FontWeight.w700,
                          ),
                        ),
                        Text(
                          'Welcome flow',
                          style: AppTextStyles.meta.copyWith(
                              color: AppColors.textMd,
                              fontWeight: FontWeight.w600),
                        ),
                      ],
                    ),
                    const Spacer(),
                    // Exit button.
                    GestureDetector(
                      onTap: _handleExit,
                      child: Row(
                        children: [
                          Text('Exit',
                              style: AppTextStyles.meta
                                  .copyWith(color: AppColors.textDim)),
                          const SizedBox(width: 4),
                          Icon(symbol('close'),
                              size: 16, color: AppColors.textDim),
                        ],
                      ),
                    ),
                  ],
                ),
              ),
              // ── Progress header ────────────────────────────────────────
              Padding(
                padding:
                    const EdgeInsets.symmetric(horizontal: 20, vertical: 4),
                child: _ProgressHeader(
                  stepLabels: _stepLabels,
                  currentIndex: stepIdx,
                ),
              ),
              const SizedBox(height: 8),
              // ── Step content ───────────────────────────────────────────
              Expanded(
                child: PageView.builder(
                  controller: _pageCtrl,
                  // Disable swipe — navigation is programmatic only.
                  physics: const NeverScrollableScrollPhysics(),
                  itemCount: OnboardingStep.values.length,
                  itemBuilder: (_, index) {
                    return Padding(
                      padding:
                          const EdgeInsets.symmetric(horizontal: 20),
                      child: _buildStep(index),
                    );
                  },
                ),
              ),
              // ── Footer ─────────────────────────────────────────────────
              _Footer(
                showBack: !isFirst && !isLast,
                showSkip: canSkip,
                nextLabel: nextLabel,
                busy: busy,
                onBack: _handleBack,
                onSkip: _handleSkip,
                onNext: _handleNext,
              ),
              const SizedBox(height: 8),
            ],
          ),
        ),
      ),
    );
  }

  Widget _buildStep(int index) {
    return switch (index) {
      0 => const StepWelcome(),
      1 => const StepAbout(),
      2 => const StepAddModel(),
      3 => const StepLinkBrokerage(),
      4 => const StepCreateInstance(),
      5 => const StepConnect(),
      6 => const StepComplete(),
      _ => const SizedBox.shrink(),
    };
  }
}

// ── Progress header ─────────────────────────────────────────────────────────

class _ProgressHeader extends StatelessWidget {
  const _ProgressHeader({
    required this.stepLabels,
    required this.currentIndex,
  });

  final List<String> stepLabels;
  final int currentIndex;

  @override
  Widget build(BuildContext context) {
    return SingleChildScrollView(
      scrollDirection: Axis.horizontal,
      child: Row(
        children: [
          for (var i = 0; i < stepLabels.length; i++) ...[
            _StepCircle(
              index: i,
              currentIndex: currentIndex,
              total: stepLabels.length,
            ),
            if (i < stepLabels.length - 1)
              _Connector(filled: i < currentIndex),
          ],
        ],
      ),
    );
  }
}

class _StepCircle extends StatelessWidget {
  const _StepCircle({
    required this.index,
    required this.currentIndex,
    required this.total,
  });

  final int index;
  final int currentIndex;
  final int total;

  @override
  Widget build(BuildContext context) {
    final isDone = index < currentIndex;
    final isCurrent = index == currentIndex;

    return AnimatedContainer(
      duration: const Duration(milliseconds: 300),
      width: 26,
      height: 26,
      decoration: BoxDecoration(
        shape: BoxShape.circle,
        color: isDone
            ? AppColors.fill(AppColors.primary)
            : isCurrent
                ? AppColors.fill(AppColors.primary)
                : Colors.transparent,
        border: Border.all(
          color: (isDone || isCurrent)
              ? AppColors.primary
              : AppColors.textFaint,
          width: isCurrent ? 2 : 1,
        ),
      ),
      child: Center(
        child: isDone
            ? Icon(symbol('check'), size: 14, color: AppColors.primary)
            : Text(
                '${index + 1}',
                style: AppTextStyles.nano.copyWith(
                  color: isCurrent
                      ? AppColors.primary
                      : AppColors.textFaint,
                  fontWeight: FontWeight.w700,
                ),
              ),
      ),
    );
  }
}

class _Connector extends StatelessWidget {
  const _Connector({required this.filled});

  final bool filled;

  @override
  Widget build(BuildContext context) {
    return AnimatedContainer(
      duration: const Duration(milliseconds: 300),
      width: 20,
      height: 2,
      decoration: BoxDecoration(
        borderRadius: BorderRadius.circular(1),
        color: filled ? AppColors.primary.withValues(alpha: 0.6) : AppColors.border,
      ),
    );
  }
}

// ── Footer ───────────────────────────────────────────────────────────────────

class _Footer extends StatelessWidget {
  const _Footer({
    required this.showBack,
    required this.showSkip,
    required this.nextLabel,
    required this.busy,
    required this.onBack,
    required this.onSkip,
    required this.onNext,
  });

  final bool showBack;
  final bool showSkip;
  final String nextLabel;
  final bool busy;
  final VoidCallback onBack;
  final VoidCallback onSkip;
  final VoidCallback onNext;

  @override
  Widget build(BuildContext context) {
    return Padding(
      padding: const EdgeInsets.symmetric(horizontal: 20, vertical: 12),
      child: Row(
        children: [
          // Back.
          if (showBack)
            AppButton.ghost(
              label: 'Back',
              icon: symbol('arrow_back'),
              onPressed: busy ? null : onBack,
              dense: true,
            )
          else
            const Spacer(),
          if (showBack) const Spacer(),
          // Skip.
          if (showSkip)
            Padding(
              padding: const EdgeInsets.only(right: 8),
              child: GestureDetector(
                onTap: busy ? null : onSkip,
                child: Opacity(
                  opacity: busy ? 0.4 : 1.0,
                  child: Text(
                    'Skip for now',
                    style: AppTextStyles.meta
                        .copyWith(color: AppColors.textDim),
                  ),
                ),
              ),
            ),
          // Next.
          AppButton.primary(
            label: nextLabel,
            icon: busy ? null : symbol('arrow_forward'),
            busy: busy,
            onPressed: busy ? null : onNext,
          ),
        ],
      ),
    );
  }
}
