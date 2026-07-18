import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../../core/theme/app_colors.dart';
import '../../../core/theme/app_text_styles.dart';
import '../../../core/widgets/material_symbols.dart';
import '../../dashboard/application/portfolio_analytics.dart' show SectorSlice;
import '../../dashboard/presentation/sector_3d_chart.dart';
import '../data/crypto_repository.dart';

/// Opens the unified create/edit crypto-instance sheet.
Future<void> showCryptoInstanceSheet(
  BuildContext context, {
  String? editInstanceId,
  String? editName,
  String? editBrokerageId,
  Map<String, dynamic>? editConfig,
  List<String>? editStocks,
  required VoidCallback onSaved,
}) {
  return showModalBottomSheet<void>(
    context: context,
    isScrollControlled: true,
    backgroundColor: Colors.transparent,
    builder: (_) => CryptoInstanceSheet(
      editInstanceId: editInstanceId,
      editName: editName,
      editBrokerageId: editBrokerageId,
      editConfig: editConfig,
      editStocks: editStocks,
      onSaved: onSaved,
    ),
  );
}

// ── Coin catalog ──────────────────────────────────────────────────────────────

class _CoinMeta {
  const _CoinMeta(this.sym, this.name);
  final String sym;
  final String name;
}

const _kCatalog = <_CoinMeta>[
  _CoinMeta('BTC', 'Bitcoin'),
  _CoinMeta('ETH', 'Ethereum'),
  _CoinMeta('SOL', 'Solana'),
  _CoinMeta('LINK', 'Chainlink'),
  _CoinMeta('AVAX', 'Avalanche'),
  _CoinMeta('DOT', 'Polkadot'),
  _CoinMeta('LTC', 'Litecoin'),
  _CoinMeta('UNI', 'Uniswap'),
  _CoinMeta('AAVE', 'Aave'),
  _CoinMeta('DOGE', 'Dogecoin'),
  _CoinMeta('BCH', 'Bitcoin Cash'),
  _CoinMeta('MKR', 'Maker'),
];

String _nameFor(String sym) => _kCatalog
    .firstWhere((c) => c.sym == sym, orElse: () => _CoinMeta(sym, sym))
    .name;

/// 'BTC/USD' → 'BTC'; already-base stays as-is.
String _baseOf(String pairOrSym) {
  final s = pairOrSym.trim().toUpperCase();
  final slash = s.indexOf('/');
  return slash > 0 ? s.substring(0, slash) : s;
}

// Distinct per-coin hues for the table dots + meter + legend. The reused 3D
// sector ring stays in its native violet metal (per-coin donut hues are optional).
const _kPalette = <Color>[
  Color(0xFFA78BFA),
  Color(0xFF7C9BFF),
  Color(0xFF5AD1E0),
  Color(0xFF5EE6B8),
  Color(0xFFF0B354),
  Color(0xFFF3799F),
  Color(0xFFB98BFF),
];
const _kDynamicColor = Color(0xFF7C5CE6);

/// Dynamic-strategy options (the crypto strategy classes). Resolved to an
/// integer `strategy_id` at submit time by matching a Strategies doc by name.
// Display name lowercases to the backend strategy id (e.g. 'Meanrev' -> 'meanrev').
const _kStrategies = <(String, String)>[
  ('Meanrev', 'Mean-Reversion (RSI) — buys oversold majors (RSI-14 < 35) only while above their 200h trend MA ("healthy dips, not falling knives"), holds 2, sells when RSI recovers past 55. Mostly in cash. Top backtest performer: +24% over 400d at Binance.US fees while BTC fell −42%, ~8% drawdown. Needs a low-fee venue — loses at Alpaca 0.25%.'),
  ('Adaptive', 'Adaptive (regime switcher) — holds the whole basket (buy & hold) while the market is above both its 200-day and 50-day trend; switches to gated Mean-Reversion dip-buying when the regime breaks. Prod backtest Oct-23..Mar-24: +132% vs Mean-Reversion\'s +56% (holding +181%). Crash protection strong but universe-dependent: 2022 with SOL/AVAX lost 19% vs holding\'s −74.5%. Weakness: chop goes modestly negative. Mean-Reversion = max bear safety; Adaptive = bull participation.'),
  ('Connors', 'Connors (fast RSI) — quicker cousin of Mean-Reversion: buys deeply oversold dips above the trend MA, exits fast when price snaps back above a short MA. Higher turnover. Backtest: modest but robust +6% over 400d. Also needs a low-fee venue.'),
  ('Momentum', 'Trend-follows the auto-discovered universe — leans into coins whose momentum is strengthening. Trend strategies were whipsawed in choppy/down backtests; best in a sustained bull run.'),
  ('Allocator', 'Risk-weights across coins toward balanced target weights. Diversified, steadier exposure; a slower rebalancer.'),
  ('Fast', 'Tactical Donchian breakout trend-follower; quick in/out on short-term signals. Most responsive, highest turnover.'),
  ('Reference', 'A simple buy-and-rebalance baseline to benchmark the others against.'),
];

