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

  @override
  void initState() {
    super.initState();
    _liveTimer = Timer.periodic(const Duration(seconds: 15), (_) {
      if (mounted) {
        ref.invalidate(kalshiInstanceLiveProvider(widget.instanceId));
        ref.invalidate(kalshiInstanceOrdersProvider(widget.instanceId));
      }
    });
  }

  @override
  void dispose() {
    _liveTimer?.cancel();
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

  Future<void> _startStop(bool start) async {
    if (_busy) return;
    setState(() => _busy = true);
    try {
      final repo = ref.read(kalshiRepositoryProvider);
      start ? await repo.startInstance(widget.instanceId) : await repo.stopInstance(widget.instanceId);
      await _refresh();
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
      backgroundColor: Colors.transparent,
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
    return Row(children: [
      Expanded(child: StatTile(label: 'Placed', value: placed, valueColor: AppColors.success)),
      const SizedBox(width: 8),
      Expanded(child: StatTile(label: 'Skipped', value: skipped)),
      const SizedBox(width: 8),
      Expanded(child: StatTile(label: 'Queued', value: queued, valueColor: AppColors.warning)),
      const SizedBox(width: 8),
      Expanded(child: StatTile(label: 'Blocked', value: blocked, valueColor: AppColors.danger)),
    ]);
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
      ]),
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
              Container(
                padding: const EdgeInsets.symmetric(horizontal: 6, vertical: 2),
                decoration: BoxDecoration(color: AppColors.fill(_decColor(dec)), borderRadius: BorderRadius.circular(4)),
                child: Text(dec.toUpperCase(), style: AppTextStyles.nano.copyWith(color: _decColor(dec), fontWeight: FontWeight.bold)),
              ),
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
              Text('Blocked: ${r['block_reason']}', style: AppTextStyles.nano.copyWith(color: AppColors.danger.withValues(alpha: 0.85))),
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
