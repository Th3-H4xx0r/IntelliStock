import 'dart:async';
import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../../core/charts/scrubbable_area_chart.dart';
import '../../../core/theme/app_colors.dart';
import '../data/kalshi_repository.dart';

const _kLeagues = [
  'World Cup', 'World Cup Qualifiers', 'Champions League', 'Europa League',
  'EPL', 'EFL Championship', 'Serie A', 'Serie B', 'La Liga', 'La Liga 2',
  'Bundesliga', '2. Bundesliga', 'Ligue 1', 'Ligue 2', 'Eredivisie',
  'Primeira Liga', 'MLS', 'Brasileirão',
];

class KalshiBacktestScreen extends ConsumerStatefulWidget {
  const KalshiBacktestScreen({super.key, required this.instanceId});
  final String instanceId;

  @override
  ConsumerState<KalshiBacktestScreen> createState() => _KalshiBacktestScreenState();
}

class _KalshiBacktestScreenState extends ConsumerState<KalshiBacktestScreen> {
  String _bid = '';
  final _name = TextEditingController();
  final _oddsKey = TextEditingController();
  List<String> _leagues = ['World Cup'];
  DateTime? _start, _end;
  double _bankroll = 54, _edgePct = 3, _noSharpPct = 8, _kelly = 0.2,
      _orderMin = 5, _orderMax = 10, _sharpWeight = 85;

  bool _submitting = false;
  String? _err;
  List<Map<String, dynamic>> _backtests = [];
  Map<String, dynamic>? _selected; // status row
  Map<String, dynamic>? _result;
  Timer? _timer;

  KalshiRepository get _repo => ref.read(kalshiRepositoryProvider);

  @override
  void initState() {
    super.initState();
    _load();
    _timer = Timer.periodic(const Duration(seconds: 3), (_) => _tick());
  }

  @override
  void dispose() {
    _timer?.cancel();
    _name.dispose();
    _oddsKey.dispose();
    super.dispose();
  }

  Future<void> _load() async {
    try {
      final d = await _repo.instanceDetail(widget.instanceId);
      final c = (d['config'] ?? {}) as Map<String, dynamic>;
      setState(() {
        _bid = (d['brokerage_id'] ?? '').toString();
        if (c['edge_threshold'] != null) _edgePct = (c['edge_threshold'] as num) * 100;
        if (c['no_sharp_edge_threshold'] != null) _noSharpPct = (c['no_sharp_edge_threshold'] as num) * 100;
        if (c['kelly_fraction'] != null) _kelly = (c['kelly_fraction'] as num).toDouble();
        if (c['order_size_min_cents'] != null) _orderMin = (c['order_size_min_cents'] as num) / 100;
        if (c['order_size_max_cents'] != null) _orderMax = (c['order_size_max_cents'] as num) / 100;
        if (c['sharp_weight'] != null) _sharpWeight = (c['sharp_weight'] as num) * 100;
        if (c['bankroll_cents'] != null) _bankroll = (c['bankroll_cents'] as num) / 100;
        if (c['leagues'] is List && (c['leagues'] as List).isNotEmpty) {
          _leagues = (c['leagues'] as List).map((e) => e.toString()).toList();
        }
        if (c['oddspapi_api_key'] != null) _oddsKey.text = c['oddspapi_api_key'].toString();
      });
      await _loadBacktests();
    } catch (_) {
      setState(() => _err = "Couldn't load the instance config.");
    }
  }

  Future<void> _loadBacktests() async {
    if (_bid.isEmpty) return;
    try {
      final list = await _repo.listBacktests(_bid);
      if (mounted) setState(() => _backtests = list);
    } catch (_) {/* transient */}
  }

  Future<void> _tick() async {
    await _loadBacktests();
    final sel = _selected;
    if (sel != null && (sel['status'] == 'pending' || sel['status'] == 'running')) {
      await _openResults(sel['id'].toString());
    }
  }

  String _fmtDate(DateTime? d) =>
      d == null ? '' : '${d.year.toString().padLeft(4, '0')}-${d.month.toString().padLeft(2, '0')}-${d.day.toString().padLeft(2, '0')}';

