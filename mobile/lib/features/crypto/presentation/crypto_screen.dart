import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../../core/theme/app_colors.dart';
import '../../../core/theme/app_text_styles.dart';
import '../../../core/widgets/app_background.dart';
import '../../../core/widgets/app_button.dart';
import '../../../core/widgets/common_widgets.dart';
import '../../../core/widgets/confirm_dialog.dart';
import '../../../core/widgets/glass_card.dart';
import '../../../core/widgets/material_symbols.dart';
import '../../../core/widgets/status_pill.dart';
import '../../instances/data/models/instance.dart';
import '../data/crypto_repository.dart';
import 'crypto_instance_sheet.dart';

/// Full-screen pushed route for /crypto. Lists the crypto (kind='crypto', 24/7)
/// instances, each with start/stop/edit, and a button to create a new one.
/// Matches the standalone-screen convention (Scaffold + AppBackground + SafeArea).
class CryptoScreen extends ConsumerWidget {
  const CryptoScreen({super.key});

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final async = ref.watch(cryptoInstancesProvider);

    void refresh() => ref.invalidate(cryptoInstancesProvider);

    void openSheet({Instance? edit}) {
      showCryptoInstanceSheet(
        context,
        editInstanceId: edit?.id,
        editName: edit?.name,
        editBrokerageId: edit?.brokerageId,
        editConfig: edit?.cryptoConfig,
        editStocks: edit?.stocks,
        onSaved: refresh,
      );
    }

