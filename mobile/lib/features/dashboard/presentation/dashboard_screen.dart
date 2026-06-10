import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';
import '../../../core/theme/app_colors.dart';
import '../../../core/theme/app_text_styles.dart';
import '../../../core/widgets/app_button.dart';
import '../../../core/widgets/common_widgets.dart';
import '../../../core/widgets/glass_card.dart';
import '../../../core/widgets/material_symbols.dart';
import '../../../core/network/session.dart';
import '../../../core/formatters/formatters.dart';
import '../application/dashboard_controller.dart';
import '../data/dashboard_repository.dart';
import 'portfolio_chart.dart';
import 'service_card.dart';

// ── Dashboard screen ──────────────────────────────────────────────────────────

/// Shell tab for /dashboard. The shell (AppShell) provides Scaffold +
/// AppBackground. This widget returns a scrollable body with
/// EdgeInsets.fromLTRB(16,24,16,24) main padding.
class DashboardScreen extends ConsumerWidget {
  const DashboardScreen({super.key});

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final session = ref.watch(sessionProvider);
    final username = session.username;

    return CustomScrollView(
      slivers: [
        SliverPadding(
          padding: const EdgeInsets.fromLTRB(16, 24, 16, 24),
          sliver: SliverList(
            delegate: SliverChildListDelegate([
              // ── Welcome header ───────────────────────────────────────────
              _WelcomeHeader(username: username),
              const SizedBox(height: 32),

              // ── Portfolio section ────────────────────────────────────────
              _PortfolioSection(),
              const SizedBox(height: 32),

              // ── Services section ─────────────────────────────────────────
              _ServicesSection(),
              const SizedBox(height: 32),

              // ── Re-run onboarding panel ──────────────────────────────────
              _OnboardingPanel(),
              const SizedBox(height: 16),
            ]),
          ),
        ),
      ],
    );
  }
}

// ── Welcome header ────────────────────────────────────────────────────────────

class _WelcomeHeader extends StatelessWidget {
  const _WelcomeHeader({required this.username});
  final String username;

  @override
  Widget build(BuildContext context) {
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Text('Dashboard'.toUpperCase(), style: AppTextStyles.eyebrow),
        const SizedBox(height: 6),
        RichText(
          text: TextSpan(
            style: AppTextStyles.h1,
            children: [
              const TextSpan(text: 'Welcome back, '),
              TextSpan(
                text: username,
                style: AppTextStyles.h1.copyWith(color: AppColors.primary),
              ),
              const TextSpan(text: '.'),
            ],
          ),
        ),
        const SizedBox(height: 6),
        Text(
          'Your workspace is ready.',
          style: AppTextStyles.body.copyWith(color: AppColors.textDim),
        ),
      ],
    );
  }
}

// ── Portfolio section ─────────────────────────────────────────────────────────

class _PortfolioSection extends ConsumerWidget {
  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final bkAsync = ref.watch(brokeragesProvider);

    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        // Section header row
        Row(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Expanded(
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Text('Portfolio', style: AppTextStyles.h2),
                  const SizedBox(height: 2),
                  Text(
                    'Live equity history from your linked brokerages.',
                    style:
                        AppTextStyles.micro.copyWith(color: AppColors.textDim),
                  ),
                ],
              ),
            ),
            GestureDetector(
              onTap: () => context.push('/brokerages'),
              child: Row(
                mainAxisSize: MainAxisSize.min,
                children: [
                  Text(
                    'Manage brokerages',
                    style: AppTextStyles.micro.copyWith(
                      color: AppColors.primary,
                      fontWeight: FontWeight.w600,
                    ),
                  ),
                  const SizedBox(width: 4),
                  Icon(symbol('arrow_forward'),
                      size: 14, color: AppColors.primary),
                ],
              ),
            ),
          ],
        ),
        const SizedBox(height: 16),

        // Content
        bkAsync.when(
          loading: () => Column(
            children: List.generate(
              2,
              (_) => Padding(
                padding: const EdgeInsets.only(bottom: 16),
                child: Container(
                  height: 320,
                  decoration: BoxDecoration(
                    color: AppColors.surface.withValues(alpha: 0.5),
                    borderRadius: BorderRadius.circular(16),
                  ),
                ),
              ),
            ),
          ),
          error: (e, _) => ErrorBanner(
            message: e.toString(),
            onRetry: () => ref.invalidate(brokeragesProvider),
          ),
          data: (accounts) {
            if (accounts.isEmpty) {
              return EmptyState(
                icon: symbol('account_balance'),
                title: 'No brokerages linked.',
                subtitle: 'Link a brokerage to see your portfolio here.',
                actionLabel: 'Link a brokerage',
                onAction: () => context.push('/brokerages'),
              );
            }
            return Column(
              children: accounts
                  .map((acct) => Padding(
                        padding: const EdgeInsets.only(bottom: 16),
                        child: PortfolioChart(account: acct),
                      ))
                  .toList(),
            );
          },
        ),
      ],
    );
  }
}

