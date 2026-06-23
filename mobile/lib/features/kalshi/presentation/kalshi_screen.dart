import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../../core/theme/app_colors.dart';
import '../../dashboard/application/dashboard_controller.dart';
import '../../dashboard/data/dashboard_repository.dart';
import '../data/kalshi_repository.dart';

/// Dedicated Kalshi monitoring screen (its own bottom-nav tab). Account
/// selector + KILL in the header; portfolio value + Edge Radar + positions.
/// Pull-to-refresh invalidates the autoDispose family providers.
class KalshiScreen extends ConsumerStatefulWidget {
  const KalshiScreen({super.key});

  @override
  ConsumerState<KalshiScreen> createState() => _KalshiScreenState();
}

class _KalshiScreenState extends ConsumerState<KalshiScreen> {
  String? _selectedId;
  bool _killing = false;

  List<BrokerageAccount> _kalshiAccounts(WidgetRef ref) {
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
        title: const Text('Stop Kalshi instance?', style: TextStyle(color: AppColors.textHi)),
        content: const Text('This halts the linked instance and cancels all resting orders on this account.',
            style: TextStyle(color: AppColors.textMuted)),
        actions: [
          TextButton(onPressed: () => Navigator.pop(c, false), child: const Text('Cancel')),
          TextButton(
            onPressed: () => Navigator.pop(c, true),
            child: const Text('KILL', style: TextStyle(color: AppColors.danger, fontWeight: FontWeight.bold)),
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
    final accounts = _kalshiAccounts(ref);
    _selectedId ??= accounts.isNotEmpty ? accounts.first.id : null;
    final selectedId = _selectedId;

    return Scaffold(
      backgroundColor: AppColors.canvas,
      appBar: AppBar(
        backgroundColor: AppColors.canvas,
        title: Row(
          children: [
            const Text('⚽ Kalshi', style: TextStyle(color: AppColors.textHi, fontWeight: FontWeight.w800, fontSize: 18)),
            const SizedBox(width: 10),
            if (accounts.isNotEmpty)
              DropdownButton<String>(
                value: selectedId,
                dropdownColor: AppColors.panel,
                underline: const SizedBox.shrink(),
                style: const TextStyle(color: AppColors.textMd, fontSize: 13),
                items: accounts
                    .map((a) => DropdownMenuItem(value: a.id, child: Text(a.accountName)))
                    .toList(),
                onChanged: (v) => setState(() => _selectedId = v),
              ),
          ],
        ),
        actions: [
          if (selectedId != null)
            Padding(
              padding: const EdgeInsets.only(right: 12),
              child: TextButton.icon(
                onPressed: _killing ? null : () => _kill(selectedId),
                icon: const Icon(Icons.stop_circle, size: 18, color: Colors.white),
                label: Text(_killing ? 'KILLING…' : 'KILL', style: const TextStyle(color: Colors.white, fontWeight: FontWeight.bold)),
                style: TextButton.styleFrom(backgroundColor: AppColors.danger),
              ),
            ),
        ],
      ),
      body: selectedId == null
          ? const Center(child: Text('No Kalshi account linked.', style: TextStyle(color: AppColors.textMuted)))
          : RefreshIndicator(
              onRefresh: _refresh,
              child: ListView(
                padding: const EdgeInsets.all(16),
                children: [
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

Widget _card({required String title, required Widget child}) => Container(
      padding: const EdgeInsets.all(14),
      decoration: BoxDecoration(
        color: const Color(0xFF111C30),
        borderRadius: BorderRadius.circular(12),
        border: Border.all(color: const Color(0xFF1E293B)),
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Text(title, style: const TextStyle(color: AppColors.info, fontSize: 11, fontWeight: FontWeight.w800, letterSpacing: 0.4)),
          const SizedBox(height: 8),
          child,
        ],
      ),
    );

class _PortfolioCard extends ConsumerWidget {
  const _PortfolioCard({required this.brokerageId});
  final String brokerageId;

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final async = ref.watch(kalshiPortfolioProvider(brokerageId));
    return _card(
      title: '📊 Portfolio value',
      child: async.when(
        loading: () => const SizedBox(height: 60, child: Center(child: CircularProgressIndicator(strokeWidth: 2))),
        error: (e, _) => Text('Error: $e', style: const TextStyle(color: AppColors.danger, fontSize: 12)),
        data: (p) {
          final positive = p.dayChange >= 0;
          return Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Row(
                crossAxisAlignment: CrossAxisAlignment.baseline,
                textBaseline: TextBaseline.alphabetic,
                children: [
                  Text('\$${p.value.toStringAsFixed(2)}',
                      style: const TextStyle(color: AppColors.textHi, fontSize: 24, fontWeight: FontWeight.w800)),
                  const SizedBox(width: 8),
                  Text('${positive ? '▲' : '▼'} \$${p.dayChange.abs().toStringAsFixed(2)}',
                      style: TextStyle(color: positive ? AppColors.success : AppColors.danger, fontWeight: FontWeight.bold, fontSize: 13)),
                ],
              ),
              if (p.series.length > 1) ...[
                const SizedBox(height: 10),
                SizedBox(height: 60, child: CustomPaint(painter: _Sparkline(p.series, positive), size: Size.infinite)),
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
    return _card(
      title: '⚡ Edge Radar',
      child: async.when(
        loading: () => const Text('Loading…', style: TextStyle(color: AppColors.textDim, fontSize: 12)),
        error: (e, _) => Text('Error: $e', style: const TextStyle(color: AppColors.danger, fontSize: 12)),
        data: (edges) => edges.isEmpty
            ? const Text('No +EV contracts right now.', style: TextStyle(color: AppColors.textDim, fontSize: 12))
            : Column(
                children: edges
                    .map((e) => Padding(
                          padding: const EdgeInsets.symmetric(vertical: 4),
                          child: Row(
                            mainAxisAlignment: MainAxisAlignment.spaceBetween,
                            children: [
                              Expanded(child: Text('${e.marketTicker} · ${e.side}', style: const TextStyle(color: AppColors.textMd, fontSize: 12), overflow: TextOverflow.ellipsis)),
                              Text('+${(e.edge * 100).toStringAsFixed(1)}%', style: const TextStyle(color: AppColors.success, fontWeight: FontWeight.bold, fontSize: 12)),
                            ],
                          ),
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
    return _card(
      title: '📑 Open positions',
      child: async.when(
        loading: () => const Text('Loading…', style: TextStyle(color: AppColors.textDim, fontSize: 12)),
        error: (e, _) => Text('Error: $e', style: const TextStyle(color: AppColors.danger, fontSize: 12)),
        data: (positions) => positions.isEmpty
            ? const Text('No open positions.', style: TextStyle(color: AppColors.textDim, fontSize: 12))
            : Column(
                children: positions.map((p) {
                  final u = p.unrealizedCents;
                  final positive = (u ?? 0) >= 0;
                  return Padding(
                    padding: const EdgeInsets.symmetric(vertical: 4),
                    child: Row(
                      mainAxisAlignment: MainAxisAlignment.spaceBetween,
                      children: [
                        Expanded(child: Text('${p.marketTicker} ${p.side} ×${p.contracts}', style: const TextStyle(color: AppColors.textMd, fontSize: 12), overflow: TextOverflow.ellipsis)),
                        Text(u == null ? '—' : '${positive ? '+' : ''}\$${(u / 100).toStringAsFixed(2)}',
                            style: TextStyle(color: positive ? AppColors.success : AppColors.danger, fontWeight: FontWeight.bold, fontSize: 12)),
                      ],
                    ),
                  );
                }).toList(),
              ),
      ),
    );
  }
}

class _Sparkline extends CustomPainter {
  _Sparkline(this.data, this.positive);
  final List<double> data;
  final bool positive;

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
      if (i == 0) {
        path.moveTo(x, y);
      } else {
        path.lineTo(x, y);
      }
    }
    final paint = Paint()
      ..color = positive ? AppColors.chartUp : AppColors.chartDown
      ..style = PaintingStyle.stroke
      ..strokeWidth = 2.5
      ..strokeJoin = StrokeJoin.round;
    canvas.drawPath(path, paint);
  }

  @override
  bool shouldRepaint(covariant _Sparkline old) => old.data != data;
}