// Recommended band per strategy (the cadence each was validated at). Mean-reversion
// strategies were validated on 60-min (Low) bars.
const _kStrategyRecommendedBand = <String, String>{
  'meanrev': 'low',
  'adaptive': 'low',
  'connors': 'low',
  'momentum': 'medium',
  'allocator': 'low',
  'fast': 'high',
  'reference': 'low',
};

String _strategyBlurb(String name) => _kStrategies
    .firstWhere((s) => s.$1 == name, orElse: () => (name, ''))
    .$2;

String? _recommendedBandFor(String strategy) =>
    _kStrategyRecommendedBand[strategy.toLowerCase()];

const _kBands = <(String, String)>[
  ('high', 'High'),
  ('medium', 'Medium'),
  ('low', 'Low'),
];

const _kBandBlurb = <String, String>{
  'high': 'High — checks every ~5 min. Most reactive to fast moves; more trades and turnover.',
  'medium': 'Medium — checks every ~15 min. Balanced reactivity vs. turnover. The default.',
  'low': 'Low — checks every ~60 min. Calmest; fewest trades, lowest fees, slower to react.',
};

// ── Row state ─────────────────────────────────────────────────────────────────

class _AllocRow {
  _AllocRow({required this.sym, required this.pct});
  final String sym;
  double pct; // 0..100
  final TextEditingController pctCtrl = TextEditingController();
  final TextEditingController usdCtrl = TextEditingController();
  String get pair => '$sym/USD';
  String get name => _nameFor(sym);
  void dispose() {
    pctCtrl.dispose();
    usdCtrl.dispose();
  }
}

// ── Sheet ─────────────────────────────────────────────────────────────────────

class CryptoInstanceSheet extends ConsumerStatefulWidget {
  const CryptoInstanceSheet({
    super.key,
    this.editInstanceId,
    this.editName,
    this.editBrokerageId,
    this.editConfig,
    this.editStocks,
    required this.onSaved,
  });

  final String? editInstanceId;
  final String? editName;
  final String? editBrokerageId;
  final Map<String, dynamic>? editConfig;
  final List<String>? editStocks;
  final VoidCallback onSaved;

  @override
  ConsumerState<CryptoInstanceSheet> createState() =>
      _CryptoInstanceSheetState();
}

class _CryptoInstanceSheetState extends ConsumerState<CryptoInstanceSheet> {
  final _id = TextEditingController();
  final _name = TextEditingController();

  String _brokerageId = '';
  String _band = 'medium';
  String _strategy = 'Momentum';
  String _mode = 'pct'; // 'pct' | 'usd'
  final List<_AllocRow> _rows = [];

  List<Map<String, dynamic>> _brokerages = [];
  List<Map<String, dynamic>> _strategies = [];
  double _equity = 0;
  bool _loadingEquity = false;
  bool _weightsUnknown = false; // edit fallback: exact weights not loadable
  bool _saving = false;
  String _err = '';

  bool get _isEdit => widget.editInstanceId != null;

  @override
  void initState() {
    super.initState();
    if (widget.editName != null) _name.text = widget.editName!;
    if (widget.editBrokerageId != null) _brokerageId = widget.editBrokerageId!;
    if (_isEdit) {
      _prefill();
    } else {
      // Mirror the approved mockup's starting allocation.
      _rows
        ..add(_AllocRow(sym: 'BTC', pct: 10))
        ..add(_AllocRow(sym: 'ETH', pct: 20));
    }
    _syncPctText();
    _loadSelectors();
  }

  void _prefill() {
    final cfg = widget.editConfig;
    final allocs = (cfg?['allocations'] as List?) ?? const [];
    final band = (cfg?['band'] ?? '').toString().toLowerCase();
    if (band.isNotEmpty) _band = band;
    final strat = (cfg?['strategy'] ?? '').toString().trim();
    if (strat.isNotEmpty) {
      _strategy = strat[0].toUpperCase() + strat.substring(1).toLowerCase();
    }
    if (allocs.isNotEmpty) {
      for (final a in allocs.whereType<Map>()) {
        final sym = _baseOf((a['symbol'] ?? '').toString());
        final pct = ((a['pct'] as num?)?.toDouble() ?? 0) * 100;
        if (sym.isNotEmpty) _rows.add(_AllocRow(sym: sym, pct: pct));
      }
      return;
    }
    // Fallback: the list/detail API doesn't surface crypto_config, so exact
    // per-coin weights can't be loaded. Reconstruct the fixed coins from the
    // stored symbol universe at an even split and warn before saving.
    final stocks = widget.editStocks ?? const [];
    if (stocks.isNotEmpty) {
      _weightsUnknown = true;
      final even = double.parse((100.0 / stocks.length).toStringAsFixed(2));
      for (final p in stocks) {
        _rows.add(_AllocRow(sym: _baseOf(p), pct: even));
      }
    }
  }

  Future<void> _loadSelectors() async {
    final repo = ref.read(cryptoRepositoryProvider);
    try {
      final brokerages = await repo.brokerages();
      final strategies = await repo.strategies();
      if (!mounted) return;
      setState(() {
        _brokerages = brokerages;
        _strategies = strategies;
        // Default the brokerage to the first crypto-capable (Alpaca) account.
        if (_brokerageId.isEmpty) {
          final crypto = _cryptoBrokerages(brokerages);
          if (crypto.isNotEmpty) _brokerageId = crypto.first['id'].toString();
        }
      });
    } catch (_) {
      // Non-fatal — selectors just stay empty.
    }
    _loadEquity();
  }

