import 'dart:math' as math;

import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';
import '../../../core/theme/app_colors.dart';
import '../../../core/theme/app_text_styles.dart';
import '../../../core/widgets/app_button.dart';
import '../../../core/widgets/common_widgets.dart';
import '../../../core/widgets/glass_card.dart';
import '../../../core/widgets/material_symbols.dart';
import '../../../core/widgets/skeleton.dart';
import '../../../core/widgets/relative_time_text.dart';
import '../../../core/formatters/formatters.dart';
import '../../../widgets_bridge/widget_sync_service.dart';
import '../application/account_positions_controller.dart';
import '../application/dashboard_controller.dart';
import '../application/selected_account_controller.dart';
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
    // Refresh the home-screen widget (live instance equity) on dashboard entry.
    ref.watch(widgetSyncProvider);

    // The shell drops the top safe-area inset for the dashboard so the gradient
    // bleeds under the status bar / dynamic island — re-add it as content pad.
    final topInset = MediaQuery.viewPaddingOf(context).top;

    return Stack(
      children: [
        // Full-bleed violet→black gradient with a diagonal light streak, behind
        // the (scrolling) glass content as a static backdrop.
        const _DashboardBackdrop(),
        CustomScrollView(
          slivers: [
            SliverPadding(
              padding: EdgeInsets.fromLTRB(16, topInset + 12, 16, 24),
              sliver: SliverList(
                delegate: SliverChildListDelegate([
                  // ── Portfolio section ──────────────────────────────────────
                  _PortfolioSection(),
                  const SizedBox(height: 32),

                  // ── Services section ───────────────────────────────────────
                  _ServicesSection(),
                  const SizedBox(height: 32),

                  // ── Re-run onboarding panel ────────────────────────────────
                  _OnboardingPanel(),
                  const SizedBox(height: 16),
                ]),
              ),
            ),
          ],
        ),
      ],
    );
  }
}

// ── Dashboard backdrop ────────────────────────────────────────────────────────

/// The professional violet gradient field behind the dashboard, modelled on a
/// modern fintech home screen: a deep violet crown that falls off to the app's
/// canvas black within the top ~third — leaving most of the screen dark — with
/// a soft diagonal specular streak and a lavender bloom confined to the crown.
/// It is full-bleed (paints under the dynamic island) and static, so the glass
/// cards glide over it.
class _DashboardBackdrop extends StatelessWidget {
  const _DashboardBackdrop();

  @override
  Widget build(BuildContext context) {
    return Positioned.fill(
      child: IgnorePointer(
        child: Stack(
          children: [
            // 1) Deep violet crown that falls off to canvas black by ~a third
            //    of the way down — the rest of the screen stays dark.
            const Positioned.fill(
              child: DecoratedBox(
                decoration: BoxDecoration(
                  gradient: LinearGradient(
                    begin: Alignment.topCenter,
                    end: Alignment.bottomCenter,
                    colors: [
                      Color(0xFF5A28BE), // deep violet crown (under the island)
                      Color(0xFF34176E),
                      Color(0xFF150A2C),
                      Color(0xFF04040C), // canvas black
                    ],
                    stops: [0.0, 0.10, 0.22, 0.35],
                  ),
                ),
              ),
            ),
            // 2) Soft diagonal specular sheen, confined to the crown.
            Positioned(
              top: 0,
              left: 0,
              right: 0,
              height: 320,
              child: DecoratedBox(
                decoration: BoxDecoration(
                  gradient: LinearGradient(
                    begin: Alignment.bottomLeft,
                    end: Alignment.topRight,
                    colors: [
                      Colors.white.withValues(alpha: 0.0),
                      Colors.white.withValues(alpha: 0.0),
                      Colors.white.withValues(alpha: 0.09),
                      Colors.white.withValues(alpha: 0.0),
                    ],
                    stops: const [0.0, 0.52, 0.70, 0.88],
                  ),
                ),
              ),
            ),
            // 3) Subtle lavender bloom capping the streak, upper-right.
            Positioned(
              top: -70,
              right: -60,
              child: Container(
                width: 280,
                height: 280,
                decoration: BoxDecoration(
                  shape: BoxShape.circle,
                  gradient: RadialGradient(
                    colors: [
                      const Color(0xFFB794FF).withValues(alpha: 0.13),
                      const Color(0xFFB794FF).withValues(alpha: 0.0),
                    ],
                  ),
                ),
              ),
            ),
          ],
        ),
      ),
    );
  }
}

