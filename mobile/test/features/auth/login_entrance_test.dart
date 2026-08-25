import 'package:fake_async/fake_async.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:intellistock_mobile/features/auth/presentation/login_screen.dart';

void main() {
  test('reveals the sign-in controls after the coin entrance starts', () {
    fakeAsync((clock) {
      final entrance = LoginEntranceController();

      entrance.onCoinEntranceStarted();
      expect(entrance.showForm, isFalse);

      clock.elapse(const Duration(milliseconds: 449));
      expect(entrance.showForm, isFalse);

      clock.elapse(const Duration(milliseconds: 1));
      expect(entrance.showForm, isTrue);
      entrance.dispose();
    });
  });
}
