import 'dart:async';
import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../../core/charts/scrubbable_area_chart.dart';
import '../../../core/theme/app_colors.dart';
import '../data/kalshi_repository.dart';

class KalshiBacktestResultScreen extends ConsumerStatefulWidget {
  const KalshiBacktestResultScreen({super.key, required this.backtestId});
  final String backtestId;

  @override
  ConsumerState<KalshiBacktestResultScreen> createState() => _S();
}

class _S extends ConsumerState<KalshiBacktestResultScreen> {
  Map<String, dynamic>? _status;
  Map<String, dynamic>? _result;
  String _tab = 'trades';
  String? _selectedDay;
  Timer? _timer;

  @override
  void initState() {
    super.initState();
    _load();
    _timer = Timer.periodic(const Duration(seconds: 3), (_) {
      final st = _status?['status'];
      if (st == 'pending' || st == 'running') _load();
    });
  }

  @override
  void dispose() { _timer?.cancel(); super.dispose(); }

  Future<void> _load() async {
    try {
      final d = await ref.read(kalshiRepositoryProvider).backtestResults(widget.backtestId);
      if (!mounted) return;
      setState(() {
        _status = {'status': d['status'], 'summary': d['summary'] ?? {},
                   'error': d['error'], 'progress': d['progress'],
                   'started_at': d['started_at'], 'created_at': d['created_at'],
                   'finished_at': d['finished_at']};
        _result = d['result'] as Map<String, dynamic>?;
        final days = _daysList();
        if (_selectedDay == null && days.isNotEmpty) _selectedDay = days.last;
      });
    } catch (_) {}
  }

  List<Map> get _trades => ((_result?['trades'] ?? []) as List).cast<Map>();
  String _dayOf(dynamic ts) {
    final n = (ts is num) ? ts.toInt() : 0;
    if (n == 0) return 'unknown';
    return DateTime.fromMillisecondsSinceEpoch(n * 1000, isUtc: true).toIso8601String().substring(0, 10);
  }
  Map<String, List<Map>> _byDay() {
    final m = <String, List<Map>>{};
    for (final t in _trades) {
      (m[_dayOf(t['kickoff'])] ??= []).add(t);
    }
    return m;
  }
  List<String> _daysList() => _byDay().keys.toList()..sort();

  String _money(dynamic c) => c == null ? '—' : '\$${((c as num) / 100).toStringAsFixed(2)}';
  String _pct(dynamic v) => v == null ? '—' : '${((v as num) * 100).toStringAsFixed(1)}%';
  Color _sc(String? st) => st == 'finished' ? AppColors.success : st == 'error' ? AppColors.danger : st == 'stopped' ? AppColors.textDim : AppColors.warning;

