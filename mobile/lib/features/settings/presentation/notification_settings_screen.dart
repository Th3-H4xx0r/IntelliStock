import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import '../../../core/theme/app_colors.dart';
import '../../../core/theme/app_text_styles.dart';
import '../../../core/widgets/app_background.dart';
import '../../../core/widgets/app_toggle.dart';
import '../../../core/widgets/glass_card.dart';
import '../../../core/widgets/material_symbols.dart';
import '../application/notification_prefs_controller.dart';
import '../data/models/notification_prefs.dart';

/// Per-category notification routing — pushed via `/settings/notifications`.
///
/// Each of the 9 categories has an independent Discord and iOS-push toggle.
/// A "Send test" button per channel lets the operator confirm delivery.
class NotificationSettingsScreen extends ConsumerWidget {
  const NotificationSettingsScreen({super.key});

  void _snack(BuildContext context, String msg) {
    ScaffoldMessenger.of(context)
      ..hideCurrentSnackBar()
      ..showSnackBar(SnackBar(content: Text(msg, style: AppTextStyles.body)));
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
        _snack(context, devices == 0
            ? 'No iOS device registered for push yet.'
            : 'Push not delivered — check APNs setup.');
      } else {
        _snack(context, ok ? '$label test sent ✓' : '$label test could not be sent');
      }
    } catch (e) {
      if (context.mounted) _snack(context, '$label test failed: $e');
    }
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
                      _SectionLabel(label: 'Per-category routing'),
                      const SizedBox(height: 8),
                      GlassCard(
                        padding: EdgeInsets.zero,
                        child: Column(
                          children: [
                            for (var i = 0; i < kNotificationCategories.length; i++) ...[
                              if (i > 0) const _Divider(),
                              _CategoryRow(
                                meta: kNotificationCategories[i],
                                route: prefs.routeFor(kNotificationCategories[i].key),
                                onDiscord: (v) => _onToggle(context, ref,
                                    kNotificationCategories[i].key, NotifChannel.discord, v),
                                onPush: (v) => _onToggle(context, ref,
                                    kNotificationCategories[i].key, NotifChannel.push, v),
                              ),
                            ],
                          ],
                        ),
                      ),
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
    required this.meta,
    required this.route,
    required this.onDiscord,
    required this.onPush,
  });

  final NotificationCategoryMeta meta;
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
          Text(meta.label, style: AppTextStyles.bodyHi),
          const SizedBox(height: 2),
          Text(meta.description,
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
