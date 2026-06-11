import 'package:flutter_test/flutter_test.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:intellistock_mobile/features/settings/data/models/notification_prefs.dart';
import 'package:intellistock_mobile/features/settings/data/notification_prefs_repository.dart';
import 'package:intellistock_mobile/features/settings/application/notification_prefs_controller.dart';

class _FakeRepo implements NotificationPrefsRepository {
  _FakeRepo(this._prefs);
  NotificationPrefs _prefs;
  bool failSave = false;
  int saveCount = 0;
  int testCount = 0;

  @override
  Future<NotificationPrefs> get() async => _prefs;

  @override
  Future<NotificationPrefs> save(NotificationPrefs prefs) async {
    saveCount++;
    if (failSave) throw Exception('save failed');
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

ProviderContainer _container(_FakeRepo repo) {
  final c = ProviderContainer(overrides: [
    notificationPrefsRepositoryProvider.overrideWithValue(repo),
  ]);
  addTearDown(c.dispose);
  return c;
}

void main() {
  test('loads preferences from the repository', () async {
    final repo = _FakeRepo(_seed());
    final c = _container(repo);
    final prefs = await c.read(notificationPrefsControllerProvider.future);
    expect(prefs.routeFor('order_fill').discord, true);
    expect(prefs.routeFor('order_fill').push, false);
  });

  test('toggle optimistically updates and persists', () async {
    final repo = _FakeRepo(_seed());
    final c = _container(repo);
    await c.read(notificationPrefsControllerProvider.future);

    final err = await c
        .read(notificationPrefsControllerProvider.notifier)
        .toggle('order_fill', NotifChannel.push, true);

    expect(err, isNull);
    expect(repo.saveCount, 1);
    final state = c.read(notificationPrefsControllerProvider).value!;
    expect(state.routeFor('order_fill').push, true);
    expect(state.routeFor('order_fill').discord, true);
  });

  test('toggle reverts on save failure and returns an error', () async {
    final repo = _FakeRepo(_seed())..failSave = true;
    final c = _container(repo);
    await c.read(notificationPrefsControllerProvider.future);

    final err = await c
        .read(notificationPrefsControllerProvider.notifier)
        .toggle('order_fill', NotifChannel.push, true);

    expect(err, isNotNull);
    final state = c.read(notificationPrefsControllerProvider).value!;
    expect(state.routeFor('order_fill').push, false); // reverted
  });

  test('sendTest delegates to the repository', () async {
    final repo = _FakeRepo(_seed());
    final c = _container(repo);
    await c.read(notificationPrefsControllerProvider.future);
    final res = await c
        .read(notificationPrefsControllerProvider.notifier)
        .sendTest(NotifChannel.discord);
    expect(res['ok'], true);
    expect(repo.testCount, 1);
  });
}
