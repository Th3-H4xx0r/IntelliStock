import 'package:flutter_riverpod/flutter_riverpod.dart';
import '../data/learning_repository.dart';

/// Everything the Learning screen renders, fetched together so the three
/// sections cannot disagree with each other mid-refresh.
class LearningState {
  const LearningState({
    this.overview,
    this.findings = const [],
    this.funnels = const [],
  });

  final LearningOverview? overview;
  final List<LearningFinding> findings;
  final List<LearningFunnel> funnels;

  /// Phase 1 observes only. The screen reads this rather than assuming, so
  /// when later phases flip the mode the UI follows without a code change.
  bool get observeOnly => !(overview?.actsAutonomously ?? false);
}

final learningStateProvider = FutureProvider.autoDispose<LearningState>((ref) async {
  final repo = ref.watch(learningRepositoryProvider);
  final results = await Future.wait([
    repo.overview(),
    repo.findings(),
    repo.funnels(),
  ]);
  return LearningState(
    overview: results[0] as LearningOverview,
    findings: results[1] as List<LearningFinding>,
    funnels: results[2] as List<LearningFunnel>,
  );
});