  Future<void> _pick(bool isStart) async {
    final now = DateTime.now();
    final d = await showDatePicker(
      context: context,
      initialDate: (isStart ? _start : _end) ?? now,
      firstDate: DateTime(2024), lastDate: DateTime(now.year + 1),
    );
    if (d != null) setState(() => isStart ? _start = d : _end = d);
  }

  Future<void> _submit() async {
    setState(() => _err = null);
    if (_start == null || _end == null) return setState(() => _err = 'Pick a start and end date.');
    if (_fmtDate(_start).compareTo(_fmtDate(_end)) > 0) return setState(() => _err = 'Start must be on/before end.');
    if (_leagues.isEmpty) return setState(() => _err = 'Select at least one league.');
    setState(() => _submitting = true);
    try {
      final body = {
        'name': _name.text.isEmpty ? 'Backtest ${_fmtDate(_start)}..${_fmtDate(_end)}' : _name.text,
        'instance_id': widget.instanceId,
        'leagues': _leagues,
        'start_date': _fmtDate(_start),
        'end_date': _fmtDate(_end),
        'bankroll_dollars': _bankroll,
        'config': {
          'edge_threshold': _edgePct / 100,
          'no_sharp_edge_threshold': _noSharpPct / 100,
          'kelly_fraction': _kelly,
          'order_size_min_dollars': _orderMin,
          'order_size_max_dollars': _orderMax,
          'sharp_weight': _sharpWeight / 100,
          if (_oddsKey.text.isNotEmpty) 'oddspapi_api_key': _oddsKey.text,
        },
      };
      final id = await _repo.createBacktest(_bid, body);
      await _loadBacktests();
      await _openResults(id);
    } catch (_) {
      setState(() => _err = 'Failed to start the backtest.');
    } finally {
      if (mounted) setState(() => _submitting = false);
    }
  }

  Future<void> _openResults(String id) async {
    try {
      final d = await _repo.backtestResults(id);
      if (!mounted) return;
      setState(() {
        _selected = {'id': id, 'status': d['status'], 'summary': d['summary'] ?? {}};
        _result = d['result'] as Map<String, dynamic>?;
      });
    } catch (_) {/* ignore */}
  }