  List<Map<String, dynamic>> _cryptoBrokerages(List<Map<String, dynamic>> all) {
    // Crypto trades on Alpaca or Binance.US (0%/0.02% fees).
    final crypto = all.where((b) {
      final t = (b['brokerage_type'] ?? '').toString().toLowerCase();
      return t.contains('alpaca') || t.contains('binance');
    }).toList();
    return crypto.isNotEmpty ? crypto : all;
  }

  Future<void> _loadEquity() async {
    if (_brokerageId.isEmpty) return;
    setState(() => _loadingEquity = true);
    final v = await ref.read(cryptoRepositoryProvider).accountEquity(_brokerageId);
    if (!mounted) return;
    setState(() {
      _equity = v;
      _loadingEquity = false;
    });
    _syncUsdText();
  }

  @override
  void dispose() {
    _id.dispose();
    _name.dispose();
    for (final r in _rows) {
      r.dispose();
    }
    super.dispose();
  }

  // ── Allocation math ─────────────────────────────────────────────────────────

  double get _fixedSum => _rows.fold(0.0, (s, r) => s + r.pct);
  double get _dynPct => (100 - _fixedSum).clamp(0.0, 100.0);
  bool get _over => _fixedSum > 100.0001;

  String _fmtNum(double v) {
    if (v == v.roundToDouble()) return v.toInt().toString();
    return v.toStringAsFixed(1);
  }

  String _fmtUsd(double v) => '\$${v.round()}';

  void _syncPctText() {
    for (final r in _rows) {
      r.pctCtrl.text = _fmtNum(r.pct);
    }
    _syncUsdText();
  }

  void _syncUsdText() {
    for (final r in _rows) {
      r.usdCtrl.text = (r.pct / 100 * _equity).round().toString();
    }
  }

  void _onPctChanged(_AllocRow r, String raw) {
    var v = double.tryParse(raw.trim()) ?? 0;
    r.pct = v.clamp(0.0, 100.0);
    r.usdCtrl.text = (r.pct / 100 * _equity).round().toString();
    setState(() {});
  }

  void _onUsdChanged(_AllocRow r, String raw) {
    final usd = double.tryParse(raw.trim()) ?? 0;
    final pct = _equity > 0 ? (usd / _equity * 100) : 0.0;
    r.pct = pct.clamp(0.0, 100.0);
    r.pctCtrl.text = _fmtNum(r.pct);
    setState(() {});
  }

  void _addCoin(String sym) {
    setState(() {
      final row = _AllocRow(sym: sym, pct: 0);
      row.pctCtrl.text = '0';
      row.usdCtrl.text = '0';
      _rows.add(row);
    });
  }

  void _removeCoin(_AllocRow r) {
    setState(() {
      _rows.remove(r);
    });
    r.dispose();
  }

  Color _colorFor(int index) => _kPalette[index % _kPalette.length];

  List<SectorSlice> _slices() {
    final slices = <SectorSlice>[
      for (final r in _rows)
        if (r.pct > 0) SectorSlice(sector: r.sym, value: r.pct, pct: r.pct),
    ];
    final dyn = _dynPct;
    if (dyn > 0) {
      slices.add(SectorSlice(sector: 'Dynamic', value: dyn, pct: dyn));
    }
    return slices;
  }

  int? _resolveStrategyId() {
    final want = _strategy.toLowerCase();
    for (final s in _strategies) {
      final name = (s['name'] ?? '').toString().toLowerCase();
      if (name == want) {
        final id = int.tryParse((s['id'] ?? '').toString());
        if (id != null) return id;
      }
    }
    return null;
  }

  // ── Submit ──────────────────────────────────────────────────────────────────

  Future<void> _submit() async {
    if (!_isEdit && _id.text.trim().isEmpty) {
      setState(() => _err = 'Instance ID is required');
      return;
    }
    if (_over) {
      setState(() => _err = 'Over-allocated — fixed weights exceed 100%');
      return;
    }
    setState(() {
      _saving = true;
      _err = '';
    });

    final allocations = [
      for (final r in _rows)
        if (r.pct > 0)
          {'symbol': r.pair, 'pct': double.parse((r.pct / 100).toStringAsFixed(4))},
    ];
    final fixedStocks = [
      for (final r in _rows)
        if (r.pct > 0) r.pair,
    ];
    // The chosen dynamic strategy travels in crypto_config; the broker
    // synthesizes its run_once spec from this (no Strategies row needed).
    final cryptoConfig = {
      'band': _band,
      'strategy': _strategy.toLowerCase(),
      'allocations': allocations,
    };

    try {
      final repo = ref.read(cryptoRepositoryProvider);
      if (_isEdit) {
        // Send EVERY editable field — the PATCH previously carried only
        // {crypto_config, stocks}, so Name and Brokerage edits silently
        // reverted on save (same bug as the web modal, fixed together).
        await repo.updateInstance(widget.editInstanceId!, {
          'name': _name.text.trim(),
          if (_brokerageId.isNotEmpty) 'brokerage_id': _brokerageId,
          // Keep the row's time increment in lockstep with the Band edit.
          'granularity': const {'high': '300', 'medium': '900', 'low': '3600'}[_band] ?? '3600',
          'crypto_config': cryptoConfig,
          'stocks': fixedStocks,
        });
      } else {
        final sid = _resolveStrategyId();
        await repo.createInstance({
          'id': _id.text.trim(),
          if (_name.text.trim().isNotEmpty) 'name': _name.text.trim(),
          // Granularity follows the Band (bar size each strategy was
          // validated on; low = 60-min bars) — was hardcoded '900'.
          'granularity': const {'high': '300', 'medium': '900', 'low': '3600'}[_band] ?? '3600',
          'run_command': false,
          'kind': 'crypto',
          if (_brokerageId.isNotEmpty) 'brokerage_id': _brokerageId,
          if (sid != null) 'strategy_id': sid,
          'stocks': fixedStocks,
          'crypto_config': cryptoConfig,
        });
      }
      if (mounted) Navigator.pop(context);
      widget.onSaved();
    } catch (e) {
      if (mounted) {
        setState(() {
          _err = '$e';
          _saving = false;
        });
      }
    }
  }