  @override
  Widget build(BuildContext context) {
    final s = (_status?['summary'] ?? {}) as Map;
    final ec = ((_result?['equity_curve'] ?? []) as List);
    final ts = <DateTime>[], vals = <double>[];
    for (var i = 0; i < ec.length; i++) {
      final kt = i < _trades.length ? (_trades[i]['kickoff'] as num? ?? 0).toInt() : 0;
      ts.add(kt > 0 ? DateTime.fromMillisecondsSinceEpoch(kt * 1000) : DateTime.fromMillisecondsSinceEpoch(i * 3600000));
      vals.add(((ec[i] ?? 0) as num) / 100);
    }
    return Scaffold(
      backgroundColor: AppColors.canvas,
      appBar: AppBar(
        backgroundColor: AppColors.canvas,
        title: Row(children: [
          const Text('Backtest '),
          Text(widget.backtestId.substring(0, 8), style: const TextStyle(fontFamily: 'monospace', fontSize: 14, color: AppColors.textMuted)),
          const SizedBox(width: 8),
          if (_status != null) Text(_status!['status']?.toString() ?? '', style: TextStyle(color: _sc(_status!['status']?.toString()), fontSize: 13)),
        ]),
      ),
      body: ListView(padding: const EdgeInsets.all(16), children: [
        _summary(s),
        if (vals.length > 1) ...[
          const SizedBox(height: 16),
          _card('Equity — scrub to see that day\'s trades', [
            if (_selectedDay != null)
              Padding(padding: const EdgeInsets.only(bottom: 6), child: Text('$_selectedDay · ${(_byDay()[_selectedDay] ?? []).length} trade(s)', style: const TextStyle(color: AppColors.primary, fontSize: 12))),
            ScrubbableAreaChart(
              timestamps: ts, values: vals, lineColor: AppColors.chartLine, height: 200, baseline: 0, indexed: true,
              onScrub: (i) {
                if (i != null && i < _trades.length) setState(() => _selectedDay = _dayOf(_trades[i]['kickoff']));
              },
            ),
            const SizedBox(height: 8),
            Wrap(spacing: 6, runSpacing: 6, children: [
              GestureDetector(onTap: () => setState(() => _selectedDay = 'all'), child: Container(
                padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 4),
                decoration: BoxDecoration(borderRadius: BorderRadius.circular(20), color: _selectedDay == 'all' ? AppColors.primary.withValues(alpha: 0.2) : AppColors.surface, border: Border.all(color: _selectedDay == 'all' ? AppColors.primary : AppColors.border)),
                child: Text('All · ${_trades.length}', style: TextStyle(fontSize: 11, color: _selectedDay == 'all' ? AppColors.primary : AppColors.textMuted)))),
              for (final d in _daysList())
                GestureDetector(onTap: () => setState(() => _selectedDay = d), child: Container(
                  padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 4),
                  decoration: BoxDecoration(borderRadius: BorderRadius.circular(20), color: _selectedDay == d ? AppColors.primary.withValues(alpha: 0.2) : AppColors.surface, border: Border.all(color: _selectedDay == d ? AppColors.primary : AppColors.border)),
                  child: Text('$d · ${_byDay()[d]!.length}', style: TextStyle(fontSize: 11, color: _selectedDay == d ? AppColors.primary : AppColors.textMuted)))),
            ]),
          ]),
        ],
        const SizedBox(height: 16),
        _tabs(),
      ]),
    );
  }

  Widget _summary(Map s) => _card('Summary', [
        Wrap(spacing: 10, runSpacing: 10, children: [
          _stat('Total P&L', _money(s['pnl_cents']), (s['pnl_cents'] ?? 0) >= 0 ? AppColors.success : AppColors.danger),
          _stat('ROI', _pct(s['roi']), AppColors.textHi),
          _stat('Bets', '${s['n_bets'] ?? '—'}', AppColors.textHi),
          _stat('Win rate', _pct(s['win_rate']), AppColors.textHi),
          _stat('Avg CLV', _pct(s['clv_avg']), AppColors.textHi),
          _stat('API/cache', '${s['api_calls'] ?? 0}/${s['cache_hits'] ?? 0}', AppColors.textMuted),
        ]),
        const SizedBox(height: 8),
        Text('Fixtures ${s['n_fixtures'] ?? 0} · bet ${s['bet'] ?? 0} · no-edge ${s['no_bet'] ?? 0} · unsettled ${s['unsettled'] ?? 0} · unmatched ${s['unmatched'] ?? 0} · no-price ${s['no_candle_data'] ?? 0}',
            style: const TextStyle(color: AppColors.textDim, fontSize: 11)),
        if (s['profit_confidence'] != null) Padding(
          padding: const EdgeInsets.only(top: 4),
          child: Text(
            'Trust: profit confidence ${((s['profit_confidence'] as num) * 100).toStringAsFixed(0)}% · 90% range ${_money(s['pnl_ci_low_cents'])} → ${_money(s['pnl_ci_high_cents'])}${(s['pnl_ci_low_cents'] ?? 0) < 0 ? ' (spans a loss — not proven)' : ''}',
            style: TextStyle(color: (s['profit_confidence'] as num) >= 0.9 ? AppColors.success : (s['profit_confidence'] as num) >= 0.75 ? AppColors.warning : AppColors.danger, fontSize: 11)),
        ),
      ]);

  Widget _tabs() {
    final decisions = ((_result?['decision_log'] ?? []) as List).cast<Map>();
    final logs = ((_result?['logs'] ?? []) as List);
    final dayTrades = _selectedDay == 'all' ? _trades : (_byDay()[_selectedDay] ?? []);
    return _card('', [
      Row(children: [
        for (final t in ['trades', 'decisions', 'logs'])
          Padding(padding: const EdgeInsets.only(right: 16), child: GestureDetector(
            onTap: () => setState(() => _tab = t),
            child: Text(t == 'decisions' ? 'Decision log' : t[0].toUpperCase() + t.substring(1),
                style: TextStyle(fontSize: 13, color: _tab == t ? AppColors.primary : AppColors.textMuted, fontWeight: _tab == t ? FontWeight.w600 : FontWeight.w400)))),
      ]),
      const SizedBox(height: 10),
      if (_tab == 'trades')
        if (dayTrades.isEmpty)
          Text(_trades.isEmpty ? 'No bets were placed under these settings.' : 'Scrub or pick a day to see its trades.', style: const TextStyle(color: AppColors.textDim, fontSize: 12))
        else
          for (final t in dayTrades) _tradeCard(t)
      else if (_tab == 'decisions')
        if (decisions.isEmpty)
          const Text('No decision log recorded.', style: TextStyle(color: AppColors.textDim, fontSize: 12))
        else
          for (final d in decisions) _decisionRow(d)
      else
        Container(
          padding: const EdgeInsets.all(10),
          decoration: BoxDecoration(color: Colors.black.withValues(alpha: 0.4), borderRadius: BorderRadius.circular(8)),
          child: logs.isEmpty
              ? const Text('No logs recorded.', style: TextStyle(color: AppColors.textDim, fontSize: 11))
              : Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
                  for (final l in logs) Text(l.toString(), style: const TextStyle(fontFamily: 'monospace', fontSize: 10.5, color: AppColors.textMd)),
                ]),
        ),
    ]);
  }

  String _pickLabel(Map t) {
    final s = t['side'];
    if (s == 'draw') return 'Draw';
    if (s == 'home') return '${t['home'] ?? 'Home'} to win';
    if (s == 'away') return '${t['away'] ?? 'Away'} to win';
    return '$s';
  }

  Widget _tradeCard(Map t) {
    final pnl = t['realized_pnl_cents'];
    final hasSharp = t['sharp_prob'] != null;
    return Container(
      margin: const EdgeInsets.only(bottom: 8),
      padding: const EdgeInsets.all(10),
      decoration: BoxDecoration(color: AppColors.canvas, borderRadius: BorderRadius.circular(10), border: Border.all(color: AppColors.border)),
      child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
        Row(children: [
          _flagImg(t['home_flag']),
          const SizedBox(width: 4),
          Expanded(child: Text(
            (t['home'] ?? '').toString().isNotEmpty ? '${t['home']} v ${t['away']}' : '${t['side']}',
            style: const TextStyle(color: AppColors.textHi, fontSize: 13, fontWeight: FontWeight.w500), overflow: TextOverflow.ellipsis)),
          _flagImg(t['away_flag']),
          const SizedBox(width: 6),
          Text(_money(pnl), style: TextStyle(color: (pnl ?? 0) >= 0 ? AppColors.success : AppColors.danger, fontSize: 13, fontWeight: FontWeight.w600)),
        ]),
        Text('${t['league'] ?? ''} · ${_pickLabel(t)} · entry ${t['entry_cents']}¢ × ${t['size']}', style: const TextStyle(color: AppColors.textMuted, fontSize: 11)),
        const SizedBox(height: 6),
        Wrap(spacing: 6, runSpacing: 4, children: [
          _badge('edge ${((t['edge'] ?? 0) * 100).toStringAsFixed(1)}%', AppColors.textMuted),
          _badge(hasSharp ? 'sharp' : 'model-only', hasSharp ? AppColors.info : AppColors.warning),
          _badge('${t['outcome']}', t['outcome'] == 'win' ? AppColors.success : AppColors.danger),
        ]),
      ]),
    );
  }

  Widget _decisionRow(Map d) {
    final dec = d['decision']?.toString() ?? '';
    final col = dec == 'placed' ? AppColors.success : dec == 'no_bet' ? AppColors.textMd : AppColors.warning;
    return Padding(
      padding: const EdgeInsets.symmetric(vertical: 5),
      child: Row(children: [
        Expanded(child: Text(d['label']?.toString() ?? '', style: const TextStyle(color: AppColors.textMd, fontSize: 12))),
        Text(dec, style: TextStyle(color: col, fontSize: 12)),
        const SizedBox(width: 8),
        Expanded(child: Text(d['reason']?.toString() ?? '', style: const TextStyle(color: AppColors.textDim, fontSize: 11), textAlign: TextAlign.right, overflow: TextOverflow.ellipsis)),
      ]),
    );
  }

  Widget _flagImg(dynamic url) {
    final u = (url ?? '').toString();
    if (u.isEmpty) return const SizedBox.shrink();
    return Image.network(u, width: 18, height: 12, fit: BoxFit.cover,
        errorBuilder: (_, _, _) => const SizedBox.shrink());
  }

  Widget _badge(String text, Color color) => Container(
        padding: const EdgeInsets.symmetric(horizontal: 6, vertical: 2),
        decoration: BoxDecoration(color: color.withValues(alpha: 0.15), borderRadius: BorderRadius.circular(4)),
        child: Text(text, style: TextStyle(color: color, fontSize: 10)));

  Widget _card(String title, List<Widget> children) => Container(
        padding: const EdgeInsets.all(14),
        decoration: BoxDecoration(color: AppColors.surface, borderRadius: BorderRadius.circular(14), border: Border.all(color: AppColors.border)),
        child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
          if (title.isNotEmpty) ...[Text(title, style: const TextStyle(color: AppColors.textHi, fontWeight: FontWeight.w600, fontSize: 14)), const SizedBox(height: 10)],
          ...children,
        ]));

  Widget _stat(String label, String value, Color color) => Container(
        width: 104, padding: const EdgeInsets.all(10),
        decoration: BoxDecoration(color: AppColors.canvas, borderRadius: BorderRadius.circular(10), border: Border.all(color: AppColors.border)),
        child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
          Text(label, style: const TextStyle(color: AppColors.textDim, fontSize: 11)),
          Text(value, style: TextStyle(color: color, fontSize: 15, fontWeight: FontWeight.w600)),
        ]));
}
