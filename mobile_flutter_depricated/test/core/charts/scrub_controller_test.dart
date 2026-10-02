import 'package:flutter_test/flutter_test.dart';
import 'package:intellistock_mobile/core/charts/scrub_controller.dart';

void main() {
  group('ScrubController', () {
    test('starts empty', () {
      final c = ScrubController(onTick: () {});
      expect(c.value, isNull);
    });

    test('fires a haptic tick when the snapped index changes', () {
      var ticks = 0;
      final c = ScrubController(onTick: () => ticks++);

      c.update(0, 0.0); // first touch -> tick
      c.update(0, 0.04); // same index, finger moved -> no tick
      c.update(1, 0.25); // new index -> tick
      c.update(2, 0.5); // new index -> tick
      c.update(2, 0.55); // same index -> no tick

      expect(ticks, 3);
    });

    test('exposes the latest sample (index + fraction)', () {
      final c = ScrubController(onTick: () {});
      c.update(3, 0.72);
      expect(c.value!.index, 3);
      expect(c.value!.fraction, closeTo(0.72, 1e-9));
    });

    test('clear resets to empty and notifies once', () {
      var notifications = 0;
      final c = ScrubController(onTick: () {})..addListener(() => notifications++);
      c.update(1, 0.3);
      c.clear();
      expect(c.value, isNull);
      expect(notifications, 2); // update + clear
    });

    test('clear on an already-empty controller does not notify', () {
      var notifications = 0;
      final c = ScrubController(onTick: () {})..addListener(() => notifications++);
      c.clear();
      expect(notifications, 0);
    });

    test('notifies listeners on every update so the hairline follows the finger', () {
      var notifications = 0;
      final c = ScrubController(onTick: () {})..addListener(() => notifications++);
      c.update(0, 0.0);
      c.update(0, 0.1); // same index but should still notify (fraction changed)
      expect(notifications, 2);
    });
  });
}