// ── Services section ──────────────────────────────────────────────────────────

class _ServicesSection extends ConsumerWidget {
  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final svcAsync = ref.watch(dashboardServicesProvider);
    final busy = ref.watch(engineBusyProvider);

    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        // Section header row
        Row(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Expanded(
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Text('Services', style: AppTextStyles.h2),
                  const SizedBox(height: 2),
                  Text(
                    'Status and controls for all IntelliStock background services.',
                    style:
                        AppTextStyles.micro.copyWith(color: AppColors.textDim),
                  ),
                ],
              ),
            ),
            IconButton(
              icon: Icon(
                symbol('refresh'),
                size: 18,
                color: AppColors.textDim,
              ),
              onPressed: () =>
                  ref.read(dashboardServicesProvider.notifier).refreshNow(),
              tooltip: 'Refresh',
              splashRadius: 20,
            ),
          ],
        ),
        const SizedBox(height: 16),

        svcAsync.when(
          loading: () => Column(
            children: List.generate(
              5,
              (_) => Padding(
                padding: const EdgeInsets.only(bottom: 12),
                child: Container(
                  height: 140,
                  decoration: BoxDecoration(
                    color: AppColors.surface.withValues(alpha: 0.5),
                    borderRadius: BorderRadius.circular(16),
                  ),
                ),
              ),
            ),
          ),
          error: (e, _) => ErrorBanner(
            message: e.toString(),
            onRetry: () =>
                ref.read(dashboardServicesProvider.notifier).refreshNow(),
          ),
          data: (svc) => Column(
            children: [
              _PriceEngineCard(svc: svc, busy: busy, ref: ref),
              const SizedBox(height: 12),
              _DiscoverEngineCard(svc: svc, busy: busy, ref: ref),
              const SizedBox(height: 12),
              _AgentCard(svc: svc, busy: busy, ref: ref),
              const SizedBox(height: 12),
              _DigestCard(svc: svc, busy: busy, ref: ref),
              const SizedBox(height: 12),
              _NexusCard(svc: svc, busy: busy, ref: ref),
            ],
          ),
        ),
      ],
    );
  }
}

// ── Price Engine card ─────────────────────────────────────────────────────────

class _PriceEngineCard extends StatelessWidget {
  const _PriceEngineCard(
      {required this.svc, required this.busy, required this.ref});
  final ServicesSnapshot svc;
  final Set<String> busy;
  final WidgetRef ref;

  static const _id = 'price_engine';

  @override
  Widget build(BuildContext context) {
    final engine = svc.engineById(_id);
    final status = engine?.status ?? 'stopped';
    final details = engine?.details;
    final isBusy = busy.contains(_id);
    final isRunning = svc.isRunning(_id);
    final repo = ref.read(dashboardRepositoryProvider);
    final busyNotifier = ref.read(engineBusyProvider.notifier);

    return ServiceCard(
      icon: symbol('trending_up'),
      iconColor: AppColors.info,
      title: 'Price Engine',
      subtitle: 'Live market data',
      status: status,
      stats: [
        if (details != null)
          ServiceStatCell(label: 'Details', value: details)
        else
          ServiceStatCell(label: 'Details', value: 'No extra details'),
      ],
      buttons: [
        if (isRunning)
          AppButton.semantic(
            label: 'Terminate',
            color: AppColors.danger,
            icon: symbol('stop_circle'),
            busy: isBusy,
            dense: true,
            onPressed: isBusy
                ? null
                : () => busyNotifier.run(_id, repo.terminatePrice),
          )
        else
          AppButton.semantic(
            label: 'Start',
            color: AppColors.success,
            icon: symbol('play_circle'),
            busy: isBusy,
            dense: true,
            onPressed: isBusy
                ? null
                : () => busyNotifier.run(_id, repo.startPriceService),
          ),
      ],
    );
  }
}

