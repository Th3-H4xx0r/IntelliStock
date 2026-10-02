import 'package:flutter_riverpod/flutter_riverpod.dart';
import '../data/learning_repository.dart';

/// Everything the Learning screen renders, fetched together so the three
/// sections cannot disagree with each other mid-refresh.
class LearningState {
  const LearningState({
    this.overview,
    this.findings = const [],
    this.funnels = const [],
    this.approvals = const [],
    this.floors = const [],
    this.engineRunning = false,
    this.mode = 'observe',
    this.targets,
    this.partialError,
  });

  /// Live approvals wait for a human indefinitely, so they are what the screen
  /// leads with.
  List<LearningApproval> get liveApprovals =>
      approvals.where((a) => a.holdsForever).toList();

  final LearningOverview? overview;
  final List<LearningFinding> findings;
  final List<LearningFunnel> funnels;
  final List<LearningApproval> approvals;
  final List<LearningFloor> floors;
  final bool engineRunning;
  final String mode;
  final LearningTargets? targets;

  /// Set when SOME endpoints answered and others did not. The screen still
  /// renders what loaded — an all-or-nothing `Future.wait` blanked the whole
  /// tab when one of three brand-new endpoints was missing, which is a live
  /// risk whenever the backend lags a redeploy.
  final String? partialError;

  /// Phase 1 observes only. The screen reads this rather than assuming, so
  /// when later phases flip the mode the UI follows without a code change.
  bool get observeOnly => !(overview?.actsAutonomously ?? false);

  /// True when nothing loaded at all — the only case worth an error screen.
  bool get isEmptyFailure =>
      overview == null && findings.isEmpty && funnels.isEmpty &&
      approvals.isEmpty && floors.isEmpty;
}

Future<T?> _attempt<T>(Future<T> Function() call, List<String> errors,
    String label) async {
  try {
    return await call();
  } catch (err) {
    errors.add('$label: $err');
    return null;
  }
}

final learningStateProvider = FutureProvider.autoDispose<LearningState>((ref) async {
  final repo = ref.watch(learningRepositoryProvider);
  final errors = <String>[];
  final results = await Future.wait([
    _attempt(repo.overview, errors, 'overview'),
    _attempt(repo.findings, errors, 'findings'),
    _attempt(repo.funnels, errors, 'runs'),
    _attempt(repo.approvals, errors, 'approvals'),
    _attempt(repo.noiseFloors, errors, 'noise floors'),
    _attempt(repo.control, errors, 'control'),
    _attempt(repo.targets, errors, 'targets'),
  ]);
  final controlDoc = results[5] as Map<String, dynamic>?;
  final state = LearningState(
    overview: results[0] as LearningOverview?,
    findings: (results[1] as List<LearningFinding>?) ?? const [],
    funnels: (results[2] as List<LearningFunnel>?) ?? const [],
    approvals: (results[3] as List<LearningApproval>?) ?? const [],
    floors: (results[4] as List<LearningFloor>?) ?? const [],
    engineRunning: controlDoc?['running'] == true,
    mode: ((controlDoc?['config'] as Map?)?['mode'] ?? 'observe').toString(),
    targets: results[6] as LearningTargets?,
    partialError: errors.isEmpty ? null : errors.join('; '),
  );
  if (state.isEmptyFailure && errors.isNotEmpty) {
    throw Exception(errors.join('; '));
  }
  return state;
});