// ── Portfolio section ─────────────────────────────────────────────────────────

class _PortfolioSection extends ConsumerStatefulWidget {
  const _PortfolioSection();

  @override
  ConsumerState<_PortfolioSection> createState() => _PortfolioSectionState();
}

class _PortfolioSectionState extends ConsumerState<_PortfolioSection> {
  bool _expanded = false;

  @override
  Widget build(BuildContext context) {
    final bkAsync = ref.watch(brokeragesProvider);
    final selectedId = ref.watch(selectedAccountProvider);

    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        // Slim section header: just a label + a quiet "Manage" affordance.
        Row(
          children: [
            Text('Portfolio', style: AppTextStyles.h3),
            const Spacer(),
            GestureDetector(
              onTap: () => context.push('/brokerages'),
              behavior: HitTestBehavior.opaque,
              child: Row(
                mainAxisSize: MainAxisSize.min,
                children: [
                  Text(
                    'Manage',
                    style: AppTextStyles.micro.copyWith(
                      color: AppColors.textMuted,
                      fontWeight: FontWeight.w600,
                    ),
                  ),
                  const SizedBox(width: 2),
                  Icon(symbol('arrow_forward'),
                      size: 13, color: AppColors.textMuted),
                ],
              ),
            ),
          ],
        ),
        const SizedBox(height: 14),

        // Content — one hero for the selected account, switchable via a
        // dropdown on the account label.
        bkAsync.when(
          loading: () => const _PortfolioSkeleton(),
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
            final selected = _resolveSelected(accounts, selectedId);
            final canSwitch = accounts.length > 1;
            return Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                _AccountSelector(
                  account: selected,
                  canSwitch: canSwitch,
                  expanded: _expanded,
                  onToggle: () => setState(() => _expanded = !_expanded),
                ),
                if (canSwitch)
                  _AccountDropdown(
                    accounts: accounts,
                    selectedId: selected.id,
                    expanded: _expanded,
                    onSelect: (id) {
                      ref.read(selectedAccountProvider.notifier).select(id);
                      setState(() => _expanded = false);
                    },
                  ),
                const SizedBox(height: 8),
                // Keyed by account so switching accounts starts a fresh load
                // (skeleton), while changing range reuses the cached value.
                PortfolioChart(
                  key: ValueKey(selected.id),
                  account: selected,
                  hero: true,
                ),
                // Live freshness line — ticks honestly even between polls.
                Consumer(builder: (context, ref, _) {
                  final updatedAt = ref.watch(portfolioUpdatedAtProvider);
                  if (updatedAt == null) return const SizedBox.shrink();
                  final faint = AppTextStyles.nano
                      .copyWith(color: AppColors.textFaint);
                  return Padding(
                    padding: const EdgeInsets.only(top: 6),
                    child: Row(
                      mainAxisSize: MainAxisSize.min,
                      children: [
                        Text('Updated ', style: faint),
                        RelativeTimeText(timestamp: updatedAt, style: faint),
                      ],
                    ),
                  );
                }),
                _HoldingsList(brokerageId: selected.id),
              ],
            );
          },
        ),
      ],
    );
  }

  BrokerageAccount _resolveSelected(
      List<BrokerageAccount> accounts, String? id) {
    if (id != null) {
      for (final a in accounts) {
        if (a.id == id) return a;
      }
    }
    return accounts.first;
  }
}

// ── Account switcher ──────────────────────────────────────────────────────────

String _accountLabel(BrokerageAccount a) {
  if (a.brokerageType == 'alpaca') {
    return a.alpacaPaper ? 'Alpaca · Paper' : 'Alpaca';
  }
  if (a.brokerageType == 'robinhood') return 'Robinhood';
  return a.accountName.isNotEmpty ? a.accountName : a.brokerageType;
}

IconData _accountIcon(BrokerageAccount a) =>
    a.brokerageType == 'alpaca' ? symbol('show_chart') : symbol('savings');