  // ── Build ───────────────────────────────────────────────────────────────────

  @override
  Widget build(BuildContext context) {
    final bottom = MediaQuery.viewInsetsOf(context).bottom;
    final brokerageLabel = _brokerages
        .cast<Map<String, dynamic>?>()
        .firstWhere((b) => b?['id'].toString() == _brokerageId, orElse: () => null);

    return Padding(
      padding: EdgeInsets.only(bottom: bottom),
      child: Container(
        constraints:
            BoxConstraints(maxHeight: MediaQuery.sizeOf(context).height * 0.92),
        decoration: const BoxDecoration(
          color: AppColors.panel,
          borderRadius: BorderRadius.vertical(top: Radius.circular(20)),
        ),
        child: Column(
          mainAxisSize: MainAxisSize.min,
          children: [
            // Header
            Padding(
              padding: const EdgeInsets.fromLTRB(20, 16, 20, 8),
              child: Row(children: [
                Icon(symbol('currency_bitcoin'),
                    color: AppColors.primary, size: 20),
                const SizedBox(width: 8),
                Text(_isEdit ? 'Edit crypto instance' : 'New crypto instance',
                    style: AppTextStyles.cardTitle),
                const Spacer(),
                GestureDetector(
                  onTap: () => Navigator.pop(context),
                  child: Icon(Icons.close, color: AppColors.textDim),
                ),
              ]),
            ),
            Flexible(
              child: ListView(
                padding: const EdgeInsets.fromLTRB(20, 4, 20, 12),
                children: [
                  Text(
                    'Pin fixed weights for the coins you want, and leave the rest '
                    'Dynamic — auto-discovered and traded for you. Empty means '
                    '100% dynamic.',
                    style: AppTextStyles.nano.copyWith(color: AppColors.textDim),
                  ),
                  const SizedBox(height: 14),

                  if (!_isEdit) ...[
                    _field(_id, 'Instance ID *', hint: 'e.g. crypto-main'),
                    _field(_name, 'Name', hint: 'Optional display name'),
                    _label('Brokerage', 'Which account this bot trades on (Alpaca).'),
                    _brokerageDropdown(),
                    const SizedBox(height: 6),
                  ] else
                    _field(_name, 'Name', hint: 'Optional display name'),

                  Padding(
                    padding: const EdgeInsets.only(top: 4, bottom: 14),
                    child: Text(
                      _loadingEquity
                          ? 'Account equity …'
                          : (_equity > 0
                              ? 'Account equity ${_fmtUsd(_equity)}'
                                  '${brokerageLabel != null ? ' · ${brokerageLabel['account_name']}' : ''}'
                              : 'Account equity unavailable — % still works'),
                      style: AppTextStyles.nano.copyWith(
                          color:
                              _equity > 0 ? AppColors.textMd : AppColors.warning),
                    ),
                  ),

                  // Band
                  _label('Volatility band',
                      'Sets the 24/7 monitor cadence (High = every 5m, Medium = 15m, Low = 60m).'),
                  _bandPills(),
                  const SizedBox(height: 6),
                  Text(_kBandBlurb[_band] ?? '',
                      style: AppTextStyles.nano
                          .copyWith(color: AppColors.textDim, height: 1.35)),
                  if (_recommendedBandFor(_strategy) != null &&
                      _recommendedBandFor(_strategy) != _band) ...[
                    const SizedBox(height: 4),
                    GestureDetector(
                      onTap: () => setState(
                          () => _band = _recommendedBandFor(_strategy)!),
                      child: Text(
                        'Recommended ${_recommendedBandFor(_strategy)![0].toUpperCase()}${_recommendedBandFor(_strategy)!.substring(1)} for $_strategy — tap to use',
                        style: AppTextStyles.nano.copyWith(
                            color: AppColors.primary,
                            fontWeight: FontWeight.w600,
                            height: 1.35),
                      ),
                    ),
                  ],
                  const SizedBox(height: 14),

                  // Dynamic strategy
                  _label('Dynamic strategy',
                      'How the auto-discovered (Dynamic) portion is traded.'),
                  _strategyDropdown(),
                  const SizedBox(height: 6),
                  Text(_strategyBlurb(_strategy),
                      style: AppTextStyles.nano
                          .copyWith(color: AppColors.textDim, height: 1.35)),
                  const SizedBox(height: 16),

                  // Allocation editor
                  _allocToolbar(),
                  const SizedBox(height: 12),
                  Center(
                    child: SizedBox(
                      width: 280,
                      child: Sector3DChart(slices: _slices()),
                    ),
                  ),
                  const SizedBox(height: 8),
                  _legend(),
                  const SizedBox(height: 12),
                  if (_weightsUnknown)
                    Padding(
                      padding: const EdgeInsets.only(bottom: 10),
                      child: Text(
                        'Current weights couldn’t be loaded — showing an even '
                        'split. Adjust before saving.',
                        style: AppTextStyles.nano
                            .copyWith(color: AppColors.warning),
                      ),
                    ),
                  _tableHeader(),
                  for (var i = 0; i < _rows.length; i++) _coinRow(_rows[i], i),
                  _dynamicRow(),
                  const SizedBox(height: 12),
                  _addBar(),
                  const SizedBox(height: 16),
                  _meter(),

                  if (_err.isNotEmpty)
                    Padding(
                      padding: const EdgeInsets.only(top: 12),
                      child: Text(_err,
                          style: AppTextStyles.meta
                              .copyWith(color: AppColors.danger)),
                    ),
                ],
              ),
            ),
            // Footer
            Padding(
              padding: EdgeInsets.fromLTRB(
                  20, 8, 20, 16 + MediaQuery.viewPaddingOf(context).bottom),
              child: SizedBox(
                width: double.infinity,
                child: ElevatedButton(
                  onPressed: _saving ? null : _submit,
                  style: ElevatedButton.styleFrom(
                    backgroundColor: AppColors.primary,
                    foregroundColor: AppColors.onPrimary,
                    padding: const EdgeInsets.symmetric(vertical: 14),
                    shape: RoundedRectangleBorder(
                        borderRadius: BorderRadius.circular(12)),
                  ),
                  child: Text(
                    _saving
                        ? 'Saving…'
                        : (_isEdit ? 'Save changes' : 'Create instance'),
                    style: AppTextStyles.body.copyWith(
                        fontWeight: FontWeight.bold, color: AppColors.onPrimary),
                  ),
                ),
              ),
            ),
          ],
        ),
      ),
    );
  }

