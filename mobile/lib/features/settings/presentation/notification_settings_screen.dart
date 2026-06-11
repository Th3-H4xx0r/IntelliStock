import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import '../../../core/theme/app_colors.dart';
import '../../../core/theme/app_text_styles.dart';
import '../../../core/widgets/app_background.dart';
import '../../../core/widgets/app_toggle.dart';
import '../../../core/widgets/glass_card.dart';
import '../../../core/widgets/material_symbols.dart';
import '../../../core/push/push_device.dart';
import '../../../core/push/push_devices_controller.dart';
import '../../../core/push/push_repository.dart';
import '../../../core/push/push_service.dart';
import '../application/notification_prefs_controller.dart';
import '../data/models/notification_prefs.dart';

/// Per-category notification routing — pushed via `/settings/notifications`.
///
/// Each of the 9 categories has an independent Discord and iOS-push toggle.
/// A "Send test" button per channel lets the operator confirm delivery.
class NotificationSettingsScreen extends ConsumerWidget {
  const NotificationSettingsScreen({super.key});

  void _snack(BuildContext context, String msg, {bool ok = true}) {
    ScaffoldMessenger.of(context)
      ..hideCurrentSnackBar()
      ..showSnackBar(SnackBar(
        content: Text(
          msg,
          style: AppTextStyles.body.copyWith(
            color: Colors.white,
            fontWeight: FontWeight.w600,
          ),
        ),
        backgroundColor: ok ? AppColors.success : AppColors.danger,
        behavior: SnackBarBehavior.floating,
        duration: const Duration(seconds: 3),
      ));
  }

  Future<void> _enableOnThisDevice(BuildContext context, WidgetRef ref) async {
    _snack(context, 'Requesting push permission…');
    await ref.read(pushServiceProvider).enable();
    // The APNs token arrives asynchronously via the native callback; give it a
    // moment, then refresh the list.
    await Future<void>.delayed(const Duration(seconds: 2));
    await ref.read(pushDevicesProvider.notifier).refresh();
  }

  Future<void> _removeDevice(BuildContext context, WidgetRef ref, PushDevice d) async {
    try {
      await ref.read(pushRepositoryProvider).unregister(d.deviceToken);
      await ref.read(pushDevicesProvider.notifier).refresh();
      if (context.mounted) _snack(context, 'Device removed');
    } catch (e) {
      if (context.mounted) _snack(context, 'Could not remove: $e', ok: false);
    }
  }

  Future<void> _onToggle(BuildContext context, WidgetRef ref, String category,
      NotifChannel channel, bool value) async {
    final err = await ref
        .read(notificationPrefsControllerProvider.notifier)
        .toggle(category, channel, value);
    if (err != null && context.mounted) {
      _snack(context, 'Could not save: $err');
    }
  }

  Future<void> _sendTest(BuildContext context, WidgetRef ref, NotifChannel channel) async {
    final label = channel == NotifChannel.discord ? 'Discord' : 'iOS push';
    try {
      final res = await ref
          .read(notificationPrefsControllerProvider.notifier)
          .sendTest(channel);
      if (!context.mounted) return;
      final ok = res['ok'] == true;
      if (channel == NotifChannel.push && !ok) {
        final devices = res['devices'] ?? 0;
        String msg;
        if (devices == 0) {
          msg = 'No iOS device registered yet — tap "Enable push on this device".';
        } else {
          final errors = res['errors'] as List?;
          final reason = (errors != null && errors.isNotEmpty)
              ? (errors.first['reason'] ?? '').toString()
              : '';
          msg = reason.isNotEmpty
              ? 'Push failed: $reason'
              : 'Push not delivered — check APNs setup.';
        }
        _snack(context, msg, ok: false);
      } else {
        _snack(context, ok ? '$label test sent ✓' : '$label test could not be sent', ok: ok);
      }
      // The send may have auto-corrected a device's env; refresh the list.
      if (channel == NotifChannel.push) {
        ref.read(pushDevicesProvider.notifier).refresh();
      }
    } catch (e) {
      if (context.mounted) _snack(context, '$label test failed: $e', ok: false);
    }
  }