/// The hero's account identity — an uppercase eyebrow with a chevron (when more
/// than one account exists) that toggles the [_AccountDropdown], plus a status
/// dot on the right. Mirrors the look of the chart card header.
class _AccountSelector extends StatelessWidget {
  const _AccountSelector({
    required this.account,
    required this.canSwitch,
    required this.expanded,
    required this.onToggle,
  });

  final BrokerageAccount account;
  final bool canSwitch;
  final bool expanded;
  final VoidCallback onToggle;

  @override
  Widget build(BuildContext context) {
    final statusColor = account.isActive ? AppColors.success : AppColors.danger;
    final left = Row(
      mainAxisSize: MainAxisSize.min,
      children: [
        Icon(_accountIcon(account), color: AppColors.primary, size: 16),
        const SizedBox(width: 6),
        Text(
          _accountLabel(account).toUpperCase(),
          style: AppTextStyles.nano.copyWith(
            color: AppColors.textDim,
            fontWeight: FontWeight.w700,
            letterSpacing: 1.0,
          ),
        ),
        if (canSwitch) ...[
          const SizedBox(width: 4),
          Icon(
            expanded ? symbol('expand_less') : symbol('expand_more'),
            size: 16,
            color: AppColors.textMuted,
          ),
        ],
      ],
    );

    return Row(
      children: [
        if (canSwitch)
          GestureDetector(
            behavior: HitTestBehavior.opaque,
            onTap: onToggle,
            child: left,
          )
        else
          left,
        const Spacer(),
        Container(
          width: 6,
          height: 6,
          decoration:
              BoxDecoration(shape: BoxShape.circle, color: statusColor),
        ),
        const SizedBox(width: 4),
        Text(account.status,
            style: AppTextStyles.nano.copyWith(color: statusColor)),
      ],
    );
  }
}

/// The dropdown that animates open below the selector, listing every account.
class _AccountDropdown extends StatelessWidget {
  const _AccountDropdown({
    required this.accounts,
    required this.selectedId,
    required this.expanded,
    required this.onSelect,
  });

  final List<BrokerageAccount> accounts;
  final String selectedId;
  final bool expanded;
  final void Function(String) onSelect;

  @override
  Widget build(BuildContext context) {
    return AnimatedSize(
      duration: const Duration(milliseconds: 200),
      curve: Curves.easeOut,
      alignment: Alignment.topCenter,
      child: expanded
          ? Padding(
              padding: const EdgeInsets.only(top: 10),
              child: GlassCard(
                liquid: true,
                padding: const EdgeInsets.all(6),
                child: Column(
                  children: [
                    for (final a in accounts)
                      _AccountRow(
                        account: a,
                        selected: a.id == selectedId,
                        onTap: () => onSelect(a.id),
                      ),
                  ],
                ),
              ),
            )
          : const SizedBox(width: double.infinity),
    );
  }
}

class _AccountRow extends StatelessWidget {
  const _AccountRow({
    required this.account,
    required this.selected,
    required this.onTap,
  });

  final BrokerageAccount account;
  final bool selected;
  final VoidCallback onTap;

  @override
  Widget build(BuildContext context) {
    final statusColor = account.isActive ? AppColors.success : AppColors.danger;
    return GestureDetector(
      behavior: HitTestBehavior.opaque,
      onTap: onTap,
      child: Container(
        padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 11),
        decoration: BoxDecoration(
          color: selected ? const Color(0x1FFFFFFF) : Colors.transparent,
          borderRadius: BorderRadius.circular(10),
        ),
        child: Row(
          children: [
            Icon(_accountIcon(account), color: AppColors.primary, size: 16),
            const SizedBox(width: 10),
            Expanded(
              child: Text(
                _accountLabel(account),
                style: AppTextStyles.bodyHi.copyWith(
                  fontWeight: selected ? FontWeight.w700 : FontWeight.w500,
                ),
              ),
            ),
            Container(
              width: 6,
              height: 6,
              decoration:
                  BoxDecoration(shape: BoxShape.circle, color: statusColor),
            ),
            const SizedBox(width: 10),
            Icon(
              selected ? symbol('check') : symbol('arrow_forward'),
              size: 15,
              color: selected ? AppColors.primary : AppColors.textFaint,
            ),
          ],
        ),
      ),
    );
  }
}

