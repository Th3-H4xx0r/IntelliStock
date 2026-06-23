import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

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

  Future<void> _refresh() async {
    ref.invalidate(kalshiInstanceDetailProvider(widget.instanceId));
    ref.invalidate(kalshiInstanceDecisionsProvider(widget.instanceId));
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