  // ── Sub-widgets ─────────────────────────────────────────────────────────────

  Widget _brokerageDropdown() {
    final items = _cryptoBrokerages(_brokerages);
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 12),
      decoration: BoxDecoration(
        color: AppColors.surface,
        borderRadius: BorderRadius.circular(10),
        border: Border.all(color: AppColors.border),
      ),
      child: DropdownButtonHideUnderline(
        child: DropdownButton<String>(
          isExpanded: true,
          value: _brokerageId.isEmpty ? null : _brokerageId,
          hint: Text('Select a brokerage',
              style: AppTextStyles.body.copyWith(color: AppColors.textFaint)),
          dropdownColor: AppColors.panel,
          icon: Icon(Icons.expand_more, color: AppColors.textDim),
          style: AppTextStyles.body.copyWith(color: AppColors.textHi),
          items: [
            for (final b in items)
              DropdownMenuItem(
                value: b['id'].toString(),
                child: Text(
                  '${b['account_name'] ?? ''} (${b['brokerage_type'] ?? ''})'.trim(),
                  overflow: TextOverflow.ellipsis,
                ),
              ),
          ],
          onChanged: (v) {
            if (v == null) return;
            setState(() => _brokerageId = v);
            _loadEquity();
          },
        ),
      ),
    );
  }

  Widget _bandPills() {
    return Container(
      padding: const EdgeInsets.all(4),
      decoration: BoxDecoration(
        color: AppColors.surface,
        borderRadius: BorderRadius.circular(10),
        border: Border.all(color: AppColors.border),
      ),
      child: Row(
        children: _kBands.map((b) {
          final on = _band == b.$1;
          return Expanded(
            child: GestureDetector(
              onTap: () => setState(() => _band = b.$1),
              child: Container(
                margin: const EdgeInsets.symmetric(horizontal: 2),
                padding: const EdgeInsets.symmetric(vertical: 8),
                decoration: BoxDecoration(
                  color: on ? AppColors.primary : Colors.transparent,
                  borderRadius: BorderRadius.circular(7),
                ),
                child: Text(
                  b.$2,
                  textAlign: TextAlign.center,
                  style: AppTextStyles.meta.copyWith(
                    color: on ? AppColors.onPrimary : AppColors.textMuted,
                    fontWeight: FontWeight.bold,
                  ),
                ),
              ),
            ),
          );
        }).toList(),
      ),
    );
  }

  Widget _strategyDropdown() {
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 12),
      decoration: BoxDecoration(
        color: AppColors.surface,
        borderRadius: BorderRadius.circular(10),
        border: Border.all(color: AppColors.border),
      ),
      child: DropdownButtonHideUnderline(
        child: DropdownButton<String>(
          isExpanded: true,
          value: _strategy,
          dropdownColor: AppColors.panel,
          icon: Icon(Icons.expand_more, color: AppColors.textDim),
          style: AppTextStyles.body.copyWith(color: AppColors.textHi),
          items: [
            for (final s in _kStrategies)
              DropdownMenuItem(
                value: s.$1,
                child: Text(s.$1),
              ),
          ],
          onChanged: (v) => setState(() {
            _strategy = v ?? _strategy;
            // Auto-apply the strategy's recommended band (user can still change it).
            final rec = _recommendedBandFor(_strategy);
            if (rec != null) _band = rec;
          }),
        ),
      ),
    );
  }

  Widget _allocToolbar() {
    Widget seg(String mode, String label) {
      final on = _mode == mode;
      return GestureDetector(
        onTap: () => setState(() => _mode = mode),
        child: Container(
          padding: const EdgeInsets.symmetric(horizontal: 14, vertical: 6),
          decoration: BoxDecoration(
            color: on ? AppColors.primary : Colors.transparent,
            borderRadius: BorderRadius.circular(7),
          ),
          child: Text(label,
              style: AppTextStyles.meta.copyWith(
                color: on ? AppColors.onPrimary : AppColors.textMuted,
                fontWeight: FontWeight.bold,
              )),
        ),
      );
    }

    return Row(
      children: [
        Text('ALLOCATION', style: AppTextStyles.eyebrow),
        const Spacer(),
        Container(
          padding: const EdgeInsets.all(2),
          decoration: BoxDecoration(
            color: AppColors.surface,
            borderRadius: BorderRadius.circular(9),
            border: Border.all(color: AppColors.border),
          ),
          child: Row(children: [seg('pct', '%'), seg('usd', '\$')]),
        ),
      ],
    );
  }

  Widget _legend() {
    final chips = <Widget>[];
    var i = 0;
    for (final r in _rows) {
      if (r.pct > 0) {
        chips.add(_legendChip(_colorFor(i), '${r.sym} ${_fmtNum(r.pct)}%'));
      }
      i++;
    }
    if (_dynPct > 0) {
      chips.add(_legendChip(_kDynamicColor, 'Dynamic ${_fmtNum(_dynPct)}%'));
    }
    return Wrap(
      alignment: WrapAlignment.center,
      spacing: 12,
      runSpacing: 6,
      children: chips,
    );
  }

  Widget _legendChip(Color c, String label) => Row(
        mainAxisSize: MainAxisSize.min,
        children: [
          Container(
            width: 9,
            height: 9,
            decoration:
                BoxDecoration(color: c, borderRadius: BorderRadius.circular(3)),
          ),
          const SizedBox(width: 6),
          Text(label, style: AppTextStyles.nano.copyWith(color: AppColors.textMd)),
        ],
      );

  Widget _tableHeader() => Padding(
        padding: const EdgeInsets.fromLTRB(2, 0, 2, 6),
        child: Row(
          children: [
            const SizedBox(width: 18),
            Expanded(
                child: Text('COIN',
                    style: AppTextStyles.nano
                        .copyWith(color: AppColors.textDim, letterSpacing: 1))),
            SizedBox(
                width: 132,
                child: Text(_mode == 'pct' ? 'WEIGHT · ≈USD' : 'USD · ≈WEIGHT',
                    textAlign: TextAlign.right,
                    style: AppTextStyles.nano
                        .copyWith(color: AppColors.textDim, letterSpacing: 1))),
            const SizedBox(width: 26),
          ],
        ),
      );

  Widget _coinRow(_AllocRow r, int index) {
    final usd = r.pct / 100 * _equity;
    final pctPrimary = _mode == 'pct';
    return Container(
      padding: const EdgeInsets.symmetric(vertical: 6),
      decoration: const BoxDecoration(
        border: Border(top: BorderSide(color: AppColors.border)),
      ),
      child: Row(
        children: [
          Container(
            width: 10,
            height: 10,
            decoration: BoxDecoration(
                color: _colorFor(index),
                borderRadius: BorderRadius.circular(3)),
          ),
          const SizedBox(width: 8),
          Expanded(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Text(r.sym,
                    style: AppTextStyles.body.copyWith(
                        color: AppColors.textHi, fontWeight: FontWeight.w600)),
                Text(r.name,
                    style:
                        AppTextStyles.nano.copyWith(color: AppColors.textMuted),
                    overflow: TextOverflow.ellipsis),
              ],
            ),
          ),
          // Primary editable field (unit follows the %/$ toggle).
          SizedBox(
            width: 76,
            child: pctPrimary
                ? _numField(r.pctCtrl, suffix: '%',
                    onChanged: (v) => _onPctChanged(r, v))
                : _numField(r.usdCtrl, prefix: '\$',
                    onChanged: (v) => _onUsdChanged(r, v)),
          ),
          const SizedBox(width: 8),
          // Converted counterpart (read-only).
          SizedBox(
            width: 48,
            child: Text(
              pctPrimary ? '≈${_fmtUsd(usd)}' : '≈${_fmtNum(r.pct)}%',
              textAlign: TextAlign.right,
              style: AppTextStyles.nano.copyWith(color: AppColors.textMuted),
              overflow: TextOverflow.ellipsis,
            ),
          ),
          SizedBox(
            width: 26,
            child: GestureDetector(
              onTap: () => _removeCoin(r),
              child: Icon(Icons.close, size: 16, color: AppColors.textDim),
            ),
          ),
        ],
      ),
    );
  }

  Widget _dynamicRow() {
    final usd = _dynPct / 100 * _equity;
    return Container(
      margin: const EdgeInsets.only(top: 2),
      padding: const EdgeInsets.symmetric(horizontal: 4, vertical: 8),
      decoration: BoxDecoration(
        color: AppColors.fill(AppColors.primary),
        borderRadius: BorderRadius.circular(10),
      ),
      child: Row(
        children: [
          Container(
            width: 10,
            height: 10,
            decoration: BoxDecoration(
                color: _kDynamicColor, borderRadius: BorderRadius.circular(3)),
          ),
          const SizedBox(width: 8),
          Expanded(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Text('Dynamic',
                    style: AppTextStyles.body.copyWith(
                        color: AppColors.textHi, fontWeight: FontWeight.w600)),
                Text('auto-discover & trade',
                    style: AppTextStyles.nano.copyWith(color: AppColors.primary)),
              ],
            ),
          ),
          Text('${_fmtNum(_dynPct)}%',
              style: AppTextStyles.value
                  .copyWith(color: AppColors.primary, fontSize: 14)),
          const SizedBox(width: 8),
          SizedBox(
            width: 48,
            child: Text(_fmtUsd(usd),
                textAlign: TextAlign.right,
                style: AppTextStyles.nano.copyWith(color: AppColors.textMd)),
          ),
          const SizedBox(width: 26),
        ],
      ),
    );
  }

  Widget _addBar() {
    final have = _rows.map((r) => r.sym).toSet();
    final remaining =
        _kCatalog.where((c) => !have.contains(c.sym)).toList();
    if (remaining.isEmpty) {
      return Text('All catalog coins added.',
          style: AppTextStyles.nano.copyWith(color: AppColors.textDim));
    }
    return _AddCoinBar(
      remaining: remaining,
      onAdd: _addCoin,
    );
  }

  Widget _meter() {
    final segs = <Widget>[];
    var i = 0;
    for (final r in _rows) {
      if (r.pct > 0) {
        segs.add(Expanded(
          flex: (r.pct * 10).round().clamp(1, 1000),
          child: Container(color: _colorFor(i)),
        ));
      }
      i++;
    }
    if (_dynPct > 0) {
      segs.add(Expanded(
        flex: (_dynPct * 10).round().clamp(1, 1000),
        child: Container(color: _kDynamicColor.withValues(alpha: 0.55)),
      ));
    }
    if (segs.isEmpty) {
      segs.add(Expanded(child: Container(color: AppColors.surface)));
    }
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        ClipRRect(
          borderRadius: BorderRadius.circular(999),
          child: Container(
            height: 8,
            decoration: BoxDecoration(
              color: AppColors.surface,
              border: Border.all(color: AppColors.border),
              borderRadius: BorderRadius.circular(999),
            ),
            child: Row(children: segs),
          ),
        ),
        const SizedBox(height: 9),
        Row(
          children: [
            Expanded(
              child: RichText(
                text: TextSpan(
                  style: AppTextStyles.meta.copyWith(color: AppColors.textMuted),
                  children: [
                    const TextSpan(text: 'Fixed '),
                    TextSpan(
                        text: '${_fmtNum(_fixedSum)}%',
                        style: AppTextStyles.meta.copyWith(
                            color: AppColors.textHi,
                            fontWeight: FontWeight.w600)),
                    const TextSpan(text: '  ·  Dynamic '),
                    TextSpan(
                        text: '${_fmtNum(_dynPct)}%',
                        style: AppTextStyles.meta.copyWith(
                            color: AppColors.primary,
                            fontWeight: FontWeight.w600)),
                  ],
                ),
              ),
            ),
            Text(
              _over
                  ? 'Over by ${_fmtNum(_fixedSum - 100)}%'
                  : '${_fmtUsd(_dynPct / 100 * _equity)} flexible',
              style: AppTextStyles.meta.copyWith(
                  color: _over ? AppColors.danger : AppColors.textMuted),
            ),
          ],
        ),
        if (!_over && _dynPct <= 0)
          Padding(
            padding: const EdgeInsets.only(top: 8),
            child: Text(
              '⚠ Dynamic 0% — the $_strategy strategy won\'t trade (buy-and-hold). '
              'Lower a fixed weight to give it a dynamic budget to trade.',
              style: AppTextStyles.nano
                  .copyWith(color: AppColors.warning, height: 1.35),
            ),
          ),
      ],
    );
  }

  // ── Shared field helpers (mirrors the Kalshi sheet) ─────────────────────────

  Widget _numField(TextEditingController c,
      {String? prefix, String? suffix, required ValueChanged<String> onChanged}) {
    return TextField(
      controller: c,
      keyboardType: const TextInputType.numberWithOptions(decimal: true),
      textAlign: TextAlign.right,
      onChanged: onChanged,
      style: AppTextStyles.body.copyWith(color: AppColors.textHi),
      decoration: InputDecoration(
        isDense: true,
        prefixText: prefix,
        suffixText: suffix,
        prefixStyle: AppTextStyles.nano.copyWith(color: AppColors.textMuted),
        suffixStyle: AppTextStyles.nano.copyWith(color: AppColors.textMuted),
        contentPadding: const EdgeInsets.symmetric(horizontal: 8, vertical: 8),
        filled: true,
        fillColor: AppColors.surface,
        border: OutlineInputBorder(
            borderRadius: BorderRadius.circular(9),
            borderSide: const BorderSide(color: AppColors.border)),
        enabledBorder: OutlineInputBorder(
            borderRadius: BorderRadius.circular(9),
            borderSide: const BorderSide(color: AppColors.border)),
        focusedBorder: OutlineInputBorder(
            borderRadius: BorderRadius.circular(9),
            borderSide: const BorderSide(color: AppColors.primary)),
      ),
    );
  }

  Widget _label(String text, String info) => Padding(
        padding: const EdgeInsets.only(bottom: 6),
        child: Row(children: [
          Text(text, style: AppTextStyles.nano.copyWith(color: AppColors.textMuted)),
          const SizedBox(width: 4),
          Tooltip(
            message: info,
            triggerMode: TooltipTriggerMode.tap,
            child: Icon(Icons.info_outline, size: 13, color: AppColors.textFaint),
          ),
        ]),
      );

  Widget _field(TextEditingController c, String label, {String? hint}) => Padding(
        padding: const EdgeInsets.only(bottom: 12),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Text(label, style: AppTextStyles.nano.copyWith(color: AppColors.textMuted)),
            const SizedBox(height: 6),
            TextField(
              controller: c,
              style: AppTextStyles.body.copyWith(color: AppColors.textHi),
              decoration: InputDecoration(
                hintText: hint,
                hintStyle: AppTextStyles.nano.copyWith(color: AppColors.textFaint),
                isDense: true,
                contentPadding:
                    const EdgeInsets.symmetric(horizontal: 12, vertical: 10),
                filled: true,
                fillColor: AppColors.surface,
                border: OutlineInputBorder(
                    borderRadius: BorderRadius.circular(10),
                    borderSide: const BorderSide(color: AppColors.border)),
                enabledBorder: OutlineInputBorder(
                    borderRadius: BorderRadius.circular(10),
                    borderSide: const BorderSide(color: AppColors.border)),
                focusedBorder: OutlineInputBorder(
                    borderRadius: BorderRadius.circular(10),
                    borderSide: const BorderSide(color: AppColors.primary)),
              ),
            ),
          ],
        ),
      );
}