// ── Holdings ──────────────────────────────────────────────────────────────────

/// The selected account's uninvested cash + holdings, shown below the hero
/// chart like a modern brokerage app (Robinhood/Coinbase). Hidden while it has
/// no data, so it never shows a blank box.
class _HoldingsList extends ConsumerWidget {
  const _HoldingsList({required this.brokerageId});
  final String brokerageId;

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final holdings = ref.watch(accountHoldingsProvider(brokerageId)).valueOrNull;
    if (holdings == null || holdings.isEmpty) return const SizedBox.shrink();
    final positions = holdings.positions;
    final sparks = ref.watch(holdingsSparklinesProvider(brokerageId)).valueOrNull;
    // Total account value → each row's ring shows its share of the portfolio.
    final total = (holdings.cash ?? 0) +
        positions.fold<double>(0, (s, p) => s + p.marketValue);
    return Padding(
      padding: const EdgeInsets.only(top: 24),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Padding(
            padding: const EdgeInsets.only(bottom: 12, left: 2),
            child: Text('Holdings', style: AppTextStyles.h3),
          ),
          GlassCard(
            frosted: true,
            padding: const EdgeInsets.symmetric(vertical: 4),
            child: Column(
              children: [
                if (holdings.cash != null) ...[
                  _CashRow(cash: holdings.cash!, total: total),
                  if (positions.isNotEmpty) const _HoldingDivider(),
                ],
                for (var i = 0; i < positions.length; i++) ...[
                  if (i > 0) const _HoldingDivider(),
                  _HoldingRow(
                    p: positions[i],
                    total: total,
                    spark: sparks?[positions[i].symbol],
                  ),
                ],
              ],
            ),
          ),
        ],
      ),
    );
  }
}

class _HoldingDivider extends StatelessWidget {
  const _HoldingDivider();
  @override
  Widget build(BuildContext context) => Divider(
        height: 1,
        thickness: 1,
        color: Colors.white.withValues(alpha: 0.05),
        indent: 66,
        endIndent: 14,
      );
}

/// Deterministic per-symbol accent so each ticker gets a stable, distinct ring
/// colour.
Color _symbolColor(String s) {
  var h = 0;
  for (final c in s.codeUnits) {
    h = (h * 31 + c) & 0x7fffffff;
  }
  return HSLColor.fromAHSL(1, (h % 360).toDouble(), 0.55, 0.6).toColor();
}

/// A tiny intraday (1D, since 12 AM) line for one holding — green if the day's
/// move is up, red if down, mirroring the live-trading position cards.
class _MiniSpark extends StatelessWidget {
  const _MiniSpark({required this.values});
  final List<double> values;

  @override
  Widget build(BuildContext context) {
    if (values.length < 2) return const SizedBox(width: 68, height: 26);
    final up = values.last >= values.first;
    return SizedBox(
      width: 68,
      height: 26,
      child: CustomPaint(
        painter:
            _SparkPainter(values, up ? AppColors.success : AppColors.danger),
      ),
    );
  }
}

class _SparkPainter extends CustomPainter {
  _SparkPainter(this.values, this.color);
  final List<double> values;
  final Color color;

  @override
  void paint(Canvas canvas, Size size) {
    var lo = values.first, hi = values.first;
    for (final v in values) {
      if (v < lo) lo = v;
      if (v > hi) hi = v;
    }
    final range = (hi - lo).abs() < 1e-9 ? 1.0 : hi - lo;
    const pad = 2.0;
    final h = size.height - pad * 2;
    final path = Path();
    for (var i = 0; i < values.length; i++) {
      final x = i / (values.length - 1) * size.width;
      final y = pad + (1 - (values[i] - lo) / range) * h;
      i == 0 ? path.moveTo(x, y) : path.lineTo(x, y);
    }
    canvas.drawPath(
      path,
      Paint()
        ..color = color
        ..style = PaintingStyle.stroke
        ..strokeWidth = 1.5
        ..strokeCap = StrokeCap.round
        ..strokeJoin = StrokeJoin.round,
    );
  }

  @override
  bool shouldRepaint(covariant _SparkPainter old) =>
      old.values != values || old.color != color;
}

