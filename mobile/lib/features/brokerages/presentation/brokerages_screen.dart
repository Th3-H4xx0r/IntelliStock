import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import '../../../core/formatters/formatters.dart';
import '../../../core/theme/app_colors.dart';
import '../../../core/theme/app_text_styles.dart';
import '../../../core/widgets/app_background.dart';
import '../../../core/widgets/app_button.dart';
import '../../../core/widgets/common_widgets.dart';
import '../../../core/widgets/confirm_dialog.dart';
import '../../../core/widgets/glass_card.dart';
import '../../../core/widgets/material_symbols.dart';
import '../../../core/widgets/skeleton.dart';
import '../application/brokerages_controller.dart';
import '../data/brokerage_repository.dart';
import '../data/models/brokerage.dart';
import 'link_brokerage_sheet.dart';

/// Full-screen pushed route for /brokerages.
///
/// Scaffold(backgroundColor: AppColors.canvas) + AppBackground + SafeArea,
/// as specified for standalone (non-shell) screens.
class BrokeragesScreen extends ConsumerWidget {
  const BrokeragesScreen({super.key});

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final state = ref.watch(brokeragesControllerProvider);

    return Scaffold(
      backgroundColor: AppColors.canvas,
      body: AppBackground(
        child: SafeArea(
          child: Column(
            children: [
              // ── App bar row ─────────────────────────────────────────────
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
                      onPressed: () => ref
                          .read(brokeragesControllerProvider.notifier)
                          .refresh(),
                    ),
                  ],
                ),
              ),
              // ── Main content ────────────────────────────────────────────
              Expanded(
                child: RefreshIndicator(
                  onRefresh: () =>
                      ref.read(brokeragesControllerProvider.notifier).refresh(),
                  color: AppColors.primary,
                  backgroundColor: AppColors.surface,
                  child: ListView(
                    padding: const EdgeInsets.symmetric(horizontal: 20),
                    children: [
                      _Header(
                        onLinkTap: () => showLinkBrokerageSheet(context),
                      ),
                      const SizedBox(height: 24),
                      state.when(
                        loading: () => const _BrokeragesSkeleton(),
                        error: (e, _) => ErrorBanner(
                          message: e.toString(),
                          onRetry: () => ref
                              .read(brokeragesControllerProvider.notifier)
                              .refresh(),
                        ),
                        data: (accounts) => accounts.isEmpty
                            ? EmptyState(
                                icon: symbol('account_balance'),
                                title: 'No brokerages linked yet',
                                subtitle:
                                    'Link an Alpaca or Robinhood account to start trading.',
                                actionLabel: 'Link your first brokerage',
                                onAction: () =>
                                    showLinkBrokerageSheet(context),
                              )
                            : _AccountList(accounts: accounts),
                      ),
                      const SizedBox(height: 40),
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

// ── Header ────────────────────────────────────────────────────────────────────

class _Header extends StatelessWidget {
  const _Header({required this.onLinkTap});

  final VoidCallback onLinkTap;

  @override
  Widget build(BuildContext context) {
    return Row(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Expanded(
          child: SectionHeader(
            eyebrow: 'Brokerages',
            title: 'Linked Accounts',
            subtitle:
                'Manage your Alpaca and Robinhood brokerage connections.',
          ),
        ),
        const SizedBox(width: 12),
        AppButton.primary(
          label: 'Link Brokerage',
          icon: Icons.add,
          onPressed: onLinkTap,
          dense: true,
        ),
      ],
    );
  }
}

// ── Account list ──────────────────────────────────────────────────────────────

class _AccountList extends StatelessWidget {
  const _AccountList({required this.accounts});

  final List<Brokerage> accounts;

  @override
  Widget build(BuildContext context) {
    return Column(
      children: accounts
          .map((a) => Padding(
                padding: const EdgeInsets.only(bottom: 16),
                child: _AccountCard(account: a),
              ))
          .toList(),
    );
  }
}

// ── Account card ──────────────────────────────────────────────────────────────

class _AccountCard extends ConsumerWidget {
  const _AccountCard({required this.account});

  final Brokerage account;

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final a = account;
    final isAlpaca = a.brokerageType == 'alpaca';

    final statusColor = _statusColor(a.status);
    final badgeLabel = isAlpaca
        ? 'ALPACA · ${a.paper ? 'Paper' : 'Live'}'
        : 'ROBINHOOD';
    final badgeColor = isAlpaca
        ? (a.paper ? AppColors.info : AppColors.warning)
        : AppColors.success;

    return GlassCard(
      padding: const EdgeInsets.all(18),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          // ── Card header ─────────────────────────────────────────────────
          Row(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              IconTile(
                icon: symbol(isAlpaca ? 'show_chart' : 'savings'),
                color: isAlpaca ? AppColors.primary : AppColors.success,
              ),
              const SizedBox(width: 12),
              Expanded(
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Text(
                      a.accountName,
                      style: AppTextStyles.cardTitle,
                      overflow: TextOverflow.ellipsis,
                    ),
                    const SizedBox(height: 4),
                    AppBadge(label: badgeLabel, color: badgeColor),
                  ],
                ),
              ),
              const SizedBox(width: 8),
              // Status dot + label
              Row(mainAxisSize: MainAxisSize.min, children: [
                Container(
                  width: 8,
                  height: 8,
                  decoration: BoxDecoration(
                    color: statusColor,
                    shape: BoxShape.circle,
                  ),
                ),
                const SizedBox(width: 6),
                Text(
                  a.status ?? 'unknown',
                  style: AppTextStyles.micro.copyWith(color: statusColor),
                ),
              ]),
            ],
          ),
          const SizedBox(height: 12),
          // ── Details ─────────────────────────────────────────────────────
          if (a.accountNumber != null) ...[
            _detailRow('Account #', a.accountNumber!),
            const SizedBox(height: 4),
          ],
          if (a.lastRefreshAt != null) ...[
            _detailRow(
              'Last refreshed',
              _fmtDateTime(a.lastRefreshAt!),
            ),
            const SizedBox(height: 4),
          ],
          if (a.lastError != null) ...[
            const SizedBox(height: 2),
            RichText(
              text: TextSpan(
                style: AppTextStyles.meta.copyWith(color: AppColors.danger),
                children: [
                  TextSpan(
                    text: 'Error: ',
                    style: AppTextStyles.meta.copyWith(
                        color: AppColors.danger,
                        fontWeight: FontWeight.w600),
                  ),
                  TextSpan(text: a.lastError),
                ],
              ),
            ),
            const SizedBox(height: 4),
          ],
          const SizedBox(height: 12),
          // ── Actions ─────────────────────────────────────────────────────
          Row(
            children: [
              if (!isAlpaca)
                _ActionButton(
                  label: 'Refresh',
                  icon: Icons.refresh,
                  onTap: () => _doRefresh(context, ref, a),
                ),
              if (!isAlpaca) const SizedBox(width: 8),
              _ActionButton(
                label: 'Edit',
                icon: Icons.edit_outlined,
                onTap: () => showLinkBrokerageSheet(
                  context,
                  editAccount: a,
                ),
              ),
              const Spacer(),
              _ActionButton(
                label: 'Remove',
                icon: Icons.delete_outline,
                color: AppColors.danger,
                onTap: () => _doRemove(context, ref, a),
              ),
            ],
          ),
        ],
      ),
    );
  }

  Widget _detailRow(String label, String value) {
    return RichText(
      text: TextSpan(
        style: AppTextStyles.meta,
        children: [
          TextSpan(
            text: '$label: ',
            style: AppTextStyles.meta.copyWith(color: AppColors.textDim),
          ),
          TextSpan(
            text: value,
            style: AppTextStyles.meta.copyWith(color: AppColors.textMuted),
          ),
        ],
      ),
    );
  }

  Future<void> _doRefresh(
      BuildContext context, WidgetRef ref, Brokerage a) async {
    try {
      await ref.read(brokerageRepositoryProvider).refresh(a.id);
      await ref.read(brokeragesControllerProvider.notifier).refresh();
    } catch (e) {
      if (context.mounted) {
        ScaffoldMessenger.of(context).showSnackBar(
          SnackBar(
            content: Text('Refresh failed: $e'),
            backgroundColor: AppColors.danger,
          ),
        );
      }
    }
  }

  Future<void> _doRemove(
      BuildContext context, WidgetRef ref, Brokerage a) async {
    await showConfirmDialog(
      context,
      title: 'Remove "${a.accountName}"?',
      body: 'This will unlink the brokerage account. This cannot be undone.',
      confirmLabel: 'Remove',
      confirmColor: AppColors.danger,
      icon: Icons.delete_outline,
      onConfirm: () async {
        await ref.read(brokerageRepositoryProvider).remove(a.id);
        await ref.read(brokeragesControllerProvider.notifier).refresh();
      },
    );
  }

  Color _statusColor(String? status) {
    switch (status) {
      case 'active':
        return AppColors.success;
      case 'expired':
        return AppColors.danger;
      default:
        return AppColors.warning;
    }
  }

  String _fmtDateTime(String raw) {
    final dt = parseDateTime(raw);
    if (dt == null) return raw;
    return fmtDateTime(dt);
  }
}

