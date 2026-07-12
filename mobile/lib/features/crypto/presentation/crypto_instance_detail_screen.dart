import 'dart:async';

import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';

import '../../../core/theme/app_colors.dart';
import '../../../core/theme/app_text_styles.dart';
import '../../../core/widgets/app_background.dart';
import '../../../core/widgets/common_widgets.dart';
import '../../../core/widgets/glass_card.dart';
import '../../../core/widgets/material_symbols.dart';
import '../../../core/widgets/status_pill.dart';
import '../../dashboard/application/portfolio_analytics.dart' show SectorSlice;
import '../../dashboard/presentation/sector_3d_chart.dart';
import '../../instances/data/models/instance.dart';
import '../data/crypto_repository.dart';
import 'crypto_backtest_sheet.dart';
import 'crypto_instance_sheet.dart';

/// Detail screen for a crypto (kind='crypto') instance. Mirrors the equity
/// instance detail (info cards + a Backtests section) with crypto specifics
/// (band cadence, fixed+dynamic allocation ring, 24/7).
class CryptoInstanceDetailScreen extends ConsumerStatefulWidget {
  const CryptoInstanceDetailScreen({super.key, required this.instanceId});
  final String instanceId;

  @override
  ConsumerState<CryptoInstanceDetailScreen> createState() =>
      _CryptoInstanceDetailScreenState();
}

