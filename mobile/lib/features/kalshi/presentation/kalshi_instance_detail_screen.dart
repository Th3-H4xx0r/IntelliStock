import 'dart:async';

import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';

import '../../../core/theme/app_colors.dart';
import '../../../core/theme/app_text_styles.dart';
import '../../../core/widgets/common_widgets.dart';
import '../../../core/widgets/glass_card.dart';
import '../../../core/widgets/material_symbols.dart';
import '../../instances/presentation/live_logs_panel.dart';
import '../data/kalshi_repository.dart';
import 'kalshi_portfolio_hero.dart';
import 'kalshi_screen.dart' show KalshiInstanceSheet;

/// Kalshi instance detail: status + Start/Stop/KILL, decision summary, the
/// LLM-reasoned decision log, and live logs.
class KalshiInstanceDetailScreen extends ConsumerStatefulWidget {
  const KalshiInstanceDetailScreen({super.key, required this.instanceId});
  final String instanceId;

  @override
  ConsumerState<KalshiInstanceDetailScreen> createState() => _State();
}

class _State extends ConsumerState<KalshiInstanceDetailScreen> {
  bool _busy = false;
  final Set<int> _expanded = {};
  int _decPage = 0;
  static const _decPageSize = 8;
  Timer? _liveTimer;
  Timer? _tickTimer;
  DateTime _now = DateTime.now();   // ticks every 1s for the kickoff countdowns

  @override
  void initState() {
    super.initState();
    _liveTimer = Timer.periodic(const Duration(seconds: 15), (_) {
      if (mounted) {
        ref.invalidate(kalshiInstanceLiveProvider(widget.instanceId));
        ref.invalidate(kalshiInstanceOrdersProvider(widget.instanceId));
      }
    });
    _tickTimer = Timer.periodic(const Duration(seconds: 1), (_) {
      if (mounted) setState(() => _now = DateTime.now());
    });
  }

  @override
  void dispose() {
    _liveTimer?.cancel();
    _tickTimer?.cancel();
    _scrubIdx.dispose();
    super.dispose();
  }

  Future<void> _refresh() async {
    ref.invalidate(kalshiInstanceDetailProvider(widget.instanceId));
    ref.invalidate(kalshiInstanceDecisionsProvider(widget.instanceId));
    ref.invalidate(kalshiInstanceLiveProvider(widget.instanceId));
    ref.invalidate(kalshiInstanceOrdersProvider(widget.instanceId));
    final bid = ref.read(kalshiInstanceDetailProvider(widget.instanceId)).value?['brokerage_id']?.toString();
    if (bid != null && bid.isNotEmpty) ref.invalidate(kalshiPositionsProvider(bid));
  }

  // Edit this instance's config via the shared sheet (prefilled, PATCHes on save).
  void _editInstance(Map<String, dynamic> detail) {
    final cfg = (detail['config'] as Map?)?.cast<String, dynamic>() ?? <String, dynamic>{};
    showModalBottomSheet(
      context: context,
      isScrollControlled: true,
      backgroundColor: Colors.transparent,
      builder: (_) => KalshiInstanceSheet(
        accounts: const [],
        initialBrokerageId: detail['brokerage_id']?.toString() ?? '',
        editInstanceId: widget.instanceId,
        editName: detail['name']?.toString(),
        editConfig: cfg,
        onCreated: (_) => _refresh(),
      ),
    );
  }