// ── Discover Engine card ──────────────────────────────────────────────────────

class _DiscoverEngineCard extends StatelessWidget {
  const _DiscoverEngineCard(
      {required this.svc, required this.busy, required this.ref});
  final ServicesSnapshot svc;
  final Set<String> busy;
  final WidgetRef ref;

  static const _id = 'discover_engine';

  @override
  Widget build(BuildContext context) {
    final engine = svc.engineById(_id);
    final status = engine?.status ?? 'stopped';
    final details = engine?.details;
    final isBusy = busy.contains(_id);
    final isRunning = svc.isRunning(_id);
    final repo = ref.read(dashboardRepositoryProvider);
    final busyNotifier = ref.read(engineBusyProvider.notifier);

    return ServiceCard(
      icon: symbol('search'),
      iconColor: AppColors.primary,
      title: 'Discover Engine',
      subtitle: 'Opportunity discovery',
      status: status,
      stats: [
        if (details != null)
          ServiceStatCell(label: 'Details', value: details)
        else
          ServiceStatCell(label: 'Details', value: 'No extra details'),
      ],
      buttons: [
        AppButton.semantic(
          label: isRunning ? 'Stop' : 'Start',
          color: isRunning ? AppColors.danger : AppColors.success,
          icon: isRunning ? symbol('stop_circle') : symbol('play_circle'),
          busy: isBusy,
          dense: true,
          onPressed: isBusy
              ? null
              : () => busyNotifier.run(
                    _id,
                    () => repo.controlDiscover(running: !isRunning),
                  ),
        ),
      ],
    );
  }
}

// ── AI Backtest Agent card ────────────────────────────────────────────────────

class _AgentCard extends StatelessWidget {
  const _AgentCard(
      {required this.svc, required this.busy, required this.ref});
  final ServicesSnapshot svc;
  final Set<String> busy;
  final WidgetRef ref;

  static const _id = 'ai_backtest_engine';

  @override
  Widget build(BuildContext context) {
    final engine = svc.engineById(_id);
    final status = engine?.status ?? 'stopped';
    final isBusy = busy.contains(_id);
    final isRunning = svc.isRunning(_id);
    final isPaused = svc.isPaused(_id);
    final agent = svc.agentControl;
    final countToday = agent?['count_today'];
    final lastRunDate = agent?['last_run_date'] as String?;
    final resumeAt = agent?['resume_at'] as String?;
    final repo = ref.read(dashboardRepositoryProvider);
    final busyNotifier = ref.read(engineBusyProvider.notifier);

    final stats = <Widget>[
      ServiceStatCell(
          label: 'Backtests today',
          value: countToday?.toString() ?? '—'),
      ServiceStatCell(label: 'Last run', value: lastRunDate ?? '—'),
      if (resumeAt != null)
        ServiceStatCell(label: 'Resume at', value: resumeAt),
    ];

    final buttons = <Widget>[
      if (isPaused)
        AppButton.semantic(
          label: 'Resume',
          color: AppColors.info,
          icon: symbol('play_circle'),
          busy: isBusy,
          dense: true,
          onPressed: isBusy
              ? null
              : () => busyNotifier.run(
                    _id,
                    () => repo.controlAgent(paused: false),
                  ),
        )
      else if (isRunning)
        AppButton.semantic(
          label: 'Pause',
          color: AppColors.warning,
          icon: symbol('pause_circle'),
          busy: isBusy,
          dense: true,
          onPressed: isBusy
              ? null
              : () => busyNotifier.run(
                    _id,
                    () => repo.controlAgent(paused: true),
                  ),
        ),
      AppButton.semantic(
        label: (isRunning || isPaused) ? 'Stop' : 'Start',
        color: (isRunning || isPaused) ? AppColors.danger : AppColors.success,
        icon: (isRunning || isPaused)
            ? symbol('stop_circle')
            : symbol('play_circle'),
        busy: isBusy,
        dense: true,
        onPressed: isBusy
            ? null
            : () {
                if (isRunning || isPaused) {
                  busyNotifier.run(
                      _id, () => repo.controlAgent(running: false));
                } else {
                  _showStartAgentSheet(context, ref);
                }
              },
      ),
    ];

    return ServiceCard(
      icon: symbol('smart_toy'),
      iconColor: AppColors.warning,
      title: 'AI Backtest Agent',
      subtitle: 'Automated strategy search',
      status: status,
      stats: stats,
      buttons: buttons,
    );
  }