class _CryptoInstanceDetailScreenState
    extends ConsumerState<CryptoInstanceDetailScreen> {
  Instance? _inst;
  List<Map<String, dynamic>> _brokerages = const [];
  double? _value;
  List<BacktestRow> _backtests = const [];
  bool _loading = true;
  String? _error;
  bool _busy = false;
  Timer? _poll;

  static const _cadence = <String, String>{
    'high': '~5 min',
    'medium': '~15 min',
    'low': '~60 min',
  };

  @override
  void initState() {
    super.initState();
    _load();
  }

  @override
  void dispose() {
    _poll?.cancel();
    super.dispose();
  }

  CryptoRepository get _repo => ref.read(cryptoRepositoryProvider);

  Future<void> _load() async {
    try {
      final inst = await _repo.getInstance(widget.instanceId);
      final brokerages = await _repo.brokerages();
      final backtests = await _repo.instanceBacktests(widget.instanceId);
      double? value;
      final bid = inst.brokerageId;
      if (bid != null && bid.isNotEmpty) value = await _repo.accountEquity(bid);
      if (!mounted) return;
      setState(() {
        _inst = inst;
        _brokerages = brokerages;
        _backtests = backtests;
        _value = value;
        _loading = false;
      });
      _startPoll();
    } catch (e) {
      if (mounted) {
        setState(() {
          _error = e.toString();
          _loading = false;
        });
      }
    }
  }

  Future<void> _refreshBacktests() async {
    try {
      final rows = await _repo.instanceBacktests(widget.instanceId);
      if (mounted) setState(() => _backtests = rows);
    } catch (_) {/* non-critical */}
  }

  void _startPoll() {
    _poll?.cancel();
    _poll = Timer.periodic(const Duration(seconds: 4), (_) {
      final anyRunning = _backtests.any((b) => const [
            'running',
            'queued',
            'pending',
            'paused',
          ].contains(b.status.toLowerCase()));
      if (anyRunning) _refreshBacktests();
    });
  }

  Future<void> _toggleRun() async {
    final inst = _inst;
    if (inst == null) return;
    setState(() => _busy = true);
    try {
      if (inst.runCommand) {
        await _repo.stopInstance(inst.id);
      } else {
        await _repo.startInstance(inst.id);
      }
      final fresh = await _repo.getInstance(widget.instanceId);
      if (mounted) setState(() => _inst = fresh);
    } catch (_) {/* refreshed state reflects reality */} finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  void _openEdit() {
    final inst = _inst;
    if (inst == null) return;
    showCryptoInstanceSheet(
      context,
      editInstanceId: inst.id,
      editName: inst.name,
      editBrokerageId: inst.brokerageId,
      editConfig: inst.cryptoConfig,
      editStocks: inst.stocks,
      onSaved: _load,
    );
  }

  void _openBacktest() {
    final inst = _inst;
    if (inst == null) return;
    showCryptoBacktestSheet(context, inst: inst, onCreated: _refreshBacktests);
  }

  // ── Formatting ────────────────────────────────────────────────────────────────
  String _fmtUsd(num? n) => '\$${(n ?? 0).round().toString().replaceAllMapped(RegExp(r'(\d)(?=(\d{3})+$)'), (m) => '${m[1]},')}';
  String _fmtDuration(num? s) {
    final v = (s ?? 0).round();
    return '${v ~/ 3600}h ${(v % 3600) ~/ 60}m ${v % 60}s';
  }

  String _fmtPnl(double? n) => n == null ? '—' : '${n >= 0 ? '+' : ''}${_fmtUsd(n)}';
  String _fmtPct(double? n) => n == null ? '—' : '${n >= 0 ? '+' : ''}${n.toStringAsFixed(2)}%';
  Color _pnlColor(double? n) =>
      n == null || n == 0 ? AppColors.textMd : (n > 0 ? AppColors.success : AppColors.danger);

  Color _btStatusColor(String s) {
    switch (s.toLowerCase()) {
      case 'finished':
      case 'completed':
      case 'done':
        return AppColors.success;
      case 'running':
      case 'queued':
      case 'pending':
        return AppColors.info;
      case 'error':
      case 'failed':
        return AppColors.danger;
      case 'paused':
        return AppColors.warning;
      default:
        return AppColors.textDim;
    }
  }

  List<SectorSlice> _slices() {
    final inst = _inst!;
    final allocs = (inst.cryptoConfig?['allocations'] as List?) ?? const [];
    final slices = <SectorSlice>[];
    for (final a in allocs.whereType<Map>()) {
      final pct = ((a['pct'] as num?)?.toDouble() ?? 0) * 100;
      if (pct > 0) {
        final sym = (a['symbol'] ?? '').toString().split('/').first;
        slices.add(SectorSlice(sector: sym, value: pct, pct: pct));
      }
    }
    final dyn = 100 - slices.fold<double>(0, (s, x) => s + x.value);
    if (dyn > 0.01) slices.add(SectorSlice(sector: 'Dynamic', value: dyn, pct: dyn));
    return slices;
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      backgroundColor: AppColors.canvas,
      body: AppBackground(
        child: SafeArea(
          child: _loading
              ? const Padding(padding: EdgeInsets.only(top: 60), child: LoadingState())
              : _error != null
                  ? Padding(
                      padding: const EdgeInsets.all(20),
                      child: ErrorBanner(message: _error!, onRetry: _load))
                  : _body(),
        ),
      ),
    );
  }

  Widget _body() {
    final inst = _inst!;
    final cfg = inst.cryptoConfig ?? const {};
    final band = (cfg['band'] ?? '').toString().toLowerCase();
    final strat = (cfg['strategy'] ?? '').toString();
    final brokerage = _brokerages.cast<Map<String, dynamic>?>().firstWhere(
        (b) => b?['id']?.toString() == inst.brokerageId,
        orElse: () => null);
    final isPaper = brokerage?['alpaca_paper'] == true;

    return RefreshIndicator(
      onRefresh: _load,
      color: AppColors.primary,
      backgroundColor: AppColors.surface,
      child: ListView(
        padding: const EdgeInsets.fromLTRB(20, 4, 20, 40),
        children: [
          // App bar row
          Row(
            children: [
              IconButton(
                icon: const Icon(Icons.arrow_back, size: 22),
                color: AppColors.textMuted,
                onPressed: () =>
                    context.canPop() ? context.pop() : context.go('/crypto'),
              ),
              const Spacer(),
              IconButton(
                icon: const Icon(Icons.refresh, size: 22),
                color: AppColors.textMuted,
                onPressed: _load,
              ),
            ],
          ),
          const SizedBox(height: 4),
          // Header
          Row(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              IconTile(icon: symbol('currency_bitcoin')),
              const SizedBox(width: 12),
              Expanded(
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Text(inst.name.isNotEmpty ? inst.name : inst.id,
                        style: AppTextStyles.h2, overflow: TextOverflow.ellipsis),
                    const SizedBox(height: 2),
                    Text(inst.id,
                        style: AppTextStyles.mono(11, color: AppColors.textDim),
                        overflow: TextOverflow.ellipsis),
                  ],
                ),
              ),
              const SizedBox(width: 8),
              Column(
                crossAxisAlignment: CrossAxisAlignment.end,
                children: [
                  const AppBadge(label: '24/7', color: AppColors.info),
                  const SizedBox(height: 6),
                  StatusPill(
                    label: inst.crashed
                        ? 'Crashed'
                        : (inst.runCommand ? 'Running' : 'Stopped'),
                    color: inst.crashed
                        ? AppColors.danger
                        : (inst.runCommand ? AppColors.success : AppColors.textDim),
                    pulsing: inst.runCommand && !inst.crashed,
                  ),
                ],
              ),
            ],
          ),
          const SizedBox(height: 16),
          // Actions
          Row(
            children: [
              Expanded(
                child: _actionBtn(
                  icon: inst.runCommand ? symbol('stop') : symbol('play_arrow'),
                  label: inst.runCommand ? 'Stop' : 'Start',
                  color: inst.runCommand ? AppColors.warning : AppColors.success,
                  onTap: _busy ? null : _toggleRun,
                ),
              ),
              const SizedBox(width: 10),
              Expanded(
                child: _actionBtn(
                  icon: symbol('edit'),
                  label: 'Edit',
                  color: AppColors.primary,
                  onTap: _openEdit,
                ),
              ),
            ],
          ),
          const SizedBox(height: 20),

          // Instance info
          _card('INSTANCE INFO', [
            _kv('Band', band.isEmpty ? '—' : '${band[0].toUpperCase()}${band.substring(1)}'),
            _kv('Cadence', _cadence[band] ?? '~15 min'),
            _kv('Uptime',
                inst.runCommand ? _fmtDuration(inst.uptimeSeconds) : '—',
                valueColor: inst.runCommand ? AppColors.success : null),
            _kv('Created by', inst.createdBy),
          ]),
          const SizedBox(height: 12),

          // Brokerage
          _card('BROKERAGE', [
            _kv('Account', brokerage?['account_name']?.toString() ?? inst.brokerageId ?? '—'),
            _kv('Mode', isPaper ? 'Paper' : 'Live',
                valueColor: isPaper ? AppColors.info : AppColors.success),
            _kv('Account value', _value != null ? _fmtUsd(_value) : '—'),
          ]),
          const SizedBox(height: 12),

          // Allocation
          GlassCard(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Text('ALLOCATION', style: AppTextStyles.eyebrow),
                const SizedBox(height: 12),
                Center(
                  child: SizedBox(width: 220, child: Sector3DChart(slices: _slices())),
                ),
                const SizedBox(height: 12),
                Text('Dynamic strategy',
                    style: AppTextStyles.nano.copyWith(color: AppColors.textDim)),
                const SizedBox(height: 2),
                Text(strat.isEmpty ? '—' : '${strat[0].toUpperCase()}${strat.substring(1)}',
                    style: AppTextStyles.body.copyWith(
                        color: AppColors.primary, fontWeight: FontWeight.w600)),
                const SizedBox(height: 12),
                Wrap(spacing: 6, runSpacing: 6, children: _allocChips()),
              ],
            ),
          ),
          const SizedBox(height: 20),

          // Backtests
          Row(
            children: [
              Text('BACKTESTS', style: AppTextStyles.eyebrow),
              const SizedBox(width: 6),
              Text('(${_backtests.length})',
                  style: AppTextStyles.eyebrow.copyWith(color: AppColors.textFaint)),
              const Spacer(),
              GestureDetector(
                onTap: _openBacktest,
                child: Container(
                  padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 7),
                  decoration: BoxDecoration(
                    color: AppColors.fill(AppColors.primary),
                    borderRadius: BorderRadius.circular(9),
                    border: Border.all(color: AppColors.stroke(AppColors.primary)),
                  ),
                  child: Row(mainAxisSize: MainAxisSize.min, children: [
                    Icon(symbol('add'), size: 15, color: AppColors.primary),
                    const SizedBox(width: 4),
                    Text('New Backtest',
                        style: AppTextStyles.meta.copyWith(
                            color: AppColors.primary, fontWeight: FontWeight.w600)),
                  ]),
                ),
              ),
            ],
          ),
          const SizedBox(height: 12),
          if (_backtests.isEmpty)
            GlassCard(
              child: Column(children: [
                Icon(symbol('analytics'), size: 30, color: AppColors.textFaint),
                const SizedBox(height: 8),
                Text('No backtests yet for this instance.',
                    style: AppTextStyles.meta.copyWith(color: AppColors.textDim)),
              ]),
            )
          else
            for (final b in _backtests) ...[
              _backtestRow(b),
              const SizedBox(height: 8),
            ],
        ],
      ),
    );
  }

  List<Widget> _allocChips() {
    final inst = _inst!;
    final allocs = (inst.cryptoConfig?['allocations'] as List?) ?? const [];
    final chips = <Widget>[];
    var fixed = 0.0;
    for (final a in allocs.whereType<Map>()) {
      final pct = ((a['pct'] as num?)?.toDouble() ?? 0) * 100;
      fixed += pct;
      final sym = (a['symbol'] ?? '').toString().split('/').first;
      chips.add(_chip('$sym ${pct.round()}%', AppColors.textMd, AppColors.border));
    }
    final dyn = (100 - fixed).clamp(0, 100).round();
    chips.add(_chip('Dynamic $dyn%', AppColors.primary,
        AppColors.stroke(AppColors.primary),
        bg: AppColors.fill(AppColors.primary)));
    return chips;
  }

  Widget _chip(String text, Color fg, Color border, {Color? bg}) => Container(
        padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 3),
        decoration: BoxDecoration(
          color: bg ?? AppColors.surface,
          borderRadius: BorderRadius.circular(6),
          border: Border.all(color: border),
        ),
        child: Text(text, style: AppTextStyles.mono(11, color: fg)),
      );

  Widget _card(String title, List<Widget> rows) => GlassCard(
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Text(title, style: AppTextStyles.eyebrow),
            const SizedBox(height: 12),
            ...rows,
          ],
        ),
      );

  Widget _kv(String label, String value, {Color? valueColor}) => Padding(
        padding: const EdgeInsets.only(bottom: 10),
        child: Row(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Text(label, style: AppTextStyles.meta.copyWith(color: AppColors.textDim)),
            const Spacer(),
            Flexible(
              child: Text(value,
                  textAlign: TextAlign.right,
                  style: AppTextStyles.meta.copyWith(
                      color: valueColor ?? AppColors.textHi,
                      fontWeight: FontWeight.w600)),
            ),
          ],
        ),
      );

  Widget _actionBtn(
      {required IconData icon,
      required String label,
      required Color color,
      VoidCallback? onTap}) {
    return Opacity(
      opacity: onTap == null ? 0.4 : 1,
      child: GestureDetector(
        onTap: onTap,
        child: Container(
          padding: const EdgeInsets.symmetric(vertical: 11),
          decoration: BoxDecoration(
            color: AppColors.fill(color),
            borderRadius: BorderRadius.circular(10),
            border: Border.all(color: AppColors.stroke(color)),
          ),
          child: Row(
            mainAxisAlignment: MainAxisAlignment.center,
            children: [
              Icon(icon, size: 16, color: color),
              const SizedBox(width: 6),
              Text(label,
                  style: AppTextStyles.body.copyWith(
                      color: color, fontWeight: FontWeight.w600)),
            ],
          ),
        ),
      ),
    );
  }

  Widget _backtestRow(BacktestRow b) {
    final coins = b.stocks.map((s) => s.split('/').first).toList();
    final color = _btStatusColor(b.status);
    return GestureDetector(
      onTap: () => context.push('/backtests/${b.id}'),
      child: GlassCard(
        padding: const EdgeInsets.symmetric(horizontal: 14, vertical: 12),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Row(
              children: [
                Text('#${b.id}',
                    style: AppTextStyles.mono(11, color: AppColors.textDim)),
                const SizedBox(width: 8),
                Container(
                  padding: const EdgeInsets.symmetric(horizontal: 7, vertical: 2),
                  decoration: BoxDecoration(
                    color: AppColors.fill(color),
                    borderRadius: BorderRadius.circular(5),
                    border: Border.all(color: AppColors.stroke(color)),
                  ),
                  child: Text(b.status.toUpperCase(),
                      style: AppTextStyles.nano.copyWith(
                          color: color, fontWeight: FontWeight.w700)),
                ),
                const Spacer(),
                Text(_fmtPnl(b.pnl),
                    style: AppTextStyles.body.copyWith(
                        color: _pnlColor(b.pnl), fontWeight: FontWeight.w700)),
                const SizedBox(width: 8),
                Text(_fmtPct(b.pnlPercent),
                    style: AppTextStyles.meta.copyWith(color: _pnlColor(b.pnlPercent))),
              ],
            ),
            const SizedBox(height: 8),
            Row(
              children: [
                Expanded(
                  child: Text(
                    coins.isEmpty ? 'Dynamic' : coins.take(5).join('  '),
                    style: AppTextStyles.mono(11,
                        color: coins.isEmpty ? AppColors.primary : AppColors.textMd),
                    overflow: TextOverflow.ellipsis,
                  ),
                ),
                Text('${b.startDate ?? '?'} → ${b.endDate ?? '?'}',
                    style: AppTextStyles.nano.copyWith(color: AppColors.textDim)),
              ],
            ),
          ],
        ),
      ),
    );
  }
}