  String _money(dynamic cents) =>
      cents == null ? '—' : '\$${((cents as num) / 100).toStringAsFixed(2)}';
  String _pct(dynamic v) => v == null ? '—' : '${((v as num) * 100).toStringAsFixed(1)}%';
  Color _statusColor(String? s) => s == 'finished'
      ? AppColors.success
      : s == 'error' ? AppColors.danger : s == 'stopped' ? AppColors.textDim : AppColors.warning;

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      backgroundColor: AppColors.canvas,
      appBar: AppBar(title: const Text('Backtest'), backgroundColor: AppColors.canvas),
      body: ListView(
        padding: const EdgeInsets.all(16),
        children: [
          if (_err != null) Padding(padding: const EdgeInsets.only(bottom: 8), child: Text(_err!, style: const TextStyle(color: AppColors.warning))),
          _card('New backtest', [
            _field('Name', _name),
            const SizedBox(height: 8),
            Row(children: [
              Expanded(child: _dateBtn('Start', _start, () => _pick(true))),
              const SizedBox(width: 8),
              Expanded(child: _dateBtn('End', _end, () => _pick(false))),
            ]),
            const SizedBox(height: 10),
            const Text('Leagues', style: TextStyle(color: AppColors.textMuted, fontSize: 12)),
            Wrap(spacing: 6, runSpacing: 6, children: [
              for (final l in _kLeagues)
                FilterChip(
                  label: Text(l, style: const TextStyle(fontSize: 11)),
                  selected: _leagues.contains(l),
                  onSelected: (v) => setState(() => v ? _leagues.add(l) : _leagues.remove(l)),
                  selectedColor: AppColors.primary.withValues(alpha: 0.25),
                  backgroundColor: AppColors.surface,
                ),
            ]),
            const SizedBox(height: 10),
            Wrap(spacing: 10, runSpacing: 10, children: [
              _num('Bankroll (\$)', _bankroll, (v) => _bankroll = v),
              _num('Edge bar (%)', _edgePct, (v) => _edgePct = v),
              _num('No-sharp (%)', _noSharpPct, (v) => _noSharpPct = v),
              _num('Kelly', _kelly, (v) => _kelly = v),
              _num('Order min (\$)', _orderMin, (v) => _orderMin = v),
              _num('Order max (\$)', _orderMax, (v) => _orderMax = v),
              _num('Sharp wt (%)', _sharpWeight, (v) => _sharpWeight = v),
            ]),
            const SizedBox(height: 8),
            _field('OddsPapi API key (sharp line; blank = model-only)', _oddsKey),
            const SizedBox(height: 12),
            SizedBox(width: double.infinity, child: ElevatedButton(
              onPressed: _submitting || _bid.isEmpty ? null : _submit,
              style: ElevatedButton.styleFrom(backgroundColor: AppColors.primary, foregroundColor: AppColors.onPrimary),
              child: Text(_submitting ? 'Starting…' : 'Run backtest'),
            )),
          ]),
          const SizedBox(height: 16),
          _card('Backtests', [
            if (_backtests.isEmpty)
              const Text('No backtests yet.', style: TextStyle(color: AppColors.textDim))
            else
              for (final b in _backtests) _btRow(b),
          ]),
          if (_selected != null) ...[
            const SizedBox(height: 16),
            _resultsCard(),
          ],
        ],
      ),
    );
  }

  Widget _card(String title, List<Widget> children) => Container(
        padding: const EdgeInsets.all(14),
        decoration: BoxDecoration(color: AppColors.surface, borderRadius: BorderRadius.circular(14), border: Border.all(color: AppColors.border)),
        child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
          Text(title, style: const TextStyle(color: AppColors.textHi, fontWeight: FontWeight.w600, fontSize: 14)),
          const SizedBox(height: 10),
          ...children,
        ]),
      );

  Widget _field(String label, TextEditingController c) => TextField(
        controller: c,
        style: const TextStyle(color: AppColors.textHi, fontSize: 13),
        decoration: InputDecoration(labelText: label, labelStyle: const TextStyle(color: AppColors.textMuted, fontSize: 12), isDense: true,
          enabledBorder: OutlineInputBorder(borderSide: const BorderSide(color: AppColors.border), borderRadius: BorderRadius.circular(8))),
      );

  Widget _dateBtn(String label, DateTime? d, VoidCallback onTap) => OutlinedButton(
        onPressed: onTap,
        style: OutlinedButton.styleFrom(side: const BorderSide(color: AppColors.border), foregroundColor: AppColors.textMd),
        child: Text(d == null ? label : _fmtDate(d), style: const TextStyle(fontSize: 12)),
      );

  Widget _num(String label, double value, ValueChanged<double> onChanged) => SizedBox(
        width: 110,
        child: TextFormField(
          initialValue: value == value.roundToDouble() ? value.toInt().toString() : value.toString(),
          keyboardType: const TextInputType.numberWithOptions(decimal: true),
          style: const TextStyle(color: AppColors.textHi, fontSize: 13),
          decoration: InputDecoration(labelText: label, labelStyle: const TextStyle(color: AppColors.textMuted, fontSize: 11), isDense: true,
            enabledBorder: OutlineInputBorder(borderSide: const BorderSide(color: AppColors.border), borderRadius: BorderRadius.circular(8))),
          onChanged: (t) => onChanged(double.tryParse(t) ?? value),
        ),
      );

  Widget _btRow(Map<String, dynamic> b) {
    final status = b['status']?.toString();
    final summary = (b['summary'] ?? {}) as Map;
    final pnl = summary['pnl_cents'];
    return Padding(
      padding: const EdgeInsets.symmetric(vertical: 6),
      child: Row(children: [
        Expanded(child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
          Text((b['name'] ?? b['id'].toString().substring(0, 8)).toString(), style: const TextStyle(color: AppColors.textMd, fontSize: 13)),
          Text('${b['start_date']} → ${b['end_date']}', style: const TextStyle(color: AppColors.textDim, fontSize: 11)),
        ])),
        Column(crossAxisAlignment: CrossAxisAlignment.end, children: [
          Text(status == 'running' || status == 'pending' ? '$status ${(b['progress'] ?? 0).round()}%' : (status ?? ''),
              style: TextStyle(color: _statusColor(status), fontSize: 12)),
          Text(pnl == null ? '—' : _money(pnl),
              style: TextStyle(color: (pnl ?? 0) >= 0 ? AppColors.success : AppColors.danger, fontSize: 12)),
        ]),
        const SizedBox(width: 6),
        IconButton(icon: const Icon(Icons.visibility_outlined, size: 18, color: AppColors.primary), onPressed: () => _openResults(b['id'].toString())),
        if (status == 'running' || status == 'pending')
          IconButton(icon: const Icon(Icons.stop_circle_outlined, size: 18, color: AppColors.warning), onPressed: () async { await _repo.stopBacktest(b['id'].toString()); await _loadBacktests(); }),
        IconButton(icon: const Icon(Icons.delete_outline, size: 18, color: AppColors.textDim), onPressed: () async { await _repo.deleteBacktest(b['id'].toString()); await _loadBacktests(); }),
      ]),
    );
  }

  Widget _resultsCard() {
    final sel = _selected!;
    final summary = (sel['summary'] ?? {}) as Map;
    final res = _result;
    final ec = (res?['equity_curve'] ?? []) as List? ?? [];
    final trades = (res?['trades'] ?? []) as List? ?? [];
    // equity_curve is a bare list of cumulative-P&L cents, ordered by bet.
    final ts = <DateTime>[], vals = <double>[];
    for (var i = 0; i < ec.length; i++) {
      ts.add(DateTime.fromMillisecondsSinceEpoch(i * 3600000));
      vals.add(((ec[i] ?? 0) as num) / 100);
    }
    return _card('Results', [
      Wrap(spacing: 10, runSpacing: 10, children: [
        _stat('Total P&L', _money(summary['pnl_cents']), (summary['pnl_cents'] ?? 0) >= 0 ? AppColors.success : AppColors.danger),
        _stat('ROI', _pct(summary['roi']), AppColors.textHi),
        _stat('Bets', '${summary['n_bets'] ?? '—'}', AppColors.textHi),
        _stat('Win rate', _pct(summary['win_rate']), AppColors.textHi),
        _stat('Avg CLV', _pct(summary['clv_avg']), AppColors.textHi),
        _stat('API/cache', '${summary['api_calls'] ?? 0}/${summary['cache_hits'] ?? 0}', AppColors.textMuted),
      ]),
      const SizedBox(height: 12),
      if (vals.length > 1)
        ScrubbableAreaChart(timestamps: ts, values: vals, lineColor: AppColors.chartLine, height: 200, baseline: 0, indexed: true),
      if (trades.isNotEmpty) ...[
        const SizedBox(height: 12),
        Text('Trades (${trades.length})', style: const TextStyle(color: AppColors.textMuted, fontSize: 12, fontWeight: FontWeight.w600)),
        for (final t in trades.take(60)) _tradeRow(t as Map),
      ],
      if (res != null && trades.isEmpty && sel['status'] == 'finished')
        const Padding(padding: EdgeInsets.only(top: 8), child: Text('No bets were placed under these settings over this range.', style: TextStyle(color: AppColors.textDim, fontSize: 12))),
    ]);
  }

  Widget _stat(String label, String value, Color color) => Container(
        width: 104,
        padding: const EdgeInsets.all(10),
        decoration: BoxDecoration(color: AppColors.canvas, borderRadius: BorderRadius.circular(10), border: Border.all(color: AppColors.border)),
        child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
          Text(label, style: const TextStyle(color: AppColors.textDim, fontSize: 11)),
          Text(value, style: TextStyle(color: color, fontSize: 15, fontWeight: FontWeight.w600)),
        ]),
      );

  Widget _tradeRow(Map t) {
    final pnl = t['realized_pnl_cents'];
    return Padding(
      padding: const EdgeInsets.symmetric(vertical: 4),
      child: Row(children: [
        Expanded(child: Text('${t['market_ticker']}', style: const TextStyle(color: AppColors.textMd, fontSize: 11), overflow: TextOverflow.ellipsis)),
        Text('${t['side']} · ${t['entry_cents']}¢ ×${t['size']}', style: const TextStyle(color: AppColors.textMuted, fontSize: 11)),
        const SizedBox(width: 8),
        Text('${t['outcome']}', style: const TextStyle(color: AppColors.textDim, fontSize: 11)),
        const SizedBox(width: 8),
        Text(_money(pnl), style: TextStyle(color: (pnl ?? 0) >= 0 ? AppColors.success : AppColors.danger, fontSize: 11)),
      ]),
    );
  }
}
