import 'package:flutter_test/flutter_test.dart';
import 'package:intellistock_mobile/features/auth/application/auth_controller.dart';

/// Pure unit tests for [LoginState] — no network, no Flutter widgets.
void main() {
  group('LoginState', () {
    test('initial state is idle with no error', () {
      const s = LoginState();
      expect(s.isLoading, isFalse);
      expect(s.errorMessage, isNull);
      expect(s.hasError, isFalse);
    });

    test('copyWith(isLoading: true) sets loading', () {
      const s = LoginState();
      final loading = s.copyWith(isLoading: true);
      expect(loading.isLoading, isTrue);
      expect(loading.errorMessage, isNull);
    });

    test('copyWith(errorMessage) sets hasError', () {
      const s = LoginState();
      final err = s.copyWith(errorMessage: 'Invalid credentials');
      expect(err.hasError, isTrue);
      expect(err.errorMessage, 'Invalid credentials');
    });

    test('copyWith clears errorMessage when not supplied', () {
      final errState =
          const LoginState().copyWith(errorMessage: 'oops');
      // copyWith without errorMessage clears it (controller clears errors this way).
      final same = errState.copyWith(isLoading: false);
      expect(same.errorMessage, isNull);
    });

    test('copyWith(errorMessage: null) via cleared state', () {
      // Simulates clearError: build a new LoginState without error.
      final errState =
          const LoginState().copyWith(errorMessage: 'oops');
      final cleared = LoginState(
        isLoading: errState.isLoading,
        // errorMessage intentionally omitted → null
      );
      expect(cleared.hasError, isFalse);
      expect(cleared.errorMessage, isNull);
    });

    test('toString includes field values', () {
      final s = const LoginState().copyWith(isLoading: true, errorMessage: 'e');
      expect(s.toString(), contains('isLoading: true'));
      expect(s.toString(), contains('errorMessage: e'));
    });
  });
}
