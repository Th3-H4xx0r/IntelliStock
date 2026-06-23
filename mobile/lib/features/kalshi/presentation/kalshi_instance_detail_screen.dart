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
  Timer? _liveTimer;

  @override
  void initState() {
    super.initState();
    _liveTimer = Timer.periodic(const Duration(seconds: 15), (_) {
      if (mounted) ref.invalidate(kalshiInstanceLiveProvider(widget.instanceId));
    });
  }

  @override
  void dispose() {
    _liveTimer?.cancel();
    super.dispose();
  }

  Future<void> _refresh() async {
    ref.invalidate(kalshiInstanceDetailProvider(widget.instanceId));
    ref.invalidate(kalshiInstanceDecisionsProvider(widget.instanceId));
    ref.invalidate(kalshiInstanceLiveProvider(widget.instanceId));
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
    final detail = detailAsync.value;
    final running = detail?['running'] == true;

    return Scaffold(
      backgroundColor: Colors.transparent,
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
          : RefreshIndicator(
              color: AppColors.primary,
              onRefresh: _refresh,
              child: ListView(
                padding: const EdgeInsets.fromLTRB(16, 4, 16, 24),
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
                  _liveCards(liveAsync),
                  _decisionLog(decAsync),
                  const SizedBox(height: 12),
                  GlassCard(
                    padding: const EdgeInsets.all(14),
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

  Widget _liveCards(AsyncValue<Map<String, dynamic>> liveAsync) {
    final matches = (liveAsync.value?['matches'] as List?) ?? const [];
    if (matches.isEmpty) return const SizedBox.shrink();
    return Padding(
      padding: const EdgeInsets.only(bottom: 12),
      child: GlassCard(
        padding: const EdgeInsets.all(14),
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
        Row(children: [
          Expanded(child: Text('${m['home']}  vs  ${m['away']}',
              style: AppTextStyles.body.copyWith(color: AppColors.textHi, fontWeight: FontWeight.w600),
              overflow: TextOverflow.ellipsis)),
          if (score != null)
            Text('${score['home'] ?? '–'} : ${score['away'] ?? '–'}',
                style: AppTextStyles.cardTitle.copyWith(color: AppColors.textHi)),
          const SizedBox(width: 6),
          Text(clock, style: AppTextStyles.nano.copyWith(color: AppColors.success, fontWeight: FontWeight.bold)),
        ]),
        const SizedBox(height: 8),
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

  Widget _decisionLog(AsyncValue<Map<String, dynamic>> decAsync) {
    return GlassCard(
      padding: const EdgeInsets.all(14),
      child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
        Row(children: [
          Icon(symbol('hub'), color: AppColors.primary, size: 18),
          const SizedBox(width: 8),
          Text('DECISION LOG', style: AppTextStyles.eyebrow),
        ]),
        const SizedBox(height: 10),
        decAsync.when(
          loading: () => const LoadingState(),
          error: (e, _) => ErrorBanner(message: '$e', onRetry: _refresh),
          data: (d) {
            final rows = (d['decisions'] as List?) ?? [];
            if (rows.isEmpty) {
              return Text('No decisions logged yet.', style: AppTextStyles.body.copyWith(color: AppColors.textDim));
            }
            return Column(
              children: List.generate(rows.length, (i) {
                final r = rows[i] as Map<String, dynamic>;
                final open = _expanded.contains(i);
                final dec = (r['decision'] ?? '').toString();
                return Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
                  GestureDetector(
                    onTap: () => setState(() => open ? _expanded.remove(i) : _expanded.add(i)),
                    child: Padding(
                      padding: const EdgeInsets.symmetric(vertical: 6),
                      child: Row(children: [
                        Icon(open ? Icons.expand_more : Icons.chevron_right, size: 16, color: AppColors.textDim),
                        const SizedBox(width: 4),
                        Expanded(child: Text('${r['market_ticker']}  ·  ${r['side']}',
                            style: AppTextStyles.meta.copyWith(color: AppColors.textMd), overflow: TextOverflow.ellipsis)),
                        Text(dec.toUpperCase(),
                            style: AppTextStyles.nano.copyWith(color: _decColor(dec), fontWeight: FontWeight.bold)),
                      ]),
                    ),
                  ),
                  if (open)
                    Padding(
                      padding: const EdgeInsets.only(left: 20, bottom: 8),
                      child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
                        Text('Model ${_pct(r['model_prob'])} · Sharp ${_pct(r['sharp_prob'])} · LLM ${_pct(r['llm_adjustment'])} · Fair ${_pct(r['fused_fair'])}',
                            style: AppTextStyles.nano.copyWith(color: AppColors.textDim)),
                        if (r['llm_rationale'] != null && (r['llm_rationale'] as String).isNotEmpty) ...[
                          const SizedBox(height: 6),
                          Container(
                            padding: const EdgeInsets.all(8),
                            decoration: BoxDecoration(
                              color: AppColors.surface.withValues(alpha: 0.4),
                              borderRadius: BorderRadius.circular(8),
                              border: Border.all(color: AppColors.border),
                            ),
                            child: Text(r['llm_rationale'].toString(),
                                style: AppTextStyles.nano.copyWith(color: AppColors.textMd)),
                          ),
                        ],
                      ]),
                    ),
                ]);
              }),
            );
          },
        ),
      ]),
    );
  }
}