// ── Add-coin bar (its own selection state) ──────────────────────────────────────

class _AddCoinBar extends StatefulWidget {
  const _AddCoinBar({required this.remaining, required this.onAdd});
  final List<_CoinMeta> remaining;
  final ValueChanged<String> onAdd;

  @override
  State<_AddCoinBar> createState() => _AddCoinBarState();
}

class _AddCoinBarState extends State<_AddCoinBar> {
  String? _sel;

  @override
  Widget build(BuildContext context) {
    // Keep the selection valid as coins get added/removed.
    if (_sel == null || !widget.remaining.any((c) => c.sym == _sel)) {
      _sel = widget.remaining.first.sym;
    }
    return Row(
      children: [
        Expanded(
          child: Container(
            padding: const EdgeInsets.symmetric(horizontal: 12),
            decoration: BoxDecoration(
              color: AppColors.surface,
              borderRadius: BorderRadius.circular(9),
              border: Border.all(color: AppColors.border),
            ),
            child: DropdownButtonHideUnderline(
              child: DropdownButton<String>(
                isExpanded: true,
                value: _sel,
                dropdownColor: AppColors.panel,
                icon: Icon(Icons.expand_more, color: AppColors.textDim),
                style: AppTextStyles.body.copyWith(color: AppColors.textHi),
                items: [
                  for (final c in widget.remaining)
                    DropdownMenuItem(
                      value: c.sym,
                      child: Text('${c.sym} · ${c.name}',
                          overflow: TextOverflow.ellipsis),
                    ),
                ],
                onChanged: (v) => setState(() => _sel = v),
              ),
            ),
          ),
        ),
        const SizedBox(width: 8),
        GestureDetector(
          onTap: () {
            final s = _sel;
            if (s != null) widget.onAdd(s);
          },
          child: Container(
            padding: const EdgeInsets.symmetric(horizontal: 14, vertical: 10),
            decoration: BoxDecoration(
              color: AppColors.fill(AppColors.primary),
              borderRadius: BorderRadius.circular(9),
              border: Border.all(color: AppColors.stroke(AppColors.primary)),
            ),
            child: Row(mainAxisSize: MainAxisSize.min, children: [
              Icon(symbol('add'), size: 16, color: AppColors.primary),
              const SizedBox(width: 4),
              Text('Add coin',
                  style: AppTextStyles.meta.copyWith(
                      color: AppColors.primary, fontWeight: FontWeight.w600)),
            ]),
          ),
        ),
      ],
    );
  }
}