    return Scaffold(
      backgroundColor: AppColors.canvas,
      body: AppBackground(
        child: SafeArea(
          child: Column(
            children: [
              // App bar row
              Padding(
                padding:
                    const EdgeInsets.symmetric(horizontal: 20, vertical: 12),
                child: Row(
                  children: [
                    IconButton(
                      icon: const Icon(Icons.arrow_back, size: 22),
                      color: AppColors.textMuted,
                      onPressed: () => Navigator.of(context).pop(),
                    ),
                    const Spacer(),
                    IconButton(
                      icon: const Icon(Icons.refresh, size: 22),
                      color: AppColors.textMuted,
                      onPressed: refresh,
                    ),
                  ],
                ),
              ),
              Expanded(
                child: RefreshIndicator(
                  onRefresh: () async => refresh(),
                  color: AppColors.primary,
                  backgroundColor: AppColors.surface,
                  child: ListView(
                    padding: const EdgeInsets.fromLTRB(20, 0, 20, 40),
                    children: [
                      Row(
                        crossAxisAlignment: CrossAxisAlignment.start,
                        children: [
                          Expanded(
                            child: SectionHeader(
                              eyebrow: 'Trading',
                              title: 'Crypto',
                              subtitle:
                                  '24/7 bots — pin fixed coin weights, auto-discover the rest.',
                            ),
                          ),
                          const SizedBox(width: 12),
                          AppButton.primary(
                            label: 'New',
                            icon: symbol('add'),
                            dense: true,
                            onPressed: () => openSheet(),
                          ),
                        ],
                      ),
                      const SizedBox(height: 20),
                      async.when(
                        loading: () => const Padding(
                          padding: EdgeInsets.only(top: 40),
                          child: LoadingState(),
                        ),
                        error: (e, _) => ErrorBanner(
                          message: e.toString(),
                          onRetry: refresh,
                        ),
                        data: (instances) => instances.isEmpty
                            ? EmptyState(
                                icon: symbol('currency_bitcoin'),
                                title: 'No crypto instances yet',
                                subtitle:
                                    'Create a 24/7 crypto bot with a fixed + dynamic coin allocation.',
                                actionLabel: 'New crypto instance',
                                onAction: () => openSheet(),
                              )
                            : Column(
                                children: [
                                  for (final inst in instances) ...[
                                    _CryptoCard(
                                        inst: inst, onEdit: () => openSheet(edit: inst)),
                                    const SizedBox(height: 12),
                                  ],
                                ],
                              ),
                      ),
                    ],
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

// ── Instance card ─────────────────────────────────────────────────────────────

class _CryptoCard extends ConsumerStatefulWidget {
  const _CryptoCard({required this.inst, required this.onEdit});
  final Instance inst;
  final VoidCallback onEdit;

  @override
  ConsumerState<_CryptoCard> createState() => _CryptoCardState();
}

class _CryptoCardState extends ConsumerState<_CryptoCard> {
  bool _busy = false;

  Future<void> _run(Future<void> Function(CryptoRepository repo) action) async {
    setState(() => _busy = true);
    try {
      await action(ref.read(cryptoRepositoryProvider));
    } catch (_) {
      // Swallow — the refreshed list reflects the true state.
    } finally {
      if (mounted) setState(() => _busy = false);
      ref.invalidate(cryptoInstancesProvider);
    }
  }

  Future<void> _confirmDelete() async {
    final inst = widget.inst;
    await showConfirmDialog(
      context,
      title: 'Delete instance',
      body:
          'Delete "${inst.name.isNotEmpty ? inst.name : inst.id}"? This cannot be undone.',
      confirmLabel: 'Delete',
      confirmColor: AppColors.danger,
      icon: symbol('delete'),
      onConfirm: () => _run((repo) => repo.deleteInstance(inst.id, force: true)),
    );
  }

  @override
  Widget build(BuildContext context) {
    final inst = widget.inst;
    final running = inst.runCommand;
    final crashed = inst.crashed;
    final fixed = inst.stocks;

    return GlassCard(
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Row(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              IconTile(icon: symbol('currency_bitcoin')),
              const SizedBox(width: 12),
              Expanded(
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Text(inst.name.isNotEmpty ? inst.name : inst.id,
                        style: AppTextStyles.cardTitle,
                        overflow: TextOverflow.ellipsis),
                    const SizedBox(height: 2),
                    Text(inst.id,
                        style: AppTextStyles.mono(11, color: AppColors.textDim),
                        overflow: TextOverflow.ellipsis),
                  ],
                ),
              ),
              const SizedBox(width: 8),
              Column(
                crossAxisAlignment: CrossAxisAlignment.end,
                children: [
                  const AppBadge(label: '24/7', color: AppColors.info),
                  const SizedBox(height: 6),
                  StatusPill(
                    label: crashed
                        ? 'Crashed'
                        : (running ? 'Running' : 'Stopped'),
                    color: crashed
                        ? AppColors.danger
                        : (running ? AppColors.success : AppColors.textDim),
                    pulsing: running && !crashed,
                  ),
                ],
              ),
            ],
          ),
          const SizedBox(height: 12),
          // Fixed coins summary
          if (fixed.isEmpty)
            Text('100% dynamic — fully auto-discovered.',
                style: AppTextStyles.meta.copyWith(color: AppColors.textDim))
          else
            Wrap(
              spacing: 6,
              runSpacing: 6,
              children: [
                for (final s in fixed)
                  Container(
                    padding:
                        const EdgeInsets.symmetric(horizontal: 8, vertical: 3),
                    decoration: BoxDecoration(
                      color: AppColors.surface,
                      borderRadius: BorderRadius.circular(6),
                      border: Border.all(color: AppColors.border),
                    ),
                    child: Text(s,
                        style:
                            AppTextStyles.mono(11, color: AppColors.textMd)),
                  ),
              ],
            ),
          const SizedBox(height: 12),
          Wrap(
            spacing: 6,
            runSpacing: 6,
            children: [
              _action(
                icon: symbol('edit'),
                label: 'Edit',
                color: AppColors.primary,
                onTap: _busy ? null : widget.onEdit,
              ),
              if (running)
                _action(
                  icon: _busy ? symbol('progress_activity') : symbol('stop'),
                  label: 'Stop',
                  color: AppColors.warning,
                  onTap: _busy
                      ? null
                      : () => _run((repo) => repo.stopInstance(inst.id)),
                )
              else
                _action(
                  icon: _busy
                      ? symbol('progress_activity')
                      : symbol('play_arrow'),
                  label: 'Start',
                  color: AppColors.success,
                  onTap: _busy
                      ? null
                      : () => _run((repo) => repo.startInstance(inst.id)),
                ),
              _action(
                icon: symbol('delete'),
                label: 'Delete',
                color: AppColors.danger,
                onTap: _busy ? null : _confirmDelete,
              ),
            ],
          ),
        ],
      ),
    );
  }

  Widget _action({
    required IconData icon,
    required String label,
    required Color color,
    VoidCallback? onTap,
  }) {
    final disabled = onTap == null;
    return Opacity(
      opacity: disabled ? 0.4 : 1,
      child: GestureDetector(
        onTap: onTap,
        child: Container(
          padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 6),
          decoration: BoxDecoration(
            color: AppColors.fill(color),
            borderRadius: BorderRadius.circular(8),
            border: Border.all(color: AppColors.stroke(color)),
          ),
          child: Row(
            mainAxisSize: MainAxisSize.min,
            children: [
              Icon(icon, size: 13, color: color),
              const SizedBox(width: 4),
              Text(label,
                  style: AppTextStyles.nano
                      .copyWith(color: color, fontWeight: FontWeight.w600)),
            ],
          ),
        ),
      ),
    );
  }
}
