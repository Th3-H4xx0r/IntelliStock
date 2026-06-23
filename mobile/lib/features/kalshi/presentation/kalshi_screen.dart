import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../../core/theme/app_colors.dart';
import '../../../core/theme/app_text_styles.dart';
import '../../../core/widgets/common_widgets.dart';
import '../../../core/widgets/glass_card.dart';
import '../../../core/widgets/material_symbols.dart';
import '../../dashboard/application/dashboard_controller.dart';
import '../../dashboard/data/dashboard_repository.dart';
import '../data/kalshi_repository.dart';

/// Dedicated Kalshi monitoring screen (its own bottom-nav tab). Account
/// selector + KILL in the header; portfolio value + Edge Radar + positions.
/// Pull-to-refresh invalidates the autoDispose family providers. Styled with
/// the app's GlassCard / AppColors / common widgets to match the rest of the UI.
class KalshiScreen extends ConsumerStatefulWidget {
  const KalshiScreen({super.key});

  @override
  ConsumerState<KalshiScreen> createState() => _KalshiScreenState();
}

class _KalshiScreenState extends ConsumerState<KalshiScreen> {
  String? _selectedId;
  bool _killing = false;

  List<BrokerageAccount> _kalshiAccounts() {
    final accts = ref.watch(brokeragesProvider).value ?? const <BrokerageAccount>[];
    return accts.where((a) => a.brokerageType == 'kalshi').toList();
  }

  Future<void> _refresh() async {
    final id = _selectedId;
    if (id == null) return;
    ref.invalidate(kalshiPortfolioProvider(id));
    ref.invalidate(kalshiEdgesProvider(id));
    ref.invalidate(kalshiPositionsProvider(id));
  }

  Future<void> _kill(String bid) async {
    final ok = await showDialog<bool>(
      context: context,
      builder: (c) => AlertDialog(
        backgroundColor: AppColors.panel,
        shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(16)),
        title: Text('Stop Kalshi instance?', style: AppTextStyles.cardTitle),
        content: Text(
          'This halts the linked instance and cancels all resting orders on this account.',
          style: AppTextStyles.body.copyWith(color: AppColors.textMuted),
        ),
        actions: [
          TextButton(onPressed: () => Navigator.pop(c, false), child: const Text('Cancel')),
          TextButton(
            onPressed: () => Navigator.pop(c, true),
            child: Text('KILL', style: AppTextStyles.body.copyWith(color: AppColors.danger, fontWeight: FontWeight.bold)),
          ),
        ],
      ),
    );
    if (ok != true) return;
    setState(() => _killing = true);
    try {
      await ref.read(kalshiRepositoryProvider).kill(bid);
      await _refresh();
    } finally {
      if (mounted) setState(() => _killing = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    final accounts = _kalshiAccounts();
    _selectedId ??= accounts.isNotEmpty ? accounts.first.id : null;
    final selectedId = _selectedId;

    return Scaffold(
      backgroundColor: Colors.transparent,
      appBar: AppBar(
        backgroundColor: Colors.transparent,
        elevation: 0,
        titleSpacing: 16,
        title: Row(
          children: [
            Icon(symbol('sports_soccer'), color: AppColors.primary, size: 22),
            const SizedBox(width: 8),
            Text('Kalshi', style: AppTextStyles.h2),
          ],
        ),
        actions: [
          if (selectedId != null)
            Padding(
              padding: const EdgeInsets.only(right: 12),
              child: _KillButton(busy: _killing, onTap: () => _kill(selectedId)),
            ),
        ],
      ),
      body: selectedId == null
          ? Padding(
              padding: const EdgeInsets.all(16),
              child: EmptyState(
                icon: symbol('sports_soccer'),
                title: 'No Kalshi account linked',
                subtitle: 'Link a Kalshi brokerage (demo or live) to monitor your portfolio, edge, and positions.',
              ),
            )
          : RefreshIndicator(
              color: AppColors.primary,
              onRefresh: _refresh,
              child: ListView(
                padding: const EdgeInsets.fromLTRB(16, 4, 16, 24),
                children: [
                  if (accounts.length > 1) ...[
                    _AccountSelector(
                      accounts: accounts,
                      selectedId: selectedId,
                      onChanged: (v) => setState(() => _selectedId = v),
                    ),
                    const SizedBox(height: 12),
                  ],
                  _PortfolioCard(brokerageId: selectedId),
                  const SizedBox(height: 12),
                  _EdgeRadarCard(brokerageId: selectedId),
                  const SizedBox(height: 12),
                  _PositionsCard(brokerageId: selectedId),
                ],
              ),
            ),
    );
  }
}

// ── Shared bits ──────────────────────────────────────────────────────────────

class _KillButton extends StatelessWidget {
  const _KillButton({required this.busy, required this.onTap});
  final bool busy;
  final VoidCallback onTap;

