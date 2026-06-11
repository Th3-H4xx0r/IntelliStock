import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:intellistock_mobile/core/widgets/app_toggle.dart';
import 'package:intellistock_mobile/features/settings/data/models/notification_prefs.dart';
import 'package:intellistock_mobile/features/settings/data/notification_prefs_repository.dart';
import 'package:intellistock_mobile/features/settings/presentation/notification_settings_screen.dart';

class _FakeRepo implements NotificationPrefsRepository {
  _FakeRepo(this._prefs);
  NotificationPrefs _prefs;
  int saveCount = 0;
  int testCount = 0;

  @override
  Future<NotificationPrefs> get() async => _prefs;
  @override
  Future<NotificationPrefs> save(NotificationPrefs prefs) async {
    saveCount++;
    _prefs = prefs;
    return prefs;
  }
  @override
  Future<Map<String, dynamic>> sendTest(NotifChannel channel) async {
    testCount++;
    return {'ok': true, 'channel': channel.name};
  }
}

NotificationPrefs _seed() => const NotificationPrefs(categories: {
      'order_fill': CategoryRoute(discord: true, push: false),
    });

Widget _app(_FakeRepo repo) => ProviderScope(
      overrides: [notificationPrefsRepositoryProvider.overrideWithValue(repo)],
      child: const MaterialApp(home: NotificationSettingsScreen()),
    );

void main() {
  testWidgets('renders 9 categories, two toggles each, and test buttons',
      (tester) async {
    await tester.pumpWidget(_app(_FakeRepo(_seed())));
    await tester.pumpAndSettle();

    expect(find.text('Order filled'), findsOneWidget);
    expect(find.text('Crash loop'), findsOneWidget);
    expect(find.byType(AppToggle), findsNWidgets(18)); // 9 categories x 2
    expect(find.text('Test Discord'), findsOneWidget);
    expect(find.text('Test iOS push'), findsOneWidget);
  });

  testWidgets('tapping a toggle persists via the controller', (tester) async {
    final repo = _FakeRepo(_seed());
    await tester.pumpWidget(_app(repo));
    await tester.pumpAndSettle();

    // First category (order_submit) push toggle = index 1.
    await tester.tap(find.byType(AppToggle).at(1));
    await tester.pumpAndSettle();

    expect(repo.saveCount, greaterThanOrEqualTo(1));
  });

  testWidgets('tapping a test button calls sendTest', (tester) async {
    final repo = _FakeRepo(_seed());
    await tester.pumpWidget(_app(repo));
    await tester.pumpAndSettle();

    await tester.tap(find.text('Test Discord'));
    await tester.pumpAndSettle();

    expect(repo.testCount, 1);
  });
}