  Future<void> _startStop(bool start) async {
    if (_busy) return;
    setState(() => _busy = true);
    try {
      final repo = ref.read(kalshiRepositoryProvider);
      start ? await repo.startInstance(widget.instanceId) : await repo.stopInstance(widget.instanceId);
      await _refresh();
    } catch (e) {
      // surface the backend guard (e.g. "another instance is already running on
      // this brokerage"); ApiError.toString() is the flattened server message.
      if (mounted) {
        ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text(e.toString())));
      }
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  Future<void> _delete() async {
    final ok = await showDialog<bool>(
      context: context,
      builder: (c) => AlertDialog(
        backgroundColor: AppColors.panel,
        title: Text('Delete instance?', style: AppTextStyles.cardTitle),
        content: Text('This cannot be undone.', style: AppTextStyles.body.copyWith(color: AppColors.textMuted)),
        actions: [
          TextButton(onPressed: () => Navigator.pop(c, false), child: const Text('Cancel')),
          TextButton(onPressed: () => Navigator.pop(c, true),
              child: Text('Delete', style: AppTextStyles.body.copyWith(color: AppColors.danger, fontWeight: FontWeight.bold))),
        ],
      ),
    );
    if (ok != true) return;
    setState(() => _busy = true);
    try {
      await ref.read(kalshiRepositoryProvider).deleteInstance(widget.instanceId);
      if (mounted) context.pop();
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  String _pct(dynamic v) => v == null ? '—' : '${((v as num) * 100).toStringAsFixed(1)}%';
  Color _decColor(String d) => {
        'placed': AppColors.success,
        'queued': AppColors.warning,
        'blocked': AppColors.danger,
      }[d] ?? AppColors.textDim;

  @override
  Widget build(BuildContext context) {
    final detailAsync = ref.watch(kalshiInstanceDetailProvider(widget.instanceId));
    final decAsync = ref.watch(kalshiInstanceDecisionsProvider(widget.instanceId));
    final liveAsync = ref.watch(kalshiInstanceLiveProvider(widget.instanceId));
    final ordersAsync = ref.watch(kalshiInstanceOrdersProvider(widget.instanceId));
    final detail = detailAsync.value;
    final running = detail?['running'] == true;

    final topInset = MediaQuery.paddingOf(context).top + kToolbarHeight;
    return Scaffold(
      // Opaque canvas: a transparent Scaffold let the previous screen bleed
      // through during the push-in transition and the iOS edge-swipe-back. The
      // KalshiCrown gradient still sits on top for the purple header look.
      backgroundColor: AppColors.canvas,
      extendBodyBehindAppBar: true,
      appBar: AppBar(
        backgroundColor: Colors.transparent,
        elevation: 0,
        title: Text(detail?['name']?.toString() ?? 'Kalshi instance', style: AppTextStyles.h2),
        actions: detail == null
            ? null
            : [
                Padding(
                  padding: const EdgeInsets.only(right: 12),
                  child: GestureDetector(
                    onTap: _busy ? null : () => _startStop(!running),
                    child: Container(
                      padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 8),
                      decoration: BoxDecoration(
                        color: AppColors.fill(running ? AppColors.warning : AppColors.primary),
                        borderRadius: BorderRadius.circular(10),
                        border: Border.all(color: AppColors.stroke(running ? AppColors.warning : AppColors.primary)),
                      ),
                      child: Row(mainAxisSize: MainAxisSize.min, children: [
                        Icon(running ? Icons.pause : Icons.play_arrow, size: 16,
                            color: running ? AppColors.warning : AppColors.primary),
                        const SizedBox(width: 6),
                        Text(running ? 'Stop' : 'Start',
                            style: AppTextStyles.meta.copyWith(
                                color: running ? AppColors.warning : AppColors.primary, fontWeight: FontWeight.bold)),
                      ]),
                    ),
                  ),
                ),
                IconButton(
                  onPressed: _busy ? null : () => _editInstance(detail),
                  icon: Icon(Icons.tune, color: AppColors.primary),
                  tooltip: 'Edit config',
                ),
                IconButton(
                  onPressed: _busy ? null : _delete,
                  icon: Icon(Icons.delete_outline, color: AppColors.danger),
                  tooltip: 'Delete',
                ),
              ],
      ),
      body: detail == null
          ? const Padding(padding: EdgeInsets.all(24), child: LoadingState())
          : Stack(children: [
              Positioned(top: 0, left: 0, right: 0, child: const KalshiCrown(height: 420)),
              RefreshIndicator(
              color: AppColors.primary,
              onRefresh: _refresh,
              child: ListView(
                padding: EdgeInsets.fromLTRB(16, topInset + 4, 16, 24),
                children: [
                  Row(children: [
                    _badge(running ? 'Running' : 'Stopped', running ? AppColors.success : AppColors.textDim),
                    const SizedBox(width: 8),
                    _badge(detail['environment'] == 'live' ? 'Live' : 'Paper',
                        detail['environment'] == 'live' ? AppColors.danger : AppColors.primary),
                  ]),
                  const SizedBox(height: 12),
                  _summary(decAsync.value?['summary'] as Map<String, dynamic>?),
                  const SizedBox(height: 12),
                  _portfolioChart(detail['brokerage_id']?.toString() ?? ''),
                  const SizedBox(height: 12),
                  _liveCards(liveAsync),
                  _positionsCard(detail['brokerage_id']?.toString() ?? ''),
                  _ordersCard(ordersAsync),
                  const SizedBox(height: 12),
                  _pregameAnalysis(decAsync),
                  const SizedBox(height: 12),
                  _decisionLog(decAsync),
                  const SizedBox(height: 12),
                  GlassCard(
                    frosted: true,                    padding: const EdgeInsets.all(14),
                    child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
                      Row(children: [
                        Icon(symbol('terminal'), color: AppColors.primary, size: 18),
                        const SizedBox(width: 8),
                        Text('LIVE LOGS', style: AppTextStyles.eyebrow),
                      ]),
                      const SizedBox(height: 10),
                      SizedBox(height: 300, child: LiveLogsPanel(key: ValueKey(widget.instanceId), instanceId: widget.instanceId)),
                    ]),
                  ),
                ],
              ),
            ),
            ]),
    );
  }

  Widget _badge(String t, Color c) => Container(
        padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 3),
        decoration: BoxDecoration(color: AppColors.fill(c), borderRadius: BorderRadius.circular(6)),
        child: Text(t, style: AppTextStyles.nano.copyWith(color: c, fontWeight: FontWeight.bold)),
      );

  Widget _summary(Map<String, dynamic>? s) {
    final placed = (s?['placed'] ?? 0).toString();
    final skipped = (s?['skipped'] ?? 0).toString();
    final queued = (s?['queued'] ?? 0).toString();
    final blocked = (s?['blocked'] ?? 0).toString();
    return Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
      Row(children: [
        Expanded(child: StatTile(label: 'Placed', value: placed, valueColor: AppColors.success)),
        const SizedBox(width: 8),
        Expanded(child: StatTile(label: 'Skipped', value: skipped)),
        const SizedBox(width: 8),
        Expanded(child: StatTile(label: 'Queued', value: queued, valueColor: AppColors.warning)),
        const SizedBox(width: 8),
        Expanded(child: StatTile(label: 'Blocked', value: blocked, valueColor: AppColors.danger)),
      ]),
      _paperPnl(s),
    ]);
  }

  // Paper P&L line: realized (closed) + live unrealized (open), each green/red.
  Widget _paperPnl(Map<String, dynamic>? s) {
    final realC = (s?['realized_pnl_cents'] as num?)?.toDouble();
    final unrealC = (s?['unrealized_pnl_cents'] as num?)?.toDouble();
    final openPos = (s?['open_positions'] as num?)?.toInt();
    if (realC == null && unrealC == null) return const SizedBox.shrink();
    final realPos = (realC ?? 0) >= 0;
    final unrealPos = (unrealC ?? 0) >= 0;
    String dollars(double cents) =>
        '${cents >= 0 ? '+' : '-'}\$${(cents.abs() / 100).toStringAsFixed(2)}';
    return Padding(
      padding: const EdgeInsets.only(top: 8),
      child: Row(children: [
        Icon(Icons.science_outlined, color: AppColors.warning, size: 14),
        const SizedBox(width: 6),
        Text('Paper P&L', style: AppTextStyles.nano.copyWith(color: AppColors.textDim, fontWeight: FontWeight.bold)),
        const SizedBox(width: 8),
        Expanded(
          child: RichText(
            overflow: TextOverflow.ellipsis,
            text: TextSpan(children: [
              TextSpan(
                text: 'realized ${realC == null ? '—' : dollars(realC)}',
                style: AppTextStyles.nano.copyWith(
                    color: realC == null ? AppColors.textDim : (realPos ? AppColors.success : AppColors.danger),
                    fontWeight: FontWeight.w600),
              ),
              TextSpan(text: '  ·  ', style: AppTextStyles.nano.copyWith(color: AppColors.textFaint)),
              TextSpan(
                text: 'unrealized ${unrealC == null ? '—' : dollars(unrealC)}',
                style: AppTextStyles.nano.copyWith(
                    color: unrealC == null ? AppColors.textDim : (unrealPos ? AppColors.success : AppColors.danger),
                    fontWeight: FontWeight.w600),
              ),
              TextSpan(
                text: openPos == null ? ' (live)' : ' (live · $openPos open)',
                style: AppTextStyles.nano.copyWith(color: AppColors.textFaint),
              ),
            ]),
          ),
        ),
      ]),
    );
  }

  final ValueNotifier<int?> _scrubIdx = ValueNotifier<int?>(null);

  Widget _portfolioChart(String brokerageId) {
    if (brokerageId.isEmpty) return const SizedBox.shrink();
    final async = ref.watch(kalshiPortfolioProvider(brokerageId));
    return KalshiPortfolioHero(
      title: 'PORTFOLIO VALUE',
      async: async,
      scrubIdx: _scrubIdx,
      onRetry: () => ref.invalidate(kalshiPortfolioProvider(brokerageId)),
    );
  }

  Widget _liveCards(AsyncValue<Map<String, dynamic>> liveAsync) {
    final matches = (liveAsync.value?['matches'] as List?) ?? const [];
    if (matches.isEmpty) return const SizedBox.shrink();
    return Padding(
      padding: const EdgeInsets.only(bottom: 12),
      child: GlassCard(
   frosted: true,        padding: const EdgeInsets.all(14),
        child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
          Row(children: [
            Container(width: 8, height: 8, decoration: const BoxDecoration(color: Color(0xFFEF4444), shape: BoxShape.circle)),
            const SizedBox(width: 8),
            Text('LIVE NOW · ${matches.length}', style: AppTextStyles.eyebrow),
          ]),
          const SizedBox(height: 10),
          ...matches.map((raw) => _liveCard(raw as Map<String, dynamic>)),
        ]),
      ),
    );
  }

  Widget _liveCard(Map<String, dynamic> m) {
    final score = m['score'] as Map<String, dynamic>?;
    final probs = (m['market_probs'] as Map?)?.cast<String, dynamic>() ?? const {};
    final decisions = (m['decisions'] as List?) ?? const [];
    final news = (m['news'] as String?) ?? '';
    String clock;
    if (score != null && (score['clock']?.toString().isNotEmpty ?? false)) {
      clock = score['clock'].toString();
    } else if (m['elapsed_min'] != null) {
      clock = "${(m['elapsed_min'] as num).round()}'";
    } else {
      clock = 'LIVE';
    }
    return Container(
      margin: const EdgeInsets.only(bottom: 8),
      padding: const EdgeInsets.all(12),
      decoration: BoxDecoration(
        color: AppColors.fill(AppColors.surface),
        borderRadius: BorderRadius.circular(12),
        border: Border.all(color: AppColors.border),
      ),
      child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
        // score header with flags
        Row(children: [
          _teamBadge((m['home_logo'] ?? '').toString(), (m['home'] ?? '').toString()),
          Expanded(child: Column(children: [
            Text('${score != null ? (score['home'] ?? 0) : 0}  :  ${score != null ? (score['away'] ?? 0) : 0}',
                style: AppTextStyles.h2.copyWith(color: AppColors.textHi, fontWeight: FontWeight.bold)),
            const SizedBox(height: 4),
            Row(mainAxisAlignment: MainAxisAlignment.center, children: [
              Container(width: 6, height: 6, decoration: const BoxDecoration(color: Color(0xFF34D399), shape: BoxShape.circle)),
              const SizedBox(width: 5),
              Text(clock, style: AppTextStyles.nano.copyWith(color: AppColors.success, fontWeight: FontWeight.bold)),
            ]),
          ])),
          _teamBadge((m['away_logo'] ?? '').toString(), (m['away'] ?? '').toString()),
        ]),
        const SizedBox(height: 10),
        ...probs.entries.map((e) {
          final v = (((e.value as num?)?.toDouble()) ?? 0.0).clamp(0.0, 1.0);
          return Padding(
            padding: const EdgeInsets.only(bottom: 5),
            child: Row(children: [
              SizedBox(width: 72, child: Text(_sideLabel(m, e.key),
                  style: AppTextStyles.nano.copyWith(color: AppColors.textDim), overflow: TextOverflow.ellipsis)),
              Expanded(child: ClipRRect(borderRadius: BorderRadius.circular(3),
                  child: LinearProgressIndicator(value: v, minHeight: 6,
                      backgroundColor: AppColors.fill(AppColors.surface),
                      valueColor: AlwaysStoppedAnimation(AppColors.primary)))),
              const SizedBox(width: 8),
              Text('${(v * 100).round()}%', style: AppTextStyles.nano.copyWith(color: AppColors.textMuted)),
            ]),
          );
        }),
        if (news.isNotEmpty)
          Padding(padding: const EdgeInsets.only(top: 4), child: Text(news.split('\n').first,
              style: AppTextStyles.nano.copyWith(color: AppColors.textDim), maxLines: 2, overflow: TextOverflow.ellipsis)),
        if (decisions.isNotEmpty)
          Padding(
            padding: const EdgeInsets.only(top: 8),
            child: Wrap(spacing: 6, runSpacing: 6, children: decisions.take(4).map((d) {
              final dm = d as Map<String, dynamic>;
              final act = (dm['action'] ?? '').toString();
              final c = _actionColor(act);
              final sz = (dm['size'] != null && dm['size'] != 0) ? ' ${dm['size']}' : '';
              return Container(
                padding: const EdgeInsets.symmetric(horizontal: 6, vertical: 2),
                decoration: BoxDecoration(color: AppColors.fill(c), borderRadius: BorderRadius.circular(4)),
                child: Text('${act.toUpperCase()}$sz', style: AppTextStyles.nano.copyWith(color: c, fontWeight: FontWeight.bold)),
              );
            }).toList()),
          ),
      ]),
    );
  }

  String _sideLabel(Map<String, dynamic> m, String side) {
    if (side == 'home') return m['home']?.toString() ?? 'Home';
    if (side == 'away') return m['away']?.toString() ?? 'Away';
    return 'Draw';
  }

  Color _actionColor(String a) => {
        'open': AppColors.success,
        'add': AppColors.success,
        'reduce': AppColors.warning,
        'exit': AppColors.danger,
      }[a] ?? AppColors.textDim;

  Widget _crest(String url, String fallbackName) {
    final init = fallbackName.replaceAll(RegExp(r'[^A-Za-z ]'), '').split(' ')
        .where((w) => w.isNotEmpty).map((w) => w[0]).take(2).join();
    return ClipOval(
      child: url.isNotEmpty
          ? Image.network(url, width: 30, height: 30, fit: BoxFit.contain,
              errorBuilder: (_, __, ___) => _crestFallback(init))
          : _crestFallback(init),
    );
  }

  Widget _crestFallback(String init) => Container(
        width: 30, height: 30, color: AppColors.surface, alignment: Alignment.center,
        child: Text(init, style: AppTextStyles.nano.copyWith(color: AppColors.textDim, fontWeight: FontWeight.bold)),
      );

  Widget _orderTile(Map<String, dynamic> o, {required bool filled}) {
    final match = (o['match'] ?? o['market_ticker'] ?? '').toString();
    final pick = (o['pick_label'] ?? o['side'] ?? '').toString();
    final edge = (o['edge'] as num?)?.toDouble();
    return Container(
      margin: const EdgeInsets.only(bottom: 8),
      padding: const EdgeInsets.all(11),
      decoration: BoxDecoration(
        color: AppColors.fill(AppColors.surface),
        borderRadius: BorderRadius.circular(10),
        border: Border.all(color: AppColors.border),
      ),
      child: Row(children: [
        _crest((o['pick_logo'] ?? '').toString(), pick.replaceAll(' to win', '')),
        const SizedBox(width: 10),
        Expanded(child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
          Row(children: [
            Expanded(child: Text(match, maxLines: 1, overflow: TextOverflow.ellipsis,
                style: AppTextStyles.body.copyWith(color: AppColors.textHi, fontWeight: FontWeight.w600))),
            if (!filled && o['in_play'] == true)
              Text('LIVE', style: AppTextStyles.nano.copyWith(color: AppColors.danger, fontWeight: FontWeight.bold)),
            if (filled)
              Text((o['action'] ?? '').toString().toUpperCase(),
                  style: AppTextStyles.nano.copyWith(
                      color: o['action'] == 'sell' ? AppColors.warning : AppColors.success,
                      fontWeight: FontWeight.bold)),
          ]),
          const SizedBox(height: 4),
          Row(children: [
            Expanded(child: Text(pick, maxLines: 1, overflow: TextOverflow.ellipsis,
                style: AppTextStyles.nano.copyWith(color: AppColors.primary, fontWeight: FontWeight.w500))),
            if (filled)
              Text('${o['contracts']} @ ${o['price_cents']}¢',
                  style: AppTextStyles.nano.copyWith(color: AppColors.textDim))
            else ...[
              Text('${o['contracts'] ?? o['size'] ?? 0}×  ', style: AppTextStyles.nano.copyWith(color: AppColors.textDim)),
              if (edge != null)
                Text('${edge >= 0 ? '+' : ''}${(edge * 100).toStringAsFixed(1)}%',
                    style: AppTextStyles.nano.copyWith(
                        color: edge >= 0 ? AppColors.success : AppColors.danger, fontWeight: FontWeight.w600)),
            ],
          ]),
        ])),
      ]),
    );
  }

  Widget _teamBadge(String logo, String name) {
    return SizedBox(
      width: 76,
      child: Column(children: [
        ClipOval(
          child: logo.isNotEmpty
              ? Image.network(logo, width: 44, height: 44, fit: BoxFit.contain,
                  errorBuilder: (_, __, ___) => _badgeFallback(name))
              : _badgeFallback(name),
        ),
        const SizedBox(height: 6),
        Text(name, maxLines: 1, overflow: TextOverflow.ellipsis, textAlign: TextAlign.center,
            style: AppTextStyles.nano.copyWith(color: AppColors.textMuted)),
      ]),
    );
  }

  Widget _badgeFallback(String name) {
    final init = name.replaceAll(RegExp(r'[^A-Za-z ]'), '').split(' ')
        .map((w) => w.isNotEmpty ? w[0] : '').join();
    return Container(
      width: 44, height: 44, color: AppColors.surface, alignment: Alignment.center,
      child: Text(init.length > 3 ? init.substring(0, 3) : init,
          style: AppTextStyles.nano.copyWith(color: AppColors.textDim, fontWeight: FontWeight.bold)),
    );
  }

  Widget _positionsCard(String brokerageId) {
    if (brokerageId.isEmpty) return const SizedBox.shrink();
    final async = ref.watch(kalshiPositionsProvider(brokerageId));
    final positions = async.value ?? const [];
    if (positions.isEmpty) return const SizedBox.shrink();
    return Padding(
      padding: const EdgeInsets.only(bottom: 12),
      child: GlassCard(
        frosted: true,
        padding: const EdgeInsets.all(14),
        child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
          Row(children: [
            Icon(symbol('account_balance_wallet'), color: AppColors.primary, size: 18),
            const SizedBox(width: 8),
            Text('OPEN POSITIONS · ${positions.length}', style: AppTextStyles.eyebrow),
          ]),
          const SizedBox(height: 10),
          ...positions.map(kalshiPositionTile),
        ]),
      ),
    );
  }

  Widget _ordersCard(AsyncValue<Map<String, dynamic>> ordersAsync) {
    final data = ordersAsync.value ?? const <String, dynamic>{};
    final placed = (data['placed'] as List?) ?? const [];
    final fills = (data['fills'] as List?) ?? const [];
    final mock = (data['mock'] as List?) ?? const [];
    final mockHistory = (data['mock_history'] as List?) ?? const [];
    return GlassCard(
   frosted: true,      padding: const EdgeInsets.all(14),
      child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
        Row(children: [
          Icon(Icons.receipt_long_outlined, color: AppColors.primary, size: 18),
          const SizedBox(width: 8),
          Text('ORDERS', style: AppTextStyles.eyebrow),
        ]),
        const SizedBox(height: 10),
        Row(children: [
          Icon(Icons.pending_outlined, color: AppColors.warning, size: 14),
          const SizedBox(width: 5),
          Text('PENDING · ${placed.length}',
              style: AppTextStyles.nano.copyWith(color: AppColors.textDim, fontWeight: FontWeight.bold)),
        ]),
        const SizedBox(height: 6),
        if (placed.isEmpty)
          Text('No resting orders — everything filled.', style: AppTextStyles.nano.copyWith(color: AppColors.textDim)),
        ...placed.take(12).map((o) => _orderTile(o as Map<String, dynamic>, filled: false)),
        if (fills.isNotEmpty) ...[
          const SizedBox(height: 12),
          Row(children: [
            Icon(Icons.check_circle_outline, color: AppColors.success, size: 14),
            const SizedBox(width: 5),
            Text('FILLED · ${fills.length}',
                style: AppTextStyles.nano.copyWith(color: AppColors.textDim, fontWeight: FontWeight.bold)),
          ]),
          const SizedBox(height: 6),
          ...fills.take(12).map((f) => _orderTile(f as Map<String, dynamic>, filled: true)),
        ],
        const SizedBox(height: 12),
        Row(children: [
          Icon(Icons.science_outlined, color: AppColors.warning, size: 14),
          const SizedBox(width: 5),
          Text('MOCK POSITIONS · ${mock.length}',
              style: AppTextStyles.nano.copyWith(color: AppColors.textDim, fontWeight: FontWeight.bold)),
        ]),
        const SizedBox(height: 6),
        if (mock.isEmpty)
          Text('No mock (paper) positions.', style: AppTextStyles.nano.copyWith(color: AppColors.textDim)),
        ...mock.take(12).map((m) => _mockTile(m as Map<String, dynamic>)),
        if (mockHistory.isNotEmpty) ...[
          const SizedBox(height: 12),
          Row(children: [
            Icon(Icons.history, color: AppColors.warning, size: 14),
            const SizedBox(width: 5),
            Text('MOCK FILLED · ${mockHistory.length}',
                style: AppTextStyles.nano.copyWith(color: AppColors.textDim, fontWeight: FontWeight.bold)),
          ]),
          const SizedBox(height: 6),
          ...mockHistory.take(12).map((m) => _mockHistoryTile(m as Map<String, dynamic>)),
        ],
      ]),
    );
  }

  // Settled/expired paper trade -> filled-orders history with realized P&L + MOCK tag.
  Widget _mockHistoryTile(Map<String, dynamic> m) {
    final match = (m['match'] ?? m['market_ticker'] ?? '').toString();
    final pick = (m['pick_label'] ?? m['side'] ?? '').toString();
    final contracts = (m['contracts'] as num?)?.toInt() ?? 0;
    final entryCents = (m['price_cents'] as num?)?.toInt();
    final rCents = (m['realized_pnl_cents'] as num?)?.toDouble();
    final rPos = (rCents ?? 0) >= 0;
    final outcome = (m['outcome'] ?? '').toString();
    return Container(
      margin: const EdgeInsets.only(bottom: 8),
      padding: const EdgeInsets.all(11),
      decoration: BoxDecoration(
        color: AppColors.fill(AppColors.surface),
        borderRadius: BorderRadius.circular(10),
        border: Border.all(color: AppColors.border),
      ),
      child: Row(children: [
        _crest((m['pick_logo'] ?? '').toString(), pick.replaceAll(' to win', '')),
        const SizedBox(width: 10),
        Expanded(child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
          Row(children: [
            Expanded(child: Text(match, maxLines: 1, overflow: TextOverflow.ellipsis,
                style: AppTextStyles.body.copyWith(color: AppColors.textHi, fontWeight: FontWeight.w600))),
            const SizedBox(width: 6),
            Container(
              padding: const EdgeInsets.symmetric(horizontal: 6, vertical: 2),
              decoration: BoxDecoration(color: AppColors.fill(AppColors.warning), borderRadius: BorderRadius.circular(4)),
              child: Text('MOCK', style: AppTextStyles.nano.copyWith(color: AppColors.warning, fontWeight: FontWeight.bold)),
            ),
            const SizedBox(width: 8),
            Text(
              rCents == null ? '—' : '${rPos ? '+' : '-'}\$${(rCents.abs() / 100).toStringAsFixed(2)}',
              style: AppTextStyles.nano.copyWith(
                  color: rCents == null ? AppColors.textDim : (rPos ? AppColors.success : AppColors.danger),
                  fontWeight: FontWeight.bold),
            ),
          ]),
          const SizedBox(height: 4),
          Row(children: [
            Expanded(child: Text(pick, maxLines: 1, overflow: TextOverflow.ellipsis,
                style: AppTextStyles.nano.copyWith(color: AppColors.primary, fontWeight: FontWeight.w500))),
            Text('$contracts @ ${entryCents ?? '—'}¢${outcome.isNotEmpty ? ' · $outcome' : ''}',
                style: AppTextStyles.nano.copyWith(color: AppColors.textDim)),
          ]),
        ])),
      ]),
    );
  }

  // Paper "mock" position: live unrealized P&L plus the entry→mark cents trail.
  Widget _mockTile(Map<String, dynamic> m) {
    final match = (m['match'] ?? m['market_ticker'] ?? '').toString();
    final pick = (m['pick_label'] ?? m['side'] ?? '').toString();
    final contracts = (m['contracts'] as num?)?.toInt() ?? 0;
    final entryCents = (m['entry_cents'] as num?)?.toInt();
    final markCents = (m['mark_cents'] as num?)?.toInt();
    final upCents = (m['unrealized_pnl_cents'] as num?)?.toDouble();
    final upPos = (upCents ?? 0) > 0;
    // Total current value of the position = contracts × current mark (fallback entry).
    final value = (contracts * (markCents ?? entryCents ?? 0)) / 100.0;
    return Container(
      margin: const EdgeInsets.only(bottom: 8),
      padding: const EdgeInsets.all(11),
      decoration: BoxDecoration(
        color: AppColors.fill(AppColors.surface),
        borderRadius: BorderRadius.circular(10),
        border: Border.all(color: AppColors.border),
      ),
      child: Row(children: [
        _crest((m['pick_logo'] ?? '').toString(), pick.replaceAll(' to win', '')),
        const SizedBox(width: 10),
        Expanded(child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
          Row(children: [
            Expanded(child: Text(match, maxLines: 1, overflow: TextOverflow.ellipsis,
                style: AppTextStyles.body.copyWith(color: AppColors.textHi, fontWeight: FontWeight.w600))),
            const SizedBox(width: 6),
            Container(
              padding: const EdgeInsets.symmetric(horizontal: 6, vertical: 2),
              decoration: BoxDecoration(color: AppColors.fill(AppColors.warning), borderRadius: BorderRadius.circular(4)),
              child: Text('MOCK', style: AppTextStyles.nano.copyWith(color: AppColors.warning, fontWeight: FontWeight.bold)),
            ),
            const SizedBox(width: 8),
            // Total position value — big bold
            Text('\$${value.toStringAsFixed(2)}',
                style: AppTextStyles.body.copyWith(color: AppColors.textHi, fontWeight: FontWeight.bold, fontSize: 17)),
          ]),
          const SizedBox(height: 4),
          Row(children: [
            Expanded(child: Text(pick, maxLines: 1, overflow: TextOverflow.ellipsis,
                style: AppTextStyles.nano.copyWith(color: AppColors.primary, fontWeight: FontWeight.w500))),
            Text(
              upCents == null ? '—' : '${upPos ? '+' : '-'}\$${(upCents.abs() / 100).toStringAsFixed(2)} P&L',
              style: AppTextStyles.nano.copyWith(
                  color: upCents == null ? AppColors.textDim : (upPos ? AppColors.success : AppColors.danger),
                  fontWeight: FontWeight.w600),
            ),
          ]),
          const SizedBox(height: 2),
          Text('$contracts @ ${entryCents ?? '—'}¢ → ${markCents ?? '—'}¢',
              style: AppTextStyles.nano.copyWith(color: AppColors.textDim)),
        ])),
      ]),
    );
  }

  // ── Pregame analysis ──────────────────────────────────────────────────
  // The flat Decision Log scatters a single match's three sides (home / draw /
  // away). This section reuses the SAME already-fetched decisions list, groups
  // it by fixture, and shows the pregame read + per-side edges together so each
  // game reads as one card.

  Widget _pregameAnalysis(AsyncValue<Map<String, dynamic>> decAsync) {
    return GlassCard(
      frosted: true,
      padding: const EdgeInsets.all(14),
      child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
        Row(children: [
          Icon(symbol('sports_soccer'), color: AppColors.primary, size: 18),
          const SizedBox(width: 8),
          Text('PREGAME ANALYSIS', style: AppTextStyles.eyebrow),
        ]),
        const SizedBox(height: 12),
        decAsync.when(
          loading: () => const LoadingState(),
          error: (e, _) => ErrorBanner(message: '$e', onRetry: _refresh),
          data: (d) {
            final rows = ((d['decisions'] as List?) ?? const [])
                .whereType<Map>()
                .map((r) => r.cast<String, dynamic>())
                .toList();
            if (rows.isEmpty) {
              return Text('No games analyzed yet — picks will appear here once the bot scans the slate.',
                  style: AppTextStyles.body.copyWith(color: AppColors.textDim));
            }
            // Group by fixture, preserving the first-seen order within a game.
            final groups = <String, List<Map<String, dynamic>>>{};
            for (final r in rows) {
              final key = (r['fixture_id'] ?? r['match'] ?? '').toString();
              (groups[key] ??= []).add(r);
            }
            // Candidate rows are keyed per-tick, so a side accumulates many rows.
            // Collapse each game to ONE row per side (latest by ts; keep PLACED if
            // it was ever placed so a held side isn't shown as a later skip).
            final games = groups.values.map(_dedupeSides).toList()
              ..sort((a, b) => _bestEdge(b).compareTo(_bestEdge(a)));
            return Column(children: games.map(_pregameCard).toList());
          },
        ),
      ]),
    );
  }

  // Collapse a game's per-tick rows to one row per side (latest by ts), keeping
  // a side marked PLACED if it was ever placed (so a held side isn't shown as a
  // later 'already positioned' skip).
  List<Map<String, dynamic>> _dedupeSides(List<Map<String, dynamic>> rs) {
    const order = {'home': 0, 'draw': 1, 'away': 2};
    final bySide = <String, Map<String, dynamic>>{};
    final everPlaced = <String, bool>{};
    // The edge at which the side actually placed (from the 'placed' row), so a
    // held side can show "placed @ +6.0%" alongside its now-decayed live edge.
    final placedEdge = <String, double>{};
    for (final r in rs) {
      final side = (r['side'] ?? '').toString();
      if (r['decision'] == 'placed') {
        everPlaced[side] = true;
        final pe = (r['entry_edge'] as num?)?.toDouble();
        if (pe != null) placedEdge[side] = pe;
      } else {
        everPlaced[side] = everPlaced[side] ?? false;
      }
      final prev = bySide[side];
      final ts = (r['ts'] ?? '').toString();
      final pts = (prev?['ts'] ?? '').toString();
      if (prev == null || ts.compareTo(pts) >= 0) bySide[side] = r;
    }
    final out = bySide.entries.map((e) {
      // The kept row already carries 'edge_history' (it's the latest row map).
      final m = Map<String, dynamic>.from(e.value);
      if (everPlaced[e.key] == true) m['decision'] = 'placed';
      if (placedEdge.containsKey(e.key)) m['entry_edge'] = placedEdge[e.key];
      return m;
    }).toList()
      ..sort((a, b) => (order[a['side']] ?? 9).compareTo(order[b['side']] ?? 9));
    return out;
  }

  double _bestEdge(List<Map<String, dynamic>> sides) {
    double best = double.negativeInfinity;
    for (final s in sides) {
      final e = (s['edge'] as num?)?.toDouble();
      if (e != null && e > best) best = e;
    }
    return best == double.negativeInfinity ? 0 : best;
  }

  // Kalshi price in cents: prefer the real fill avg, else back it out of the
  // fair value and edge (fair − edge, both fractions → cents).
  int? _priceCents(Map<String, dynamic> r) {
    final entry = r['entry_avg_cents'];
    if (entry is num) return entry.round();
    final fair = (r['fused_fair'] as num?)?.toDouble();
    final edge = (r['edge'] as num?)?.toDouble();
    if (fair == null || edge == null) return null;
    return ((fair - edge) * 100).round();
  }

  // Live countdown to kickoff. ts is epoch SECONDS. Clean two-unit format:
  // "5d 4h" / "4h 3m" / "3m 2s" / "2s"; "live" once it has kicked off.
  String _kickoffCountdown(num? ts) {
    if (ts == null) return '';
    var secs = (ts.toDouble() - _now.millisecondsSinceEpoch / 1000).round();
    if (secs <= 0) return 'live';
    final d = secs ~/ 86400; secs -= d * 86400;
    final h = secs ~/ 3600; secs -= h * 3600;
    final m = secs ~/ 60; final s = secs - m * 60;
    final u = <List<dynamic>>[[d, 'd'], [h, 'h'], [m, 'm'], [s, 's']];
    final i = u.indexWhere((e) => (e[0] as int) > 0);
    if (i < 0) return 'live';
    final out = ['${u[i][0]}${u[i][1]}'];
    if (i + 1 < u.length && (u[i + 1][0] as int) > 0) out.add('${u[i + 1][0]}${u[i + 1][1]}');
    return out.join(' ');
  }

  Widget _pregameCard(List<Map<String, dynamic>> sides) {
    final head = sides.first;
    final match = (head['match'] ?? '').toString();
    final home = (head['home'] ?? '').toString();
    final away = (head['away'] ?? '').toString();
    final title = match.isNotEmpty
        ? match
        : (home.isNotEmpty || away.isNotEmpty ? '$home vs $away' : 'Match');
    final best = _bestEdge(sides);
    final bestPos = best > 0;
    final cd = _kickoffCountdown(head['kickoff_ts'] as num?);   // live countdown to kickoff

    // Compact context line: "Elo h/a · xG h/a", each half omitted if null.
    final elo = _pair(head['home_elo'], head['away_elo'], decimals: 0);
    final xg = _pair(head['home_xg'], head['away_xg'], decimals: 2);
    final parts = <String>[
      if (elo != null) 'Elo $elo',
      if (xg != null) 'xG $xg',
    ];

    // Order sides home → draw → away, keeping only those present.
    Map<String, dynamic>? bySide(String s) {
      for (final r in sides) {
        if ((r['side'] ?? '').toString() == s) return r;
      }
      return null;
    }
    final ordered = [
      for (final s in const ['home', 'draw', 'away'])
        if (bySide(s) != null) bySide(s)!,
    ];
    // Fallback: if no side keys matched, just show whatever rows we have.
    final shownSides = ordered.isNotEmpty ? ordered : sides;

    return Container(
      margin: const EdgeInsets.only(bottom: 10),
      padding: const EdgeInsets.all(12),
      decoration: BoxDecoration(
        color: AppColors.fill(AppColors.surface),
        borderRadius: BorderRadius.circular(12),
        border: Border.all(color: AppColors.border),
      ),
      child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
        // Header: flag/crest + "Home vs Away" + best-edge chip.
        Row(children: [
          _crest((head['pick_logo'] ?? '').toString(), home.isNotEmpty ? home : title),
          const SizedBox(width: 10),
          Expanded(child: Text(title, maxLines: 1, overflow: TextOverflow.ellipsis,
              style: AppTextStyles.body.copyWith(color: AppColors.textHi, fontWeight: FontWeight.w700))),
          const SizedBox(width: 8),
          Container(
            padding: const EdgeInsets.symmetric(horizontal: 7, vertical: 3),
            decoration: BoxDecoration(
              color: AppColors.fill(bestPos ? AppColors.success : AppColors.danger),
              borderRadius: BorderRadius.circular(6),
              border: Border.all(color: AppColors.stroke(bestPos ? AppColors.success : AppColors.danger)),
            ),
            child: Text('${bestPos ? '+' : ''}${(best * 100).toStringAsFixed(1)}% edge',
                style: AppTextStyles.nano.copyWith(
                    color: bestPos ? AppColors.success : AppColors.danger, fontWeight: FontWeight.bold)),
          ),
        ]),
        if (parts.isNotEmpty) ...[
          const SizedBox(height: 6),
          Text(parts.join('  ·  '),
              style: AppTextStyles.nano.copyWith(color: AppColors.textDim)),
        ],
        if (cd.isNotEmpty) ...[
          const SizedBox(height: 4),
          Row(children: [
            Icon(Icons.schedule, size: 12, color: cd == 'live' ? AppColors.success : AppColors.primary),
            const SizedBox(width: 4),
            Text(cd == 'live' ? 'Live now' : 'Starts in $cd',
                style: AppTextStyles.nano.copyWith(
                    color: cd == 'live' ? AppColors.success : AppColors.primary, fontWeight: FontWeight.w600)),
          ]),
        ],
        const SizedBox(height: 10),
        ...shownSides.map(_pregameSideRow),
      ]),
    );
  }

  // "h/a" with each side omitted (→ blank) when null; null if both missing.
  String? _pair(dynamic a, dynamic b, {required int decimals}) {
    String fmt(dynamic v) => v is num ? v.toStringAsFixed(decimals) : '—';
    if (a is! num && b is! num) return null;
    return '${fmt(a)}/${fmt(b)}';
  }

  Widget _pregameSideRow(Map<String, dynamic> r) {
    final pick = (r['pick_label'] ?? r['side'] ?? '').toString();
    final fair = (r['fused_fair'] as num?)?.toDouble();
    final edge = (r['edge'] as num?)?.toDouble();
    final edgePos = (edge ?? 0) > 0;
    final price = _priceCents(r);
    final dec = (r['decision'] ?? '').toString();
    final modelOnly = r['sharp_prob'] == null;
    final updated = _fmtTs(r['ts']?.toString());
    // Edge-over-time sparkline points (carried through _dedupeSides).
    final spark = _edgeSeries(r['edge_history']);
    // Entry edge captured from the 'placed' row (e.g. placed @ +6.0%).
    final placed = dec.toLowerCase() == 'placed';
    final entryEdge = (r['entry_edge'] as num?)?.toDouble();
    return Padding(
      padding: const EdgeInsets.only(top: 8),
      child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
        Row(children: [
          Expanded(child: Row(children: [
            Flexible(child: Text(pick, maxLines: 1, overflow: TextOverflow.ellipsis,
                style: AppTextStyles.nano.copyWith(color: AppColors.primary, fontWeight: FontWeight.w600))),
            if (modelOnly) ...[
              const SizedBox(width: 6),
              Text('model-only', style: AppTextStyles.nano.copyWith(
                  color: AppColors.textFaint, fontStyle: FontStyle.italic)),
            ],
          ])),
          if (spark.length >= 2) ...[
            const SizedBox(width: 8),
            SizedBox(
              width: 60, height: 16,
              child: CustomPaint(painter: _EdgeSparkPainter(spark)),
            ),
          ],
          const SizedBox(width: 8),
          _sidePill(dec),
        ]),
        const SizedBox(height: 4),
        Row(children: [
          _metric('fair', fair == null ? '—' : '${(fair * 100).toStringAsFixed(0)}%'),
          const SizedBox(width: 14),
          _metric('price', price == null ? '—' : '$price¢'),
          const SizedBox(width: 14),
          _metric(
            'edge',
            edge == null ? '—' : '${edgePos ? '+' : ''}${(edge * 100).toStringAsFixed(1)}%',
            valueColor: edge == null ? null : (edgePos ? AppColors.success : AppColors.danger),
          ),
          if (updated.isNotEmpty) ...[
            const Spacer(),
            Text('updated $updated', style: AppTextStyles.nano.copyWith(color: AppColors.textFaint)),
          ],
        ]),
        if (placed && entryEdge != null) ...[
          const SizedBox(height: 3),
          Text('placed @ ${entryEdge >= 0 ? '+' : ''}${(entryEdge * 100).toStringAsFixed(1)}%',
              style: AppTextStyles.nano.copyWith(color: AppColors.success, fontStyle: FontStyle.italic)),
        ],
      ]),
    );
  }

  // Parse an 'edge_history' list of {'ts','edge'} into ordered edge values.
  List<double> _edgeSeries(dynamic raw) {
    if (raw is! List) return const [];
    final out = <double>[];
    for (final p in raw) {
      if (p is Map) {
        final e = (p['edge'] as num?)?.toDouble();
        if (e != null) out.add(e);
      }
    }
    return out;
  }

  // "Updated" stamp per edge — relative if recent, else short date/time.
  String _fmtTs(String? ts) {
    if (ts == null || ts.isEmpty) return '';
    final t = DateTime.tryParse(ts);
    if (t == null) return '';
    final secs = DateTime.now().toUtc().difference(t.toUtc()).inSeconds;
    if (secs < 60) return 'just now';
    if (secs < 3600) return '${secs ~/ 60}m ago';
    if (secs < 86400) return '${secs ~/ 3600}h ago';
    final l = t.toLocal();
    String two(int n) => n.toString().padLeft(2, '0');
    return '${two(l.month)}/${two(l.day)} ${two(l.hour)}:${two(l.minute)}';
  }

  Widget _metric(String label, String value, {Color? valueColor}) => RichText(
        text: TextSpan(children: [
          TextSpan(text: '$label ', style: AppTextStyles.nano.copyWith(color: AppColors.textDim)),
          TextSpan(text: value, style: AppTextStyles.nano.copyWith(
              color: valueColor ?? AppColors.textMd, fontWeight: FontWeight.w600)),
        ]),
      );

  // Status pill: PLACED emerald, SKIPPED muted, BLOCKED amber.
  Widget _sidePill(String decision) {
    final d = decision.toLowerCase();
    final Color c = d == 'placed'
        ? AppColors.success
        : d == 'blocked'
            ? AppColors.warning
            : AppColors.textDim;
    final label = decision.isEmpty ? '—' : decision.toUpperCase();
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 6, vertical: 2),
      decoration: BoxDecoration(color: AppColors.fill(c), borderRadius: BorderRadius.circular(4)),
      child: Text(label, style: AppTextStyles.nano.copyWith(color: c, fontWeight: FontWeight.bold)),
    );
  }

  Widget _decisionLog(AsyncValue<Map<String, dynamic>> decAsync) {
    return GlassCard(
   frosted: true,      padding: const EdgeInsets.all(14),
      child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
        Row(children: [
          Icon(symbol('hub'), color: AppColors.primary, size: 18),
          const SizedBox(width: 8),
          Text('DECISION LOG', style: AppTextStyles.eyebrow),
        ]),
        const SizedBox(height: 12),
        decAsync.when(
          loading: () => const LoadingState(),
          error: (e, _) => ErrorBanner(message: '$e', onRetry: _refresh),
          data: (d) {
            final rows = (d['decisions'] as List?) ?? [];
            if (rows.isEmpty) {
              return Text('No decisions logged yet.', style: AppTextStyles.body.copyWith(color: AppColors.textDim));
            }
            final pages = (rows.length / _decPageSize).ceil();
            final page = _decPage.clamp(0, pages - 1);
            final start = page * _decPageSize;
            final slice = rows.sublist(start, (start + _decPageSize).clamp(0, rows.length));
            return Column(children: [
              ...List.generate(slice.length, (j) => _decisionCard(slice[j] as Map<String, dynamic>, start + j)),
              if (pages > 1) ...[
                const SizedBox(height: 8),
                Row(mainAxisAlignment: MainAxisAlignment.spaceBetween, children: [
                  _pageBtn(Icons.chevron_left, page > 0, () => setState(() { _decPage = page - 1; _expanded.clear(); })),
                  Text('Page ${page + 1} / $pages', style: AppTextStyles.nano.copyWith(color: AppColors.textDim)),
                  _pageBtn(Icons.chevron_right, page < pages - 1, () => setState(() { _decPage = page + 1; _expanded.clear(); })),
                ]),
              ],
            ]);
          },
        ),
      ]),
    );
  }

  Widget _pageBtn(IconData icon, bool enabled, VoidCallback onTap) => GestureDetector(
        onTap: enabled ? onTap : null,
        child: Container(
          padding: const EdgeInsets.all(8),
          decoration: BoxDecoration(
            color: enabled ? AppColors.fill(AppColors.primary) : AppColors.fill(AppColors.surface),
            borderRadius: BorderRadius.circular(8),
            border: Border.all(color: enabled ? AppColors.stroke(AppColors.primary) : AppColors.border),
          ),
          child: Icon(icon, size: 18, color: enabled ? AppColors.primary : AppColors.textDim),
        ),
      );

  Widget _decisionCard(Map<String, dynamic> r, int i) {
    final open = _expanded.contains(i);
    final dec = (r['decision'] ?? '').toString();
    final match = (r['match'] ?? r['market_ticker'] ?? '').toString();
    final pick = (r['pick_label'] ?? r['side'] ?? '').toString();
    final edge = (r['edge'] as num?)?.toDouble();
    return GestureDetector(
      onTap: () => setState(() => open ? _expanded.remove(i) : _expanded.add(i)),
      child: Container(
        margin: const EdgeInsets.only(bottom: 8),
        padding: const EdgeInsets.all(12),
        decoration: BoxDecoration(
          color: AppColors.fill(AppColors.surface),
          borderRadius: BorderRadius.circular(12),
          border: Border.all(color: AppColors.border),
        ),
        child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
          Row(children: [
            _crest((r['pick_logo'] ?? '').toString(), pick.replaceAll(' to win', '')),
            const SizedBox(width: 10),
            Expanded(child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
              Text(match, maxLines: 1, overflow: TextOverflow.ellipsis,
                  style: AppTextStyles.body.copyWith(color: AppColors.textHi, fontWeight: FontWeight.w600)),
              const SizedBox(height: 2),
              Text(pick, maxLines: 1, overflow: TextOverflow.ellipsis,
                  style: AppTextStyles.nano.copyWith(color: AppColors.primary)),
            ])),
            const SizedBox(width: 8),
            Column(crossAxisAlignment: CrossAxisAlignment.end, children: [
              if (edge != null)
                Text('${edge >= 0 ? '+' : ''}${(edge * 100).toStringAsFixed(1)}%',
                    style: AppTextStyles.meta.copyWith(
                        color: edge >= 0 ? AppColors.success : AppColors.danger, fontWeight: FontWeight.bold)),
              const SizedBox(height: 2),
              Row(mainAxisSize: MainAxisSize.min, children: [
                if (r['paper'] == true) ...[
                  Container(
                    padding: const EdgeInsets.symmetric(horizontal: 6, vertical: 2),
                    decoration: BoxDecoration(color: const Color(0x26F59E0B), borderRadius: BorderRadius.circular(4)),
                    child: Text('MOCK', style: AppTextStyles.nano.copyWith(color: const Color(0xFFF59E0B), fontWeight: FontWeight.bold)),
                  ),
                  const SizedBox(width: 4),
                ],
                Container(
                  padding: const EdgeInsets.symmetric(horizontal: 6, vertical: 2),
                  decoration: BoxDecoration(color: AppColors.fill(_decColor(dec)), borderRadius: BorderRadius.circular(4)),
                  child: Text(dec.toUpperCase(), style: AppTextStyles.nano.copyWith(color: _decColor(dec), fontWeight: FontWeight.bold)),
                ),
              ]),
            ]),
            const SizedBox(width: 4),
            Icon(open ? Icons.expand_less : Icons.expand_more, size: 18, color: AppColors.textDim),
          ]),
          if (open) ...[
            const SizedBox(height: 10),
            Wrap(spacing: 14, runSpacing: 6, children: [
              _kv('Model', _pct(r['model_prob'])),
              _kv('Sharp', _pct(r['sharp_prob'])),
              _kv('LLM', _pct(r['llm_adjustment'])),
              _kv('Fair', _pct(r['fused_fair'])),
              _kv('Size', '${r['size'] ?? 0}'),
              if (r['paper'] == true && r['realized_pnl_cents'] != null)
                _kv('Paper P&L', '\$${((r['realized_pnl_cents'] as num) / 100).toStringAsFixed(2)}'),
            ]),
            if (r['llm_rationale'] != null && (r['llm_rationale'] as String).isNotEmpty) ...[
              const SizedBox(height: 8),
              Container(
                width: double.infinity,
                padding: const EdgeInsets.all(10),
                decoration: BoxDecoration(
                  color: AppColors.surface.withValues(alpha: 0.4),
                  borderRadius: BorderRadius.circular(8),
                  border: Border.all(color: AppColors.border),
                ),
                child: Row(crossAxisAlignment: CrossAxisAlignment.start, children: [
                  Icon(symbol('psychology'), size: 14, color: AppColors.primary),
                  const SizedBox(width: 6),
                  Expanded(child: Text(r['llm_rationale'].toString(),
                      style: AppTextStyles.nano.copyWith(color: AppColors.textMd))),
                ]),
              ),
            ],
            if (r['block_reason'] != null && (r['block_reason'] as String).isNotEmpty) ...[
              const SizedBox(height: 6),
              Text('${dec == 'blocked' ? 'Blocked: ' : 'Skipped — '}${r['block_reason']}',
                  style: AppTextStyles.nano.copyWith(color: dec == 'blocked' ? AppColors.danger.withValues(alpha: 0.85) : AppColors.textDim)),
            ],
          ],
        ]),
      ),
    );
  }

  Widget _kv(String k, String v) => RichText(
        text: TextSpan(children: [
          TextSpan(text: '$k ', style: AppTextStyles.nano.copyWith(color: AppColors.textDim)),
          TextSpan(text: v, style: AppTextStyles.nano.copyWith(color: AppColors.textMd, fontWeight: FontWeight.w600)),
        ]),
      );
}