  void _showStartAgentSheet(BuildContext context, WidgetRef ref) {
    final controller = TextEditingController();
    showModalBottomSheet<void>(
      context: context,
      backgroundColor: AppColors.panel,
      shape: const RoundedRectangleBorder(
        borderRadius: BorderRadius.vertical(top: Radius.circular(20)),
      ),
      isScrollControlled: true,
      builder: (_) => Padding(
        padding: EdgeInsets.only(
          bottom: MediaQuery.of(context).viewInsets.bottom,
        ),
        child: Padding(
          padding: const EdgeInsets.all(20),
          child: Column(
            mainAxisSize: MainAxisSize.min,
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Row(children: [
                IconTile(icon: symbol('smart_toy'), color: AppColors.warning),
                const SizedBox(width: 12),
                Expanded(
                  child: Column(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      Text('Start AI Backtest Agent',
                          style: AppTextStyles.cardTitle),
                      Text(
                          'Optionally provide a special request for this run.',
                          style: AppTextStyles.micro
                              .copyWith(color: AppColors.textFaint)),
                    ],
                  ),
                ),
              ]),
              const SizedBox(height: 16),
              Text('Special Request',
                  style: AppTextStyles.micro
                      .copyWith(fontWeight: FontWeight.w600)),
              const SizedBox(height: 8),
              TextField(
                controller: controller,
                maxLines: 3,
                style: AppTextStyles.body,
                decoration: InputDecoration(
                  hintText:
                      'e.g. Focus on high-volatility stocks…',
                  hintStyle:
                      AppTextStyles.body.copyWith(color: AppColors.textFaint),
                  filled: true,
                  fillColor: AppColors.surface,
                  border: OutlineInputBorder(
                    borderRadius: BorderRadius.circular(12),
                    borderSide: BorderSide(color: AppColors.border),
                  ),
                  enabledBorder: OutlineInputBorder(
                    borderRadius: BorderRadius.circular(12),
                    borderSide: BorderSide(color: AppColors.border),
                  ),
                  focusedBorder: OutlineInputBorder(
                    borderRadius: BorderRadius.circular(12),
                    borderSide: BorderSide(
                        color: AppColors.primary.withValues(alpha: 0.4)),
                  ),
                ),
              ),
              const SizedBox(height: 16),
              Row(children: [
                Expanded(
                  child: AppButton.ghost(
                    label: 'Cancel',
                    onPressed: () => Navigator.of(context).pop(),
                  ),
                ),
                const SizedBox(width: 12),
                Expanded(
                  child: AppButton.semantic(
                    label: 'Start Agent',
                    color: AppColors.success,
                    icon: symbol('play_circle'),
                    onPressed: () {
                      Navigator.of(context).pop();
                      final sr = controller.text.trim().isEmpty
                          ? null
                          : controller.text.trim();
                      ref.read(engineBusyProvider.notifier).run(
                            _id,
                            () => ref
                                .read(dashboardRepositoryProvider)
                                .controlAgent(
                                    running: true, specialRequest: sr),
                          );
                    },
                  ),
                ),
              ]),
              const SizedBox(height: 8),
            ],
          ),
        ),
      ),
    );
  }
}

// ── Daily Digest card ─────────────────────────────────────────────────────────

class _DigestCard extends StatelessWidget {
  const _DigestCard(
      {required this.svc, required this.busy, required this.ref});
  final ServicesSnapshot svc;
  final Set<String> busy;
  final WidgetRef ref;

  static const _id = 'daily_digest_engine';

