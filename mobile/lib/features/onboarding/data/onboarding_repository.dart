import 'package:flutter_riverpod/flutter_riverpod.dart';
import '../../../core/network/api_client.dart';

/// Data layer for onboarding endpoints.
///
/// All three methods return the raw decoded JSON map. [complete] and [reset]
/// both return `{user: {...}}` which the controller uses to update the session.
class OnboardingRepository {
  const OnboardingRepository(this._client);

  final ApiClient _client;

  /// GET /onboarding/state → `{has_completed_onboarding, counts, user}`.
  Future<Map<String, dynamic>> state() async {
    final data = await _client.get<Map<String, dynamic>>('/onboarding/state');
    return data;
  }

  /// POST /onboarding/complete → `{user}`.
  Future<Map<String, dynamic>> complete() async {
    final data =
        await _client.post<Map<String, dynamic>>('/onboarding/complete');
    return data;
  }

  /// POST /onboarding/reset → `{user}`.
  Future<Map<String, dynamic>> reset() async {
    final data =
        await _client.post<Map<String, dynamic>>('/onboarding/reset');
    return data;
  }
}

final onboardingRepositoryProvider = Provider<OnboardingRepository>(
  (ref) => OnboardingRepository(ref.watch(apiClientProvider)),
);
