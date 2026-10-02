import 'package:flutter_riverpod/flutter_riverpod.dart';
import '../../../core/network/api_client.dart';
import '../../../core/network/api_error.dart';

/// Thin data layer for authentication endpoints.
///
/// All network errors bubble up as [ApiError] from the underlying [ApiClient].
class AuthRepository {
  const AuthRepository(this._client);

  final ApiClient _client;

  /// POST /auth/login — returns the raw `{access_token, user}` map.
  Future<Map<String, dynamic>> login(String username, String password) async {
    final data = await _client.post<Map<String, dynamic>>(
      '/auth/login',
      body: {'username': username, 'password': password},
    );
    return data;
  }

  /// GET /auth/me — returns the current user document.
  Future<Map<String, dynamic>> fetchMe() async {
    final data = await _client.get<Map<String, dynamic>>('/auth/me');
    return data;
  }
}

final authRepositoryProvider = Provider<AuthRepository>(
  (ref) => AuthRepository(ref.watch(apiClientProvider)),
);