  @override
  Widget build(BuildContext context) {
    final digest = svc.digestControl;
    final isRunning = digest?['running'] == true;
    final status = isRunning ? 'running' : 'stopped';
    final isBusy = busy.contains(_id);
    final lastMorning = digest?['last_morning_at'];
    final lastEvening = digest?['last_evening_at'];
    final repo = ref.read(dashboardRepositoryProvider);
    final busyNotifier = ref.read(engineBusyProvider.notifier);

    return ServiceCard(
      icon: symbol('newspaper'),
      iconColor: AppColors.success,
      title: 'Daily Digest',
      subtitle: 'Discord market summaries',
      status: status,
      stats: [
        ServiceStatCell(
            label: 'Last morning', value: fmtDateTime(lastMorning)),
        ServiceStatCell(
            label: 'Last evening', value: fmtDateTime(lastEvening)),
      ],
      buttons: [
        AppButton.semantic(
          label: isRunning ? 'Stop' : 'Start',
          color: isRunning ? AppColors.danger : AppColors.success,
          icon: isRunning ? symbol('stop_circle') : symbol('play_circle'),
          busy: isBusy,
          dense: true,
          onPressed: isBusy
              ? null
              : () => busyNotifier.run(
                    _id,
                    () => repo.controlDigest(running: !isRunning),
                  ),
        ),
        AppButton.semantic(
          label: 'Send Now',
          color: AppColors.primary,
          icon: symbol('send'),
          busy: isBusy,
          dense: true,
          onPressed:
              isBusy ? null : () => busyNotifier.run(_id, repo.digestSendNow),
        ),
      ],
    );
  }
}

// ── Nexus Graph Engine card ───────────────────────────────────────────────────

class _NexusCard extends StatelessWidget {
  const _NexusCard(
      {required this.svc, required this.busy, required this.ref});
  final ServicesSnapshot svc;
  final Set<String> busy;
  final WidgetRef ref;

  static const _id = 'nexus_graph_engine';

  @override
  Widget build(BuildContext context) {
    final engine = svc.engineById(_id);
    final status = engine?.status ?? 'stopped';
    final isBusy = busy.contains(_id);
    final isRunning = svc.isRunning(_id);
    final nexus = svc.nexusStatus;
    final repo = ref.read(dashboardRepositoryProvider);
    final busyNotifier = ref.read(engineBusyProvider.notifier);

    // Nexus progress
    final graphBuild = nexus?['graph_build'] as Map<String, dynamic>?;
    final progressPct = graphBuild != null
        ? (graphBuild['progress_pct'] as num?)?.round()
        : null;
    final stages = (graphBuild?['stages'] as List?)
        ?.whereType<Map<String, dynamic>>()
        .toList();
    final lastPhase = stages != null && stages.isNotEmpty
        ? (stages.last['message'] as String?)
        : null;

    final stats = <Widget>[
      if (progressPct != null)
        NexusProgressCell(progressPct: progressPct, phase: lastPhase)
      else
        ServiceStatCell(label: 'Build', value: 'No build in progress'),
    ];

    return ServiceCard(
      icon: symbol('hub'),
      iconColor: AppColors.primary,
      title: 'Nexus Graph Engine',
      subtitle: 'Knowledge graph builder',
      status: status,
      stats: stats,
      buttons: [
        AppButton.semantic(
          label: isRunning ? 'Stop' : 'Start',
          color: isRunning ? AppColors.danger : AppColors.success,
          icon: isRunning ? symbol('stop_circle') : symbol('play_circle'),
          busy: isBusy,
          dense: true,
          onPressed: isBusy
              ? null
              : () => busyNotifier.run(
                    _id,
                    () => repo.controlNexus(running: !isRunning),
                  ),
        ),
      ],
    );
  }
}

// ── Re-run onboarding panel ───────────────────────────────────────────────────

class _OnboardingPanel extends StatelessWidget {
  @override
  Widget build(BuildContext context) {
    return GlassCard(
      borderColor: AppColors.border,
      child: Row(
        children: [
          Expanded(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Row(
                  children: [
                    Icon(symbol('replay'),
                        size: 16, color: AppColors.primary),
                    const SizedBox(width: 8),
                    Text('Re-run onboarding',
                        style: AppTextStyles.cardTitle),
                  ],
                ),
                const SizedBox(height: 6),
                Text(
                  'Walk through the welcome flow again to add another model, '
                  'link a brokerage, or spin up a new instance.',
                  style:
                      AppTextStyles.micro.copyWith(color: AppColors.textDim),
                ),
              ],
            ),
          ),
          const SizedBox(width: 16),
          AppButton.semantic(
            label: 'Open',
            color: AppColors.primary,
            icon: symbol('arrow_forward'),
            dense: true,
            onPressed: () => context.push('/onboarding'),
          ),
        ],
      ),
    );
  }
}
