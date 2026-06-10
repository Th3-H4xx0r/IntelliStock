import 'package:flutter_riverpod/flutter_riverpod.dart';
import '../data/model_repository.dart';

/// AutoDisposeAsyncNotifier that holds the full list of LLM models.
/// Call [refresh] to force-reload (e.g. after create/update/delete).
class ModelsController extends AutoDisposeAsyncNotifier<List<LlmModel>> {
  @override
  Future<List<LlmModel>> build() => _fetch();

  Future<List<LlmModel>> _fetch() =>
      ref.read(modelRepositoryProvider).list();

  Future<void> refresh() async {
    state = const AsyncLoading();
    state = await AsyncValue.guard(_fetch);
  }
}

final modelsControllerProvider =
    AutoDisposeAsyncNotifierProvider<ModelsController, List<LlmModel>>(
  ModelsController.new,
);