  @override
  Widget build(BuildContext context) {
    return GestureDetector(
      onTap: busy ? null : onTap,
      child: Container(
        padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 8),
        decoration: BoxDecoration(
          color: AppColors.fill(AppColors.danger),
          borderRadius: BorderRadius.circular(10),
          border: Border.all(color: AppColors.stroke(AppColors.danger)),
        ),
        child: Row(
          mainAxisSize: MainAxisSize.min,
          children: [
            Icon(Icons.stop_circle_outlined, size: 16, color: AppColors.danger),
            const SizedBox(width: 6),
            Text(busy ? 'Killing…' : 'Kill',
                style: AppTextStyles.meta.copyWith(color: AppColors.danger, fontWeight: FontWeight.bold)),
          ],
        ),
      ),
    );
  }
}

class _AccountSelector extends StatelessWidget {
  const _AccountSelector({required this.accounts, required this.selectedId, required this.onChanged});
  final List<BrokerageAccount> accounts;
  final String selectedId;
  final ValueChanged<String?> onChanged;

  @override
  Widget build(BuildContext context) {
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 14),
      decoration: BoxDecoration(
        color: AppColors.surface.withValues(alpha: 0.5),
        borderRadius: BorderRadius.circular(12),
        border: Border.all(color: AppColors.border),
      ),
      child: DropdownButtonHideUnderline(
        child: DropdownButton<String>(
          isExpanded: true,
          value: selectedId,
          dropdownColor: AppColors.panel,
          borderRadius: BorderRadius.circular(12),
          icon: Icon(Icons.expand_more, color: AppColors.textDim),
          style: AppTextStyles.body.copyWith(color: AppColors.textMd),
          items: accounts
              .map((a) => DropdownMenuItem(value: a.id, child: Text(a.accountName)))
              .toList(),
          onChanged: onChanged,
        ),
      ),
    );
  }
}

/// Card shell: GlassCard with an icon + uppercase eyebrow header.
class _KCard extends StatelessWidget {
  const _KCard({required this.icon, required this.title, required this.child});
  final String icon;
  final String title;
  final Widget child;

  @override
  Widget build(BuildContext context) {
    return GlassCard(
      padding: const EdgeInsets.all(16),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Row(
            children: [
              Icon(symbol(icon), color: AppColors.primary, size: 18),
              const SizedBox(width: 8),
              Text(title.toUpperCase(), style: AppTextStyles.eyebrow),
            ],
          ),
          const SizedBox(height: 12),
          child,
        ],
      ),
    );
  }
}

Widget _rowItem(String left, Widget right) => Padding(
      padding: const EdgeInsets.symmetric(vertical: 5),
      child: Row(
        children: [
          Expanded(child: Text(left, style: AppTextStyles.body.copyWith(color: AppColors.textMd), overflow: TextOverflow.ellipsis)),
          right,
        ],
      ),
    );

// ── Cards ────────────────────────────────────────────────────────────────────

class _PortfolioCard extends ConsumerWidget {
  const _PortfolioCard({required this.brokerageId});
  final String brokerageId;

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final async = ref.watch(kalshiPortfolioProvider(brokerageId));
    return _KCard(
      icon: 'monitoring',
      title: 'Portfolio value',
      child: async.when(
        loading: () => const SizedBox(height: 56, child: LoadingState()),
        error: (e, _) => ErrorBanner(message: '$e', onRetry: () => ref.invalidate(kalshiPortfolioProvider(brokerageId))),
        data: (p) {
          final positive = p.dayChange >= 0;
          final color = positive ? AppColors.success : AppColors.danger;
          return Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Row(
                crossAxisAlignment: CrossAxisAlignment.baseline,
                textBaseline: TextBaseline.alphabetic,
                children: [
                  Text('\$${p.value.toStringAsFixed(2)}',
                      style: AppTextStyles.value.copyWith(fontSize: 26, color: AppColors.textHi)),
                  const SizedBox(width: 8),
                  Icon(positive ? Icons.trending_up : Icons.trending_down, size: 16, color: color),
                  const SizedBox(width: 2),
                  Text('${positive ? '+' : '-'}\$${p.dayChange.abs().toStringAsFixed(2)}',
                      style: AppTextStyles.meta.copyWith(color: color, fontWeight: FontWeight.bold)),
                ],
              ),
              if (p.series.length > 1) ...[
                const SizedBox(height: 14),
                SizedBox(height: 56, child: CustomPaint(painter: _Sparkline(p.series), size: Size.infinite)),
              ],
            ],
          );
        },
      ),
    );
  }
}