/// Tiny edge-over-time sparkline: a normalized polyline of one side's edge
/// history, stroked emerald if the latest edge is >= 0, else red.
class _EdgeSparkPainter extends CustomPainter {
  _EdgeSparkPainter(this.values);
  final List<double> values;

  @override
  void paint(Canvas canvas, Size size) {
    if (values.length < 2) return;
    var lo = values.first, hi = values.first;
    for (final v in values) {
      if (v < lo) lo = v;
      if (v > hi) hi = v;
    }
    final span = (hi - lo).abs();
    const pad = 1.5;
    final w = size.width;
    final h = size.height - pad * 2;
    final dx = w / (values.length - 1);
    final path = Path();
    for (var i = 0; i < values.length; i++) {
      final x = i * dx;
      // Flat series → center line; else normalize min..max into the box (top=hi).
      final norm = span == 0 ? 0.5 : (values[i] - lo) / span;
      final y = pad + (1 - norm) * h;
      if (i == 0) {
        path.moveTo(x, y);
      } else {
        path.lineTo(x, y);
      }
    }
    final color = values.last >= 0 ? AppColors.success : AppColors.danger;
    final paint = Paint()
      ..color = color
      ..style = PaintingStyle.stroke
      ..strokeWidth = 1.4
      ..strokeJoin = StrokeJoin.round
      ..strokeCap = StrokeCap.round
      ..isAntiAlias = true;
    canvas.drawPath(path, paint);
  }

  @override
  bool shouldRepaint(covariant _EdgeSparkPainter old) => old.values != values;
}
