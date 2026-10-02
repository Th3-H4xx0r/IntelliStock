import 'package:flutter_riverpod/flutter_riverpod.dart';
import '../../../core/polling/poller.dart';
import '../data/nexus_repository.dart';

// ── State ─────────────────────────────────────────────────────────────────────

class NexusState {
  const NexusState({
    required this.status,
    this.busy = false,
    this.errorMessage,
  });

  final NexusStatus status;
  final bool busy;
  final String? errorMessage;

  NexusState copyWith({
    NexusStatus? status,
    bool? busy,
    Object? errorMessage = _sentinel,
  }) =>
      NexusState(
        status: status ?? this.status,
        busy: busy ?? this.busy,
        errorMessage: errorMessage == _sentinel
            ? this.errorMessage
            : errorMessage as String?,
      );
}

const _sentinel = Object();

// ── Controller ────────────────────────────────────────────────────────────────

class NexusController extends PollingNotifier<NexusState> {
  @override
  Future<NexusState> fetch() async {
    final repo = ref.read(nexusRepositoryProvider);
    final s = await repo.status();
    final prev = state.valueOrNull;
    return NexusState(
      status: s,
      busy: prev?.busy ?? false,
      errorMessage: null,
    );
  }

  /// 2s while building, 5s idle.
  @override
  Duration interval() {
    final s = state.valueOrNull?.status;
    if (s != null && s.isBuilding) return const Duration(seconds: 2);
    return const Duration(seconds: 5);
  }

  // ── Control actions ──────────────────────────────────────────────────────────

  Future<void> postControl(Map<String, dynamic> body) async {
    await _withBusy(() => ref.read(nexusRepositoryProvider).control(body));
  }

  Future<Map<String, dynamic>?> rebuild(Map<String, dynamic> body) async {
    Map<String, dynamic>? result;
    await _withBusy(() async {
      result = await ref.read(nexusRepositoryProvider).rebuild(body);
    });
    return result;
  }

  Future<void> deleteEdges(Map<String, dynamic> body) async {
    await _withBusy(() => ref.read(nexusRepositoryProvider).deleteEdges(body));
  }

  Future<NexusCacheInfo?> fetchCache() async {
    try {
      return await ref.read(nexusRepositoryProvider).cache();
    } catch (_) {
      return null;
    }
  }

  Future<void> _withBusy(Future<void> Function() action) async {
    if (state case AsyncData(:final value)) {
      state = AsyncData(value.copyWith(busy: true, errorMessage: null));
    }
    try {
      await action();
    } catch (e) {
      if (state case AsyncData(:final value)) {
        state = AsyncData(
            value.copyWith(busy: false, errorMessage: e.toString()));
      }
      return;
    }
    await refreshNow();
  }
}

final nexusControllerProvider =
    AutoDisposeAsyncNotifierProvider<NexusController, NexusState>(
  NexusController.new,
);