/// A circular allocation ring: a progress arc = this item's share of the
/// portfolio, with the percentage in the centre (the "diversity" indicator).
class _AllocationRing extends StatelessWidget {
  const _AllocationRing({required this.fraction, required this.color});
  final double fraction;
  final Color color;

  static String _label(double f) {
    final pct = f * 100;
    if (pct <= 0) return '0%';
    if (pct < 1) return '<1%';
    return '${pct.round()}%';
  }

  @override
  Widget build(BuildContext context) {
    return SizedBox(
      width: 44,
      height: 44,
      child: Stack(
        alignment: Alignment.center,
        children: [
          CustomPaint(
            size: const Size(44, 44),
            painter: _RingPainter(fraction: fraction.clamp(0.0, 1.0), color: color),
          ),
          Text(
            _label(fraction),
            style: AppTextStyles.nano.copyWith(
              color: AppColors.textHi,
              fontWeight: FontWeight.w700,
            ),
          ),
        ],
      ),
    );
  }
}

class _RingPainter extends CustomPainter {
  _RingPainter({required this.fraction, required this.color});
  final double fraction;
  final Color color;

  @override
  void paint(Canvas canvas, Size size) {
    final center = size.center(Offset.zero);
    final radius = size.width / 2 - 3.5;
    final track = Paint()
      ..color = Colors.white.withValues(alpha: 0.10)
      ..style = PaintingStyle.stroke
      ..strokeWidth = 3.5;
    final prog = Paint()
      ..color = color
      ..style = PaintingStyle.stroke
      ..strokeWidth = 3.5
      ..strokeCap = StrokeCap.round;
    canvas.drawCircle(center, radius, track);
    canvas.drawArc(
      Rect.fromCircle(center: center, radius: radius),
      -math.pi / 2,
      2 * math.pi * fraction.clamp(0.0, 1.0),
      false,
      prog,
    );
  }

  @override
  bool shouldRepaint(_RingPainter o) =>
      o.fraction != fraction || o.color != color;
}

class _CashRow extends StatelessWidget {
  const _CashRow({required this.cash, required this.total});
  final double cash;
  final double total;

  @override
  Widget build(BuildContext context) {
    return Padding(
      padding: const EdgeInsets.symmetric(horizontal: 14, vertical: 11),
      child: Row(
        children: [
          _AllocationRing(
            fraction: total > 0 ? cash / total : 0,
            color: AppColors.teal,
          ),
          const SizedBox(width: 12),
          Expanded(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Text('Cash',
                    style: AppTextStyles.bodyHi
                        .copyWith(fontWeight: FontWeight.w700)),
                const SizedBox(height: 2),
                Text('Available to invest',
                    style:
                        AppTextStyles.micro.copyWith(color: AppColors.textDim)),
              ],
            ),
          ),
          Text(fmtMoney(cash),
              style: AppTextStyles.value.copyWith(color: AppColors.textHi)),
        ],
      ),
    );
  }
}

class _HoldingRow extends StatelessWidget {
  const _HoldingRow({required this.p, required this.total, this.spark});
  final AccountPosition p;
  final double total;

  /// Intraday 1D values (since 12 AM) for this symbol's mini sparkline.
  final List<double>? spark;

  String get _qtyLabel {
    final q = p.qty;
    final s =
        q == q.roundToDouble() ? q.toInt().toString() : q.toStringAsFixed(2);
    return '$s ${q == 1 ? 'share' : 'shares'}';
  }

