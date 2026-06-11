import 'package:flutter_test/flutter_test.dart';
import 'package:intellistock_mobile/features/settings/data/models/notification_prefs.dart';

void main() {
  group('NotificationPrefs.fromJson', () {
    test('parses the categories matrix', () {
      final p = NotificationPrefs.fromJson({
        'categories': {
          'order_fill': {'discord': false, 'push': true},
          'halt': {'discord': true, 'push': false},
        },
      });
      expect(p.routeFor('order_fill').discord, false);
      expect(p.routeFor('order_fill').push, true);
      expect(p.routeFor('halt').discord, true);
    });

    test('routeFor defaults to discord-only when absent', () {
      final p = NotificationPrefs.fromJson({'categories': {}});
      expect(p.routeFor('order_fill').discord, true);
      expect(p.routeFor('order_fill').push, false);
    });

    test('round-trips through toJson', () {
      const p = NotificationPrefs(categories: {
        'order_fill': CategoryRoute(discord: false, push: true),
      });
      final back = NotificationPrefs.fromJson(p.toJson());
      expect(back.routeFor('order_fill').push, true);
      expect(back.routeFor('order_fill').discord, false);
    });

    test('withRoute returns an immutable copy', () {
      const p = NotificationPrefs(categories: {
        'order_fill': CategoryRoute(discord: true, push: false),
      });
      final next = p.withRoute('order_fill', const CategoryRoute(discord: true, push: true));
      expect(p.routeFor('order_fill').push, false); // original unchanged
      expect(next.routeFor('order_fill').push, true);
    });
  });

  test('there are 9 categories with stable keys', () {
    expect(kNotificationCategories.length, 9);
    expect(kNotificationCategories.map((c) => c.key), containsAll(<String>[
      'order_submit', 'order_fill', 'order_reject', 'order_retry',
      'strategy_start', 'strategy_error', 'halt', 'drawdown_halt', 'crash_loop',
    ]));
  });
}