class _EdgeRadarCard extends ConsumerWidget {
  const _EdgeRadarCard({required this.brokerageId});
  final String brokerageId;

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final async = ref.watch(kalshiEdgesProvider(brokerageId));
    return _KCard(
      icon: 'bolt',
      title: 'Edge Radar',
      child: async.when(
        loading: () => const LoadingState(),
        error: (e, _) => ErrorBanner(message: '$e', onRetry: () => ref.invalidate(kalshiEdgesProvider(brokerageId))),
        data: (edges) => edges.isEmpty
            ? Text('No +EV contracts right now.', style: AppTextStyles.body.copyWith(color: AppColors.textDim))
            : Column(
                children: edges
                    .map((e) => _rowItem(
                          '${e.marketTicker}  ·  ${e.side}',
                          Text('+${(e.edge * 100).toStringAsFixed(1)}%',
                              style: AppTextStyles.value.copyWith(color: AppColors.success, fontSize: 14)),
                        ))
                    .toList(),
              ),
      ),
    );
  }
}

class _PositionsCard extends ConsumerWidget {
  const _PositionsCard({required this.brokerageId});
  final String brokerageId;

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final async = ref.watch(kalshiPositionsProvider(brokerageId));
    return _KCard(
      icon: 'receipt_long',
      title: 'Open positions',
      child: async.when(
        loading: () => const LoadingState(),
        error: (e, _) => ErrorBanner(message: '$e', onRetry: () => ref.invalidate(kalshiPositionsProvider(brokerageId))),
        data: (positions) => positions.isEmpty
            ? Text('No open positions.', style: AppTextStyles.body.copyWith(color: AppColors.textDim))
            : Column(
                children: positions.map((p) {
                  final u = p.unrealizedCents;
                  final positive = (u ?? 0) >= 0;
                  return _rowItem(
                    '${p.marketTicker}  ${p.side} ×${p.contracts}',
                    Text(
                      u == null ? '—' : '${positive ? '+' : ''}\$${(u / 100).toStringAsFixed(2)}',
                      style: AppTextStyles.value.copyWith(
                        color: u == null ? AppColors.textDim : (positive ? AppColors.success : AppColors.danger),
                        fontSize: 14,
                      ),
                    ),
                  );
                }).toList(),
              ),
      ),
    );
  }
}

class _Sparkline extends CustomPainter {
  _Sparkline(this.data);
  final List<double> data;

  @override
  void paint(Canvas canvas, Size size) {
    if (data.length < 2) return;
    final lo = data.reduce((a, b) => a < b ? a : b);
    final hi = data.reduce((a, b) => a > b ? a : b);
    final range = (hi - lo).abs() < 1e-9 ? 1.0 : (hi - lo);
    final dx = size.width / (data.length - 1);
    final path = Path();
    for (var i = 0; i < data.length; i++) {
      final x = dx * i;
      final y = size.height - ((data[i] - lo) / range) * size.height;
      i == 0 ? path.moveTo(x, y) : path.lineTo(x, y);
    }
    // Soft violet fill under the line, matching the web chart.
    final fill = Path.from(path)
      ..lineTo(size.width, size.height)
      ..lineTo(0, size.height)
      ..close();
    canvas.drawPath(
      fill,
      Paint()
        ..shader = LinearGradient(
          begin: Alignment.topCenter,
          end: Alignment.bottomCenter,
          colors: [AppColors.primary.withValues(alpha: 0.28), AppColors.primary.withValues(alpha: 0.0)],
        ).createShader(Rect.fromLTWH(0, 0, size.width, size.height)),
    );
    canvas.drawPath(
      path,
      Paint()
        ..color = AppColors.primary
        ..style = PaintingStyle.stroke
        ..strokeWidth = 2.5
        ..strokeJoin = StrokeJoin.round,
    );
  }

  @override
  bool shouldRepaint(covariant _Sparkline old) => old.data != data;
}