// ── Loading skeleton ───────────────────────────────────────────────────────────

class _BrokeragesSkeleton extends StatelessWidget {
  const _BrokeragesSkeleton();

  @override
  Widget build(BuildContext context) {
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        // Header skeleton
        Row(crossAxisAlignment: CrossAxisAlignment.start, children: [
          Expanded(
            child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
              Skeleton(width: 90, height: 10),
              const SizedBox(height: 6),
              Skeleton(width: 180, height: 20),
              const SizedBox(height: 6),
              Skeleton(width: double.infinity, height: 11),
            ]),
          ),
          const SizedBox(width: 12),
          Skeleton(width: 120, height: 36, radius: 10),
        ]),
        const SizedBox(height: 24),
        // 3 account-card skeletons
        for (int i = 0; i < 3; i++) ...[
          _AccountCardSkeleton(),
          const SizedBox(height: 16),
        ],
      ],
    );
  }
}

class _AccountCardSkeleton extends StatelessWidget {
  @override
  Widget build(BuildContext context) {
    return GlassCard(
      padding: const EdgeInsets.all(18),
      child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
        Row(crossAxisAlignment: CrossAxisAlignment.start, children: [
          Skeleton.circle(40),
          const SizedBox(width: 12),
          Expanded(
            child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
              Skeleton(width: 140, height: 14),
              const SizedBox(height: 6),
              Skeleton(width: 80, height: 11),
            ]),
          ),
          const SizedBox(width: 8),
          Skeleton(width: 60, height: 11),
        ]),
        const SizedBox(height: 12),
        Skeleton(width: 200, height: 10),
        const SizedBox(height: 4),
        Skeleton(width: 160, height: 10),
        const SizedBox(height: 12),
        Row(children: [
          Skeleton(width: 70, height: 28, radius: 8),
          const SizedBox(width: 8),
          Skeleton(width: 60, height: 28, radius: 8),
        ]),
      ]),
    );
  }
}

// ── Small action button ───────────────────────────────────────────────────────

class _ActionButton extends StatelessWidget {
  const _ActionButton({
    required this.label,
    required this.icon,
    required this.onTap,
    this.color = AppColors.textMuted,
  });

  final String label;
  final IconData icon;
  final VoidCallback onTap;
  final Color color;

  @override
  Widget build(BuildContext context) {
    return InkWell(
      onTap: onTap,
      borderRadius: BorderRadius.circular(8),
      child: Container(
        padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 6),
        decoration: BoxDecoration(
          color: Colors.transparent,
          borderRadius: BorderRadius.circular(8),
          border: Border.all(color: AppColors.border),
        ),
        child: Row(
          mainAxisSize: MainAxisSize.min,
          children: [
            Icon(icon, size: 14, color: color),
            const SizedBox(width: 5),
            Text(
              label,
              style: AppTextStyles.micro.copyWith(
                  color: color, fontWeight: FontWeight.w500),
            ),
          ],
        ),
      ),
    );
  }
}
