import 'package:flutter_riverpod/flutter_riverpod.dart';
import '../../../core/network/api_error.dart';
import '../../../core/network/session.dart';
import '../data/auth_repository.dart';

/// State for the login operation — idle / loading / error.
class LoginState {
  const LoginState({
    this.isLoading = false,
    this.errorMessage,
    this.succeeded = false,
  });

  final bool isLoading;
  final String? errorMessage;

  /// Credentials accepted, but the session is not committed yet — the
  /// login screen is playing its exit before the router takes over.
  final bool succeeded;

  bool get hasError => errorMessage != null;

  LoginState copyWith({
    bool? isLoading,
    String? errorMessage,
    bool? succeeded,
  }) => LoginState(
    isLoading: isLoading ?? this.isLoading,
    errorMessage: errorMessage,
    succeeded: succeeded ?? this.succeeded,
  );

  @override
  String toString() =>
      'LoginState(isLoading: $isLoading, '
      'errorMessage: $errorMessage, succeeded: $succeeded)';
}

/// Handles the login flow: calls [AuthRepository], writes the session via
/// [sessionProvider], and surfaces loading / error state.
///
/// Auto-disposes so it is fresh each time the login screen is pushed.
class LoginController extends AutoDisposeNotifier<LoginState> {
  @override
  LoginState build() => const LoginState();

  /// Attempts login with [username] and [password].
  ///
  /// On success the session is persisted via [SessionStore.setSession];
  /// [GoRouter] (which listens to [sessionProvider]) will automatically
  /// redirect away from /login.
  ///
  /// [holdBeforeCommit] delays writing the session — and therefore the
  /// router's redirect — so the login screen can play its success
  /// animation while still on screen. The credentials are already
  /// accepted at that point; the delay only defers navigation.
  ///
  /// Returns `true` on success, `false` on failure (state.errorMessage is set).
  Future<bool> login(
    String username,
    String password, {
    Duration holdBeforeCommit = Duration.zero,
  }) async {
    state = state.copyWith(isLoading: true, errorMessage: null);

    try {
      final repo = ref.read(authRepositoryProvider);
      final data = await repo.login(username, password);

      final token = data['access_token'] as String? ?? '';
      final user = data['user'] as Map<String, dynamic>?;

      // Tell the screen it won before navigating, so it can start the
      // coin's flip while we hold the commit.
      state = const LoginState(succeeded: true);
      if (holdBeforeCommit > Duration.zero) {
        await Future<void>.delayed(holdBeforeCommit);
      }

      await ref.read(sessionProvider).setSession(token, user);
      return true;
    } on ApiError catch (e) {
      state = state.copyWith(isLoading: false, errorMessage: e.message);
      return false;
    } catch (e) {
      state = state.copyWith(
        isLoading: false,
        errorMessage: 'Something went wrong. Please try again.',
      );
      return false;
    }
  }

  /// Clears any displayed error (e.g. when the user starts typing again).
  void clearError() {
    if (state.hasError) {
      state = state.copyWith(errorMessage: null);
    }
  }
}

final loginControllerProvider =
    AutoDisposeNotifierProvider<LoginController, LoginState>(
      LoginController.new,
    );