  @override
  Widget build(BuildContext context) {
    // Colour the whole row by this holding's TOTAL P&L (dollar) sign.
    final up = p.unrealizedPnl >= 0;
    final color = up ? AppColors.success : AppColors.danger;
    return Padding(
      padding: const EdgeInsets.symmetric(horizontal: 14, vertical: 11),
      child: Row(
        children: [
          _AllocationRing(
            fraction: total > 0 ? p.marketValue / total : 0,
            color: _symbolColor(p.symbol),
          ),
          const SizedBox(width: 12),
          Expanded(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Text(p.symbol,
                    style: AppTextStyles.bodyHi
                        .copyWith(color: color, fontWeight: FontWeight.w700)),
                const SizedBox(height: 2),
                Text(_qtyLabel,
                    style: AppTextStyles.micro
                        .copyWith(color: color.withValues(alpha: 0.7))),
              ],
            ),
          ),
          if (spark != null) ...[
            _MiniSpark(values: spark!),
            const SizedBox(width: 12),
          ],
          Column(
            crossAxisAlignment: CrossAxisAlignment.end,
            children: [
              Text(fmtMoney(p.marketValue),
                  style: AppTextStyles.value.copyWith(color: color)),
              const SizedBox(height: 3),
              Text(
                '${fmtPnl(p.unrealizedPnl)} · ${fmtPct(p.unrealizedPnlPct)}',
                style: AppTextStyles.micro
                    .copyWith(color: color, fontWeight: FontWeight.w700),
              ),
            ],
          ),
        ],
      ),
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
          loading: () => const _ServicesSkeleton(),
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

// ── Skeleton helpers ──────────────────────────────────────────────────────────

/// Skeleton for the portfolio section: 2 portfolio-card shaped blocks.
class _PortfolioSkeleton extends StatelessWidget {
  const _PortfolioSkeleton();

  @override
  Widget build(BuildContext context) {
    return Column(
      children: List.generate(2, (i) => _PortfolioCardSkeleton(bottomPad: i < 1 ? 16 : 0)),
    );
  }
}

class _PortfolioCardSkeleton extends StatelessWidget {
  const _PortfolioCardSkeleton({this.bottomPad = 0});
  final double bottomPad;

  @override
  Widget build(BuildContext context) {
    return Padding(
      padding: EdgeInsets.only(bottom: bottomPad),
      child: GlassCard(
        liquid: true,
        padding: EdgeInsets.zero,
        child: Padding(
          padding: const EdgeInsets.all(16),
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              // Brokerage header line
              Row(
                children: [
                  Skeleton(width: 120, height: 12, radius: 6),
                  const Spacer(),
                  Skeleton(width: 48, height: 10, radius: 5),
                ],
              ),
              const SizedBox(height: 12),
              // Big value
              Skeleton(width: 160, height: 28, radius: 8),
              const SizedBox(height: 8),
              // Change line
              Skeleton(width: 100, height: 12, radius: 6),
              const SizedBox(height: 14),
              // Range pills row
              Row(
                children: List.generate(
                  7,
                  (_) => Padding(
                    padding: const EdgeInsets.only(right: 6),
                    child: Skeleton(width: 30, height: 22, radius: 6),
                  ),
                ),
              ),
              const SizedBox(height: 8),
              // Chart block
              Skeleton(height: 140, radius: 8),
            ],
          ),
        ),
      ),
    );
  }
}

/// Skeleton for the services section: 5 service-card shaped blocks.
class _ServicesSkeleton extends StatelessWidget {
  const _ServicesSkeleton();

  @override
  Widget build(BuildContext context) {
    return Column(
      children: List.generate(5, (i) => _ServiceCardSkeleton(bottomPad: i < 4 ? 12 : 0)),
    );
  }
}

class _ServiceCardSkeleton extends StatelessWidget {
  const _ServiceCardSkeleton({this.bottomPad = 0});
  final double bottomPad;

  @override
  Widget build(BuildContext context) {
    return Padding(
      padding: EdgeInsets.only(bottom: bottomPad),
      child: GlassCard(
        liquid: true,
        padding: const EdgeInsets.all(20),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            // Header row: icon tile + title/subtitle + pill
            Row(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Skeleton(width: 40, height: 40, radius: 10),
                const SizedBox(width: 12),
                Expanded(
                  child: Column(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      Skeleton(width: 140, height: 14, radius: 6),
                      const SizedBox(height: 6),
                      Skeleton(width: 100, height: 10, radius: 5),
                    ],
                  ),
                ),
                const SizedBox(width: 8),
                Skeleton(width: 60, height: 22, radius: 11),
              ],
            ),
            const SizedBox(height: 16),
            // Stat cell
            Skeleton(height: 44, radius: 8),
            const SizedBox(height: 16),
            // Button
            Skeleton(height: 36, radius: 8),
          ],
        ),
      ),
    );
  }
}

// ── Re-run onboarding panel ───────────────────────────────────────────────────

class _OnboardingPanel extends StatelessWidget {
  @override
  Widget build(BuildContext context) {
    return GlassCard(
      liquid: true,
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