  /// Grouped per-type routing: a header per group, then a card of rows with
  /// independent Discord + iOS-push switches. Uses the API taxonomy; falls back
  /// to the built-in categories if the server didn't send `types`.
  List<Widget> _buildGroupedRouting(BuildContext context, WidgetRef ref, NotificationPrefs prefs) {
    final types = prefs.types.isNotEmpty
        ? prefs.types
        : kNotificationCategories
            .map((m) => NotificationType(
                key: m.key, group: 'Notifications', label: m.label, desc: m.description))
            .toList();
    final groups = <String>[];
    for (final t in types) {
      if (!groups.contains(t.group)) groups.add(t.group);
    }
    final widgets = <Widget>[];
    for (final group in groups) {
      final groupTypes = types.where((t) => t.group == group).toList();
      widgets.add(_SectionLabel(label: group));
      widgets.add(const SizedBox(height: 8));
      widgets.add(GlassCard(
        padding: EdgeInsets.zero,
        child: Column(
          children: [
            for (var i = 0; i < groupTypes.length; i++) ...[
              if (i > 0) const _Divider(),
              _CategoryRow(
                label: groupTypes[i].label,
                description: groupTypes[i].desc,
                route: prefs.routeFor(groupTypes[i].key),
                onDiscord: (v) =>
                    _onToggle(context, ref, groupTypes[i].key, NotifChannel.discord, v),
                onPush: (v) =>
                    _onToggle(context, ref, groupTypes[i].key, NotifChannel.push, v),
              ),
            ],
          ],
        ),
      ));
      widgets.add(const SizedBox(height: 24));
    }
    return widgets;
  }

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final prefsAsync = ref.watch(notificationPrefsControllerProvider);

    return Scaffold(
      backgroundColor: AppColors.canvas,
      body: AppBackground(
        child: SafeArea(
          child: CustomScrollView(
            slivers: [
              SliverAppBar(
                backgroundColor: Colors.transparent,
                elevation: 0,
                leading: IconButton(
                  icon: Icon(symbol('arrow_back'), color: AppColors.textMuted, size: 22),
                  onPressed: () => Navigator.of(context).pop(),
                ),
                title: Text('Notifications', style: AppTextStyles.h3),
                centerTitle: false,
              ),
              SliverPadding(
                padding: const EdgeInsets.fromLTRB(16, 0, 16, 32),
                sliver: prefsAsync.when(
                  loading: () => const SliverToBoxAdapter(
                    child: Padding(
                      padding: EdgeInsets.only(top: 80),
                      child: Center(child: CircularProgressIndicator()),
                    ),
                  ),
                  error: (e, _) => SliverToBoxAdapter(
                    child: Padding(
                      padding: const EdgeInsets.only(top: 60),
                      child: Center(
                        child: Text('Failed to load preferences\n$e',
                            textAlign: TextAlign.center,
                            style: AppTextStyles.body.copyWith(color: AppColors.danger)),
                      ),
                    ),
                  ),
                  data: (prefs) => SliverList(
                    delegate: SliverChildListDelegate([
                      _SectionLabel(label: 'Test delivery'),
                      const SizedBox(height: 8),
                      GlassCard(
                        child: Column(
                          crossAxisAlignment: CrossAxisAlignment.start,
                          children: [
                            Text('Send a sample notification to confirm a channel works.',
                                style: AppTextStyles.micro.copyWith(color: AppColors.textDim)),
                            const SizedBox(height: 12),
                            Row(
                              children: [
                                Expanded(
                                  child: _TestButton(
                                    icon: symbol('discord'),
                                    label: 'Test Discord',
                                    onTap: () => _sendTest(context, ref, NotifChannel.discord),
                                  ),
                                ),
                                const SizedBox(width: 10),
                                Expanded(
                                  child: _TestButton(
                                    icon: symbol('notifications'),
                                    label: 'Test iOS push',
                                    onTap: () => _sendTest(context, ref, NotifChannel.push),
                                  ),
                                ),
                              ],
                            ),
                          ],
                        ),
                      ),
                      const SizedBox(height: 24),
                      _SectionLabel(label: 'Registered devices'),
                      const SizedBox(height: 8),
                      GlassCard(
                        child: Column(
                          crossAxisAlignment: CrossAxisAlignment.start,
                          children: [
                            ref.watch(pushDevicesProvider).when(
                                  loading: () => Row(children: [
                                    const SizedBox(
                                        width: 16,
                                        height: 16,
                                        child: CircularProgressIndicator(strokeWidth: 2)),
                                    const SizedBox(width: 10),
                                    Text('Checking registered devices…',
                                        style: AppTextStyles.micro
                                            .copyWith(color: AppColors.textDim)),
                                  ]),
                                  error: (e, _) => Text('Could not load devices: $e',
                                      style: AppTextStyles.micro
                                          .copyWith(color: AppColors.danger)),
                                  data: (devices) => devices.isEmpty
                                      ? Column(
                                          crossAxisAlignment: CrossAxisAlignment.start,
                                          children: [
                                            Text('No devices registered yet.',
                                                style: AppTextStyles.bodyHi),
                                            const SizedBox(height: 4),
                                            Text(
                                              'Tap "Enable push on this device" and allow '
                                              'notifications. Requires a physical device with '
                                              'the app installed (push doesn\'t work in the '
                                              'simulator).',
                                              style: AppTextStyles.micro
                                                  .copyWith(color: AppColors.textDim),
                                            ),
                                          ],
                                        )
                                      : Column(
                                          children: [
                                            for (final d in devices)
                                              _DeviceTile(
                                                device: d,
                                                onRemove: () =>
                                                    _removeDevice(context, ref, d),
                                              ),
                                          ],
                                        ),
                                ),
                            const SizedBox(height: 12),
                            _TestButton(
                              icon: symbol('notifications'),
                              label: 'Enable push on this device',
                              onTap: () => _enableOnThisDevice(context, ref),
                            ),
                          ],
                        ),
                      ),
                      const SizedBox(height: 24),
                      ..._buildGroupedRouting(context, ref, prefs),
                    ]),
                  ),
                ),
              ),
            ],
          ),
        ),
      ),
    );
  }
}

