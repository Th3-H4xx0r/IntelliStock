import 'package:flutter_riverpod/flutter_riverpod.dart';
import '../../../core/network/session.dart';
import '../data/onboarding_repository.dart';

/// Step identifiers for the onboarding wizard.
enum OnboardingStep {
  welcome,
  about,
  addModel,
  linkBrokerage,
  createInstance,
  connect,
  complete,
}

/// State held by [OnboardingController].
class OnboardingState {
  const OnboardingState({
    this.stepIndex = 0,
    this.direction = OnboardingDirection.forward,
    this.busy = false,
    this.error,
    // Counts refreshed on each step advance so the Complete step shows live numbers.
    this.modelCount = 0,
    this.brokerageCount = 0,
    this.instanceCount = 0,
  });

  final int stepIndex;
  final OnboardingDirection direction;
  final bool busy;
  final String? error;
  final int modelCount;
  final int brokerageCount;
  final int instanceCount;

  static const steps = OnboardingStep.values;

  OnboardingStep get currentStep => steps[stepIndex];

  bool get isFirstStep => stepIndex == 0;
  bool get isLastStep => stepIndex == steps.length - 1;

  OnboardingState copyWith({
    int? stepIndex,
    OnboardingDirection? direction,
    bool? busy,
    Object? error = _sentinel,
    int? modelCount,
    int? brokerageCount,
    int? instanceCount,
  }) {
    return OnboardingState(
      stepIndex: stepIndex ?? this.stepIndex,
      direction: direction ?? this.direction,
      busy: busy ?? this.busy,
      error: error == _sentinel ? this.error : error as String?,
      modelCount: modelCount ?? this.modelCount,
      brokerageCount: brokerageCount ?? this.brokerageCount,
      instanceCount: instanceCount ?? this.instanceCount,
    );
  }
}

// Sentinel for nullable copyWith.
const _sentinel = Object();

enum OnboardingDirection { forward, back }

/// Notifier that drives the 7-step onboarding wizard.
class OnboardingController extends Notifier<OnboardingState> {
  @override
  OnboardingState build() => const OnboardingState();

  OnboardingRepository get _repo => ref.read(onboardingRepositoryProvider);
  SessionStore get _session => ref.read(sessionProvider);

  /// Advance to the next step.
  void next() {
    final s = state;
    if (s.isLastStep) return;
    state = s.copyWith(
      stepIndex: s.stepIndex + 1,
      direction: OnboardingDirection.forward,
      error: null,
    );
  }

  /// Go back one step.
  void back() {
    final s = state;
    if (s.isFirstStep) return;
    state = s.copyWith(
      stepIndex: s.stepIndex - 1,
      direction: OnboardingDirection.back,
      error: null,
    );
  }

  /// Skip this step (same as next, only called for skippable steps).
  void skip() => next();

  /// Update counts after a resource is created inside a step.
  void updateCounts({int? models, int? brokerages, int? instances}) {
    state = state.copyWith(
      modelCount: models ?? state.modelCount,
      brokerageCount: brokerages ?? state.brokerageCount,
      instanceCount: instances ?? state.instanceCount,
    );
  }

  /// Load the onboarding state from the API (counts only — called on mount).
  Future<void> loadState() async {
    try {
      final data = await _repo.state();
      final counts = (data['counts'] as Map?)?.cast<String, dynamic>() ?? {};
      state = state.copyWith(
        modelCount: (counts['models'] as num?)?.toInt() ?? 0,
        brokerageCount: (counts['brokerages'] as num?)?.toInt() ?? 0,
        instanceCount: (counts['instances'] as num?)?.toInt() ?? 0,
      );
    } catch (_) {
      // Best-effort; counts default to 0.
    }
  }

  /// Complete onboarding: POST /onboarding/complete then update the session.
  /// Returns true on success.
  Future<bool> finish() async {
    state = state.copyWith(busy: true, error: null);
    try {
      final data = await _repo.complete();
      final user = data['user'] as Map?;
      if (user != null) {
        await _session.setUser(user.cast<String, dynamic>());
      }
      state = state.copyWith(busy: false);
      return true;
    } catch (e) {
      state = state.copyWith(busy: false, error: e.toString());
      return false;
    }
  }
}

final onboardingControllerProvider =
    NotifierProvider<OnboardingController, OnboardingState>(
  OnboardingController.new,
);