class _CategoryRow extends StatelessWidget {
  const _CategoryRow({
    required this.label,
    required this.description,
    required this.route,
    required this.onDiscord,
    required this.onPush,
  });

  final String label;
  final String description;
  final CategoryRoute route;
  final ValueChanged<bool> onDiscord;
  final ValueChanged<bool> onPush;

  @override
  Widget build(BuildContext context) {
    return Padding(
      padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 14),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Text(label, style: AppTextStyles.bodyHi),
          const SizedBox(height: 2),
          Text(description,
              style: AppTextStyles.micro.copyWith(color: AppColors.textDim)),
          const SizedBox(height: 10),
          Row(
            children: [
              _ChannelToggle(label: 'Discord', value: route.discord, onChanged: onDiscord),
              const SizedBox(width: 20),
              _ChannelToggle(label: 'iOS push', value: route.push, onChanged: onPush),
            ],
          ),
        ],
      ),
    );
  }
}

class _ChannelToggle extends StatelessWidget {
  const _ChannelToggle({required this.label, required this.value, required this.onChanged});
  final String label;
  final bool value;
  final ValueChanged<bool> onChanged;

  @override
  Widget build(BuildContext context) {
    return Row(
      mainAxisSize: MainAxisSize.min,
      children: [
        Text(label,
            style: AppTextStyles.micro.copyWith(
              color: value ? AppColors.textHi : AppColors.textDim,
            )),
        const SizedBox(width: 8),
        AppToggle(value: value, onChanged: onChanged),
      ],
    );
  }
}

class _TestButton extends StatelessWidget {
  const _TestButton({required this.icon, required this.label, required this.onTap});
  final IconData icon;
  final String label;
  final VoidCallback onTap;

  @override
  Widget build(BuildContext context) {
    return InkWell(
      onTap: onTap,
      borderRadius: BorderRadius.circular(10),
      child: Container(
        padding: const EdgeInsets.symmetric(vertical: 12, horizontal: 12),
        decoration: BoxDecoration(
          color: AppColors.fill(AppColors.primary),
          borderRadius: BorderRadius.circular(10),
          border: Border.all(color: AppColors.stroke(AppColors.primary)),
        ),
        child: Row(
          mainAxisAlignment: MainAxisAlignment.center,
          children: [
            Icon(icon, size: 16, color: AppColors.primary),
            const SizedBox(width: 8),
            Flexible(
              child: Text(label,
                  overflow: TextOverflow.ellipsis,
                  style: AppTextStyles.meta.copyWith(color: AppColors.primary)),
            ),
          ],
        ),
      ),
    );
  }
}

class _DeviceTile extends StatelessWidget {
  const _DeviceTile({required this.device, required this.onRemove});
  final PushDevice device;
  final VoidCallback onRemove;

  @override
  Widget build(BuildContext context) {
    final parts = <String>[device.platform.toUpperCase(), device.env];
    if (device.lastSeen != null && device.lastSeen!.isNotEmpty) {
      parts.add('seen ${device.lastSeen!.split('T').first}');
    }
    return Padding(
      padding: const EdgeInsets.symmetric(vertical: 6),
      child: Row(
        children: [
          Icon(symbol('notifications'), size: 16, color: AppColors.primary),
          const SizedBox(width: 10),
          Expanded(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Text(device.tokenSuffix, style: AppTextStyles.body),
                Text(parts.join(' · '),
                    style: AppTextStyles.micro.copyWith(color: AppColors.textDim)),
              ],
            ),
          ),
          IconButton(
            icon: Icon(symbol('delete'), size: 18, color: AppColors.textDim),
            tooltip: 'Remove',
            onPressed: onRemove,
          ),
        ],
      ),
    );
  }
}

class _SectionLabel extends StatelessWidget {
  const _SectionLabel({required this.label});
  final String label;
  @override
  Widget build(BuildContext context) {
    return Padding(
      padding: const EdgeInsets.only(left: 4, bottom: 2),
      child: Text(label.toUpperCase(), style: AppTextStyles.eyebrow),
    );
  }
}

class _Divider extends StatelessWidget {
  const _Divider();
  @override
  Widget build(BuildContext context) {
    return Container(height: 1, margin: const EdgeInsets.symmetric(horizontal: 16), color: AppColors.border);
  }
}
