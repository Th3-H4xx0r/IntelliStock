import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';

import '../../../core/theme/app_colors.dart';
import '../../../core/theme/app_text_styles.dart';
import '../../../core/widgets/common_widgets.dart';
import '../../../core/widgets/glass_card.dart';
import '../../../core/widgets/material_symbols.dart';
import '../../dashboard/application/dashboard_controller.dart';
import '../../dashboard/data/dashboard_repository.dart';
import '../../instances/presentation/live_logs_panel.dart';
import 'kalshi_portfolio_hero.dart';
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

  List<BrokerageAccount> _kalshiAccounts() {
    final accts = ref.watch(brokeragesProvider).value ?? const <BrokerageAccount>[];
    return accts.where((a) => a.brokerageType == 'kalshi').toList();
  }

  Future<void> _refresh() async {
    final id = _selectedId;
    if (id == null) return;
    ref.invalidate(kalshiInstancesProvider(id));
    ref.invalidate(kalshiPortfolioProvider(id));
    ref.invalidate(kalshiEdgesProvider(id));
    ref.invalidate(kalshiPositionsProvider(id));
  }

  @override
  Widget build(BuildContext context) {
    final accounts = _kalshiAccounts();
    // Drop a stale selection (account unlinked elsewhere) before it reaches the
    // DropdownButton — a `value` with no matching item asserts in debug.
    if (_selectedId != null && !accounts.any((a) => a.id == _selectedId)) {
      _selectedId = null;
    }
    _selectedId ??= accounts.isNotEmpty ? accounts.first.id : null;
    final selectedId = _selectedId;

    final instancesAsync = selectedId == null ? null : ref.watch(kalshiInstancesProvider(selectedId));
    final instances = instancesAsync?.value ?? const <KalshiInstance>[];
    final instance = instances.isNotEmpty ? instances.first : null;

    // The shell drops this tab's top safe-area inset so the gradient crown bleeds
    // under the status bar / dynamic island (matches the dashboard) — re-add it as
    // content padding. No AppBar: the header scrolls with the content, so nothing
    // pins over the chart. Start/Stop/KILL live on the instance screen, not here.
    final topInset = MediaQuery.viewPaddingOf(context).top;

    Widget header() => Padding(
          padding: const EdgeInsets.only(bottom: 4),
          child: Row(children: [
            Icon(symbol('sports_soccer'), color: AppColors.primary, size: 24),
            const SizedBox(width: 8),
            Text('Kalshi', style: AppTextStyles.h2),
          ]),
        );

    if (selectedId == null) {
      return Stack(children: [
        Positioned(top: 0, left: 0, right: 0, child: const KalshiCrown()),
        ListView(
          padding: EdgeInsets.fromLTRB(16, topInset + 12, 16, 24),
          children: [
            header(),
            const SizedBox(height: 16),
            EmptyState(
              icon: symbol('sports_soccer'),
              title: 'No Kalshi account linked',
              subtitle: 'Link a Kalshi brokerage (demo or live) to create a trading instance.',
            ),
          ],
        ),
      ]);
    }

    return Stack(children: [
      Positioned(top: 0, left: 0, right: 0, child: const KalshiCrown()),
      RefreshIndicator(
        color: AppColors.primary,
        onRefresh: _refresh,
        child: ListView(
          padding: EdgeInsets.fromLTRB(16, topInset + 12, 16, 24),
          children: [
            header(),
            const SizedBox(height: 16),
            if (accounts.length > 1) ...[
              _AccountSelector(
                accounts: accounts,
                selectedId: selectedId,
                onChanged: (v) => setState(() => _selectedId = v),
              ),
              const SizedBox(height: 12),
            ],
            if (instancesAsync != null && instancesAsync.isLoading)
              const Padding(padding: EdgeInsets.only(top: 40), child: LoadingState())
            else if (instance == null)
              EmptyState(
                icon: symbol('smart_toy'),
                title: 'No trading instance yet',
                subtitle: 'Create a Kalshi instance to scan soccer markets, flag edge, and (when started) trade.',
                actionLabel: 'Create instance',
                onAction: () => _showCreateSheet(accounts, selectedId),
              )
            else ...[
              // All instances for this brokerage — tap any to manage it (start/stop/
              // delete live on its detail screen). Only ONE may run per brokerage
              // (enforced by the backend when you press Start).
              for (final inst in instances) ...[
                _InstanceStatus(instance: inst),
                const SizedBox(height: 8),
              ],
              Align(
                alignment: Alignment.centerLeft,
                child: TextButton.icon(
                  onPressed: () => _showCreateSheet(accounts, selectedId),
                  icon: Icon(symbol('add'), size: 18, color: AppColors.primary),
                  label: Text('New instance',
                      style: AppTextStyles.body.copyWith(color: AppColors.primary, fontWeight: FontWeight.w600)),
                ),
              ),
              const SizedBox(height: 8),
              _PortfolioCard(brokerageId: selectedId),
              const SizedBox(height: 12),
              _EdgeRadarCard(brokerageId: selectedId),
              const SizedBox(height: 12),
              _PositionsCard(brokerageId: selectedId),
              const SizedBox(height: 12),
              _KCard(
                icon: 'terminal',
                title: 'Live logs',
                child: SizedBox(height: 300, child: LiveLogsPanel(key: ValueKey(instance.id), instanceId: instance.id)),
              ),
            ],
          ],
        ),
      ),
    ]);
  }

  void _showCreateSheet(List<BrokerageAccount> accounts, String brokerageId) {
    showModalBottomSheet(
      context: context,
      isScrollControlled: true,
      backgroundColor: Colors.transparent,
      builder: (_) => _CreateInstanceSheet(
        accounts: accounts,
        initialBrokerageId: brokerageId,
        onCreated: (bid) {
          ref.invalidate(kalshiInstancesProvider(bid));
        },
      ),
    );
  }
}

// ── Shared bits ──────────────────────────────────────────────────────────────

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
          // Theme the closed value AND each menu item explicitly — unstyled
          // DropdownMenuItem text falls back to the system grey, which is what
          // made the open menu look native. Matches the create-sheet dropdowns.
          style: AppTextStyles.body.copyWith(color: AppColors.textHi),
          items: accounts
              .map((a) => DropdownMenuItem(
                    value: a.id,
                    child: Text(a.accountName,
                        style: AppTextStyles.body.copyWith(color: AppColors.textHi)),
                  ))
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
      frosted: true,
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

class _PortfolioCard extends ConsumerStatefulWidget {
  const _PortfolioCard({required this.brokerageId});
  final String brokerageId;
  @override
  ConsumerState<_PortfolioCard> createState() => _PortfolioCardState();
}

class _PortfolioCardState extends ConsumerState<_PortfolioCard> {
  final ValueNotifier<int?> _scrub = ValueNotifier<int?>(null);

  @override
  void dispose() {
    _scrub.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    return KalshiPortfolioHero(
      title: 'Portfolio value',
      async: ref.watch(kalshiPortfolioProvider(widget.brokerageId)),
      scrubIdx: _scrub,
      onRetry: () => ref.invalidate(kalshiPortfolioProvider(widget.brokerageId)),
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
            : Column(children: positions.map(kalshiPositionTile).toList()),
      ),
    );
  }
}

// ── Lifecycle bits ───────────────────────────────────────────────────────────

class _InstanceStatus extends StatelessWidget {
  const _InstanceStatus({required this.instance});
  final KalshiInstance instance;

  @override
  Widget build(BuildContext context) {
    final running = instance.running;
    return GlassCard(
      frosted: true,
      onTap: () => context.push('/kalshi/instances/${instance.id}'),
      padding: const EdgeInsets.symmetric(horizontal: 14, vertical: 12),
      child: Row(
        children: [
          Container(width: 8, height: 8, decoration: BoxDecoration(
            color: running ? AppColors.success : AppColors.textFaint, shape: BoxShape.circle)),
          const SizedBox(width: 10),
          Expanded(child: Text(instance.name, style: AppTextStyles.body.copyWith(color: AppColors.textHi, fontWeight: FontWeight.w600), overflow: TextOverflow.ellipsis)),
          _pill(running ? 'Running' : 'Stopped', running ? AppColors.success : AppColors.textDim),
          const SizedBox(width: 6),
          _pill(instance.liveEnabled ? 'Live' : 'Paper', instance.liveEnabled ? AppColors.danger : AppColors.primary),
          const SizedBox(width: 8),
          Icon(Icons.chevron_right, size: 16, color: AppColors.textDim),
        ],
      ),
    );
  }

  Widget _pill(String text, Color color) => Container(
        padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 3),
        decoration: BoxDecoration(color: AppColors.fill(color), borderRadius: BorderRadius.circular(6)),
        child: Text(text, style: AppTextStyles.nano.copyWith(color: color, fontWeight: FontWeight.bold)),
      );
}

const _kLeagues = [
  'World Cup', 'World Cup Qualifiers', 'Champions League', 'Europa League',
  'EPL', 'EFL Championship', 'Serie A', 'Serie B', 'La Liga', 'La Liga 2',
  'Bundesliga', '2. Bundesliga', 'Ligue 1', 'Ligue 2', 'Eredivisie',
  'Primeira Liga', 'MLS', 'Brasileirão',
];

// Risk presets tune every config value; dlpct = daily-loss cap as a fraction of
// the effective bankroll. Mirrors the web KalshiCreateInstanceModal presets.
// Survival-first presets: fractional Kelly + small exposure caps + cash reserve
// (all far below the old kelly 0.25-0.60 / exposure 60-100%). Mirrors the web modal.
const _kRiskPresets = <String, Map<String, dynamic>>{
  'low': {'label': 'Low', 'edge': 5.0, 'kelly': 0.10, 'maxC': 25, 'exp': 10.0, 'lcap': 12.0, 'usage': 40.0, 'poll': 90, 'dlpct': 0.05,
    'blurb': 'Conservative — fewer, higher-confidence trades; small stakes, tight daily-loss cap.'},
  'medium': {'label': 'Medium', 'edge': 4.0, 'kelly': 0.125, 'maxC': 50, 'exp': 15.0, 'lcap': 25.0, 'usage': 50.0, 'poll': 60, 'dlpct': 0.08,
    'blurb': 'Balanced — the default. Fractional-Kelly with a small exposure cap and cash reserve.'},
  'high': {'label': 'High', 'edge': 3.0, 'kelly': 0.15, 'maxC': 75, 'exp': 25.0, 'lcap': 30.0, 'usage': 60.0, 'poll': 45, 'dlpct': 0.10,
    'blurb': 'More active — lower edge bar, slightly bigger Kelly and exposure. More variance.'},
  'max': {'label': 'Max', 'edge': 2.0, 'kelly': 0.20, 'maxC': 100, 'exp': 40.0, 'lcap': 40.0, 'usage': 70.0, 'poll': 30, 'dlpct': 0.15,
    'blurb': 'Aggressive — more +EV spots at larger size. Highest variance, but capped well below the old defaults.'},
};

class _CreateInstanceSheet extends ConsumerStatefulWidget {
  const _CreateInstanceSheet({required this.accounts, required this.initialBrokerageId, required this.onCreated});
  final List<BrokerageAccount> accounts;
  final String initialBrokerageId;
  final ValueChanged<String> onCreated;

  @override
  ConsumerState<_CreateInstanceSheet> createState() => _CreateInstanceSheetState();
}

class _CreateInstanceSheetState extends ConsumerState<_CreateInstanceSheet> {
  late String _brokerageId = widget.initialBrokerageId;
  final _name = TextEditingController();
  final _edge = TextEditingController(text: '4');
  final _kelly = TextEditingController(text: '0.125');
  final _maxContracts = TextEditingController(text: '50');
  final _exposure = TextEditingController(text: '15');
  final _leagueCap = TextEditingController(text: '25');
  final _minPrice = TextEditingController(text: '15');
  final _maxPrice = TextEditingController(text: '90');
  final _drawMinEdge = TextEditingController(text: '10');
  final _orderSizeMin = TextEditingController(text: '0');   // order-size range $/trade (0/0 = auto)
  final _orderSizeMax = TextEditingController(text: '0');
  final _dailyLoss = TextEditingController(text: '100');
  final _poll = TextEditingController(text: '60');
  final _manualBankroll = TextEditingController(text: '1000');
  final _oddsKey = TextEditingController();
  double _sharpWeight = 85;
  final Set<String> _leagues = {'EPL', 'Serie B', 'Ligue 2'};
  double _usagePct = 50;
  double _balance = 0;
  bool _loadingBalance = false;
  bool _dailyLossTouched = false;
  double _dailyLossPct = 0.10;
  String _risk = 'medium';
  bool _liveMonitoring = true;
  // HARD dry-run: read real prices, place NO real orders. Safe default for a funded
  // live account; toggle OFF to place REAL orders.
  bool _paperMode = true;
  List<Map<String, dynamic>> _models = [];
  String? _selectedModel;
  bool _creating = false;
  String _err = '';

  @override
  void initState() {
    super.initState();
    _loadBalance();
    _loadModels();
  }

  Future<void> _loadModels() async {
    try {
      final m = await ref.read(kalshiRepositoryProvider).models();
      if (mounted) setState(() => _models = m);
    } catch (_) {}
  }

  @override
  void dispose() {
    for (final c in [_name, _edge, _kelly, _maxContracts, _exposure, _leagueCap, _dailyLoss, _poll, _manualBankroll, _minPrice, _maxPrice, _drawMinEdge, _orderSizeMin, _orderSizeMax]) {
      c.dispose();
    }
    super.dispose();
  }

  bool get _hasBalance => _balance > 0;
  double get _effectiveBankroll =>
      _hasBalance ? (_balance * _usagePct / 100).roundToDouble() : (double.tryParse(_manualBankroll.text.trim()) ?? 0);

  Future<void> _loadBalance() async {
    if (_brokerageId.isEmpty) return;
    setState(() => _loadingBalance = true);
    try {
      final p = await ref.read(kalshiRepositoryProvider).portfolio(_brokerageId);
      _balance = (p.cash > 0) ? p.cash : p.value;
    } catch (_) {
      _balance = 0;
    } finally {
      if (mounted) {
        setState(() => _loadingBalance = false);
        _scaleDailyLoss();
      }
    }
  }

  void _scaleDailyLoss() {
    if (_dailyLossTouched) return;
    _dailyLoss.text = (_effectiveBankroll * _dailyLossPct).round().clamp(1, 1 << 30).toString();
  }

  String _num(num v) => v == v.roundToDouble() ? v.toInt().toString() : v.toString();

  void _applyPreset(String level) {
    final p = _kRiskPresets[level]!;
    setState(() {
      _risk = level;
      _edge.text = _num(p['edge'] as num);
      _kelly.text = _num(p['kelly'] as num);
      _maxContracts.text = _num(p['maxC'] as num);
      _exposure.text = _num(p['exp'] as num);
      _leagueCap.text = _num(p['lcap'] as num);
      _poll.text = _num(p['poll'] as num);
      _usagePct = (p['usage'] as num).toDouble();
      _dailyLossPct = (p['dlpct'] as num).toDouble();
      _dailyLossTouched = false;
      _scaleDailyLoss();
    });
  }

  double _d(TextEditingController c, double dflt) => double.tryParse(c.text.trim()) ?? dflt;
  int _i(TextEditingController c, int dflt) => int.tryParse(c.text.trim()) ?? dflt;

  Future<void> _submit() async {
    if (_name.text.trim().isEmpty) { setState(() => _err = 'Name is required'); return; }
    if (_leagues.isEmpty) { setState(() => _err = 'Pick at least one league'); return; }
    setState(() { _creating = true; _err = ''; });
    try {
      await ref.read(kalshiRepositoryProvider).createInstance(_brokerageId, {
        'name': _name.text.trim(),
        'leagues': _leagues.toList(),
        'edge_threshold': _d(_edge, 3) / 100,
        'kelly_fraction': _d(_kelly, 0.25),
        'max_contracts_per_market': _i(_maxContracts, 50),
        'max_open_exposure_frac': _d(_exposure, 60) / 100,
        'per_league_cap_frac': _d(_leagueCap, 25) / 100,
        'min_price_cents': _i(_minPrice, 15),
        'max_price_cents': _i(_maxPrice, 90),
        'draw_min_edge': _d(_drawMinEdge, 10) / 100,
        'order_size_min_dollars': _d(_orderSizeMin, 0),
        'order_size_max_dollars': _d(_orderSizeMax, 0),
        'daily_loss_cap_dollars': _d(_dailyLoss, 100),
        'bankroll_dollars': _effectiveBankroll,
        'poll_seconds': _i(_poll, 60),
        'bankroll_usage_pct': _usagePct.round(),
        'live_monitoring': _liveMonitoring,
        'odds_api_key': _oddsKey.text.trim(),
        'sharp_weight': _sharpWeight / 100,
        'tier': _risk,
        'model': _selectedModel,
        'paper_mode': _paperMode,   // dry-run by default; backend forces paper on live brokerages unless off
      });
      if (mounted) Navigator.pop(context);
      widget.onCreated(_brokerageId);
    } catch (e) {
      if (mounted) setState(() => _err = '$e');
    } finally {
      if (mounted) setState(() => _creating = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    final bottom = MediaQuery.viewInsetsOf(context).bottom;
    return Padding(
      padding: EdgeInsets.only(bottom: bottom),
      child: Container(
        constraints: BoxConstraints(maxHeight: MediaQuery.sizeOf(context).height * 0.9),
        decoration: const BoxDecoration(
          color: AppColors.panel,
          borderRadius: BorderRadius.vertical(top: Radius.circular(20)),
        ),
        child: Column(
          mainAxisSize: MainAxisSize.min,
          children: [
            Padding(
              padding: const EdgeInsets.fromLTRB(20, 16, 20, 8),
              child: Row(children: [
                Icon(symbol('smart_toy'), color: AppColors.primary, size: 20),
                const SizedBox(width: 8),
                Text('Create Kalshi instance', style: AppTextStyles.cardTitle),
                const Spacer(),
                GestureDetector(onTap: () => Navigator.pop(context), child: Icon(Icons.close, color: AppColors.textDim)),
              ]),
            ),
            Flexible(
              child: ListView(
                padding: const EdgeInsets.fromLTRB(20, 4, 20, 12),
                children: [
                  // Brokerage selector + balance
                  _label('Kalshi account', 'Which Kalshi account this bot trades on.'),
                  Container(
                    padding: const EdgeInsets.symmetric(horizontal: 12),
                    decoration: BoxDecoration(color: AppColors.surface, borderRadius: BorderRadius.circular(10), border: Border.all(color: AppColors.border)),
                    child: DropdownButtonHideUnderline(
                      child: DropdownButton<String>(
                        isExpanded: true,
                        value: _brokerageId.isEmpty ? null : _brokerageId,
                        dropdownColor: AppColors.panel,
                        icon: Icon(Icons.expand_more, color: AppColors.textDim),
                        style: AppTextStyles.body.copyWith(color: AppColors.textHi),
                        items: widget.accounts.map((a) => DropdownMenuItem(value: a.id, child: Text(a.accountName))).toList(),
                        onChanged: (v) { if (v != null) { setState(() => _brokerageId = v); _loadBalance(); } },
                      ),
                    ),
                  ),
                  Padding(
                    padding: const EdgeInsets.only(top: 6, bottom: 12),
                    child: Text(
                      _loadingBalance ? 'Balance: …' : (_hasBalance ? 'Balance: \$${_balance.toStringAsFixed(2)}' : 'Live balance unavailable'),
                      style: AppTextStyles.nano.copyWith(color: _hasBalance ? AppColors.textMd : AppColors.warning),
                    ),
                  ),

                  // Risk tolerance preset
                  _label('Risk tolerance', "Pick a preset and we'll tune edge, Kelly, exposure, caps, bankroll usage, cadence, and the daily-loss cap. You can still tweak any value after."),
                  Container(
                    padding: const EdgeInsets.all(4),
                    decoration: BoxDecoration(color: AppColors.surface, borderRadius: BorderRadius.circular(10), border: Border.all(color: AppColors.border)),
                    child: Row(
                      children: _kRiskPresets.entries.map((e) {
                        final on = _risk == e.key;
                        return Expanded(
                          child: GestureDetector(
                            onTap: () => _applyPreset(e.key),
                            child: Container(
                              margin: const EdgeInsets.symmetric(horizontal: 2),
                              padding: const EdgeInsets.symmetric(vertical: 8),
                              decoration: BoxDecoration(
                                color: on ? AppColors.primary : Colors.transparent,
                                borderRadius: BorderRadius.circular(7),
                              ),
                              child: Text(e.value['label'] as String, textAlign: TextAlign.center,
                                  style: AppTextStyles.meta.copyWith(
                                      color: on ? AppColors.onPrimary : AppColors.textMuted, fontWeight: FontWeight.bold)),
                            ),
                          ),
                        );
                      }).toList(),
                    ),
                  ),
                  Padding(
                    padding: const EdgeInsets.only(top: 6, bottom: 12),
                    child: Text(_kRiskPresets[_risk]!['blurb'] as String, style: AppTextStyles.nano.copyWith(color: AppColors.textDim)),
                  ),

                  // Analyst LLM model
                  _label('Analyst LLM model', 'The model that reads injuries/lineups/form and adjusts probabilities + writes the per-bet rationale.'),
                  Container(
                    padding: const EdgeInsets.symmetric(horizontal: 12),
                    decoration: BoxDecoration(color: AppColors.surface, borderRadius: BorderRadius.circular(10), border: Border.all(color: AppColors.border)),
                    child: DropdownButtonHideUnderline(
                      child: DropdownButton<String?>(
                        isExpanded: true,
                        value: _selectedModel,
                        dropdownColor: AppColors.panel,
                        icon: Icon(Icons.expand_more, color: AppColors.textDim),
                        style: AppTextStyles.body.copyWith(color: AppColors.textHi),
                        items: [
                          const DropdownMenuItem<String?>(value: null, child: Text('Default (system model)')),
                          ..._models.map((m) => DropdownMenuItem<String?>(
                                value: m['id'] as String,
                                child: Text((m['name'] ?? m['model'] ?? m['id']).toString(), overflow: TextOverflow.ellipsis),
                              )),
                        ],
                        onChanged: (v) => setState(() => _selectedModel = v),
                      ),
                    ),
                  ),
                  const SizedBox(height: 12),

                  // Live in-match trading toggle
                  Container(
                    decoration: BoxDecoration(color: AppColors.surface, borderRadius: BorderRadius.circular(10), border: Border.all(color: AppColors.border)),
                    child: SwitchListTile(
                      contentPadding: const EdgeInsets.symmetric(horizontal: 12),
                      activeColor: AppColors.primary,
                      value: _liveMonitoring,
                      onChanged: (v) => setState(() => _liveMonitoring = v),
                      title: Text('Live in-match trading', style: AppTextStyles.body.copyWith(color: AppColors.textHi)),
                      subtitle: Text('Monitor live matches and trade two-way (open/add/reduce/exit) in-play.', style: AppTextStyles.nano.copyWith(color: AppColors.textDim)),
                    ),
                  ),
                  const SizedBox(height: 12),

                  // Paper mode (dry-run) toggle — safe for funded live accounts
                  Container(
                    decoration: BoxDecoration(color: _paperMode ? AppColors.surface : AppColors.danger.withValues(alpha: 0.1), borderRadius: BorderRadius.circular(10), border: Border.all(color: _paperMode ? AppColors.border : AppColors.danger.withValues(alpha: 0.4))),
                    child: SwitchListTile(
                      contentPadding: const EdgeInsets.symmetric(horizontal: 12),
                      activeColor: AppColors.primary,
                      value: _paperMode,
                      onChanged: (v) => setState(() => _paperMode = v),
                      title: Text(_paperMode ? 'Paper mode (dry-run)' : 'REAL orders', style: AppTextStyles.body.copyWith(color: _paperMode ? AppColors.textHi : AppColors.danger, fontWeight: FontWeight.w600)),
                      subtitle: Text(_paperMode ? 'Reads real prices, places NO real orders — safe to test a funded account.' : '⚠ Places REAL orders with REAL money when started.', style: AppTextStyles.nano.copyWith(color: AppColors.textDim)),
                    ),
                  ),
                  const SizedBox(height: 12),

                  // Sharp-odds anchor
                  _label('Odds API key — sharp anchor',
                      'Anchor fair value to de-vig\'d sharp bookmaker odds (the-odds-api.com) so it bets where Kalshi disagrees with the books. Free key. Blank = model-only (rarely trades).'),
                  _field(_oddsKey, 'Odds API key', hint: 'the-odds-api.com key (optional)', obscure: true),
                  Padding(
                    padding: const EdgeInsets.only(top: 4, bottom: 12),
                    child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
                      Text('Sharp weight: ${_sharpWeight.round()}%', style: AppTextStyles.nano.copyWith(color: AppColors.textDim)),
                      Slider(
                        value: _sharpWeight, min: 0, max: 100, divisions: 20,
                        activeColor: AppColors.primary,
                        label: '${_sharpWeight.round()}%',
                        onChanged: (v) => setState(() => _sharpWeight = v),
                      ),
                    ]),
                  ),

                  _field(_name, 'Instance name', hint: 'e.g. Soccer edge — demo'),

                  // Leagues multi-select chips
                  _label('Leagues', 'Which soccer leagues to scan. Thinner divisions often carry more edge.'),
                  Padding(
                    padding: const EdgeInsets.only(bottom: 12),
                    child: Wrap(
                      spacing: 8, runSpacing: 8,
                      children: _kLeagues.map((l) {
                        final on = _leagues.contains(l);
                        return GestureDetector(
                          onTap: () => setState(() => on ? _leagues.remove(l) : _leagues.add(l)),
                          child: Container(
                            padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 7),
                            decoration: BoxDecoration(
                              color: on ? AppColors.fill(AppColors.primary) : AppColors.surface,
                              borderRadius: BorderRadius.circular(20),
                              border: Border.all(color: on ? AppColors.stroke(AppColors.primary) : AppColors.border),
                            ),
                            child: Text(l, style: AppTextStyles.meta.copyWith(
                              color: on ? AppColors.primary : AppColors.textMuted,
                              fontWeight: on ? FontWeight.bold : FontWeight.normal)),
                          ),
                        );
                      }).toList(),
                    ),
                  ),

                  // Bankroll usage
                  _label('Bankroll usage', 'How much of this account the bot sizes against. The daily-loss cap scales with it.'),
                  if (_hasBalance) ...[
                    Row(children: [
                      Expanded(child: Slider(
                        value: _usagePct, min: 5, max: 100, divisions: 19,
                        activeColor: AppColors.primary, label: '${_usagePct.round()}%',
                        onChanged: (v) => setState(() { _usagePct = v; _scaleDailyLoss(); }),
                      )),
                      Text('${_usagePct.round()}% · \$${_effectiveBankroll.round()}',
                          style: AppTextStyles.meta.copyWith(color: AppColors.textMd, fontWeight: FontWeight.bold)),
                    ]),
                    const SizedBox(height: 12),
                  ] else
                    Padding(padding: const EdgeInsets.only(bottom: 12), child: _field(_manualBankroll, 'Bankroll (\$)', number: true)),

                  Row(children: [
                    Expanded(child: _field(_edge, 'Edge threshold (%)', number: true)),
                    const SizedBox(width: 12),
                    Expanded(child: _field(_kelly, 'Kelly fraction', number: true)),
                  ]),
                  Row(children: [
                    Expanded(child: _field(_maxContracts, 'Max contracts', number: true)),
                    const SizedBox(width: 12),
                    Expanded(child: _field(_exposure, 'Max exposure (%)', number: true)),
                  ]),
                  Row(children: [
                    Expanded(child: _field(_leagueCap, 'Per-league cap (%)', number: true)),
                    const SizedBox(width: 12),
                    Expanded(child: _field(_poll, 'Scan cadence (s)', number: true)),
                  ]),
                  Row(children: [
                    Expanded(child: _field(_minPrice, 'Min price (¢)', number: true)),
                    const SizedBox(width: 12),
                    Expanded(child: _field(_maxPrice, 'Max price (¢)', number: true)),
                    const SizedBox(width: 12),
                    Expanded(child: _field(_drawMinEdge, 'Draw min edge (%)', number: true)),
                  ]),
                  Row(children: [
                    Expanded(child: _field(_orderSizeMin, 'Order size min (\$)', number: true)),
                    const SizedBox(width: 12),
                    Expanded(child: _field(_orderSizeMax, 'Order size max (\$)', number: true)),
                  ]),
                  TextField(
                    controller: _dailyLoss,
                    onChanged: (_) => _dailyLossTouched = true,
                    keyboardType: const TextInputType.numberWithOptions(decimal: true),
                    style: AppTextStyles.body.copyWith(color: AppColors.textHi),
                    decoration: _dec('Daily-loss cap (\$) — auto-scales with bankroll'),
                  ),
                  if (_err.isNotEmpty)
                    Padding(padding: const EdgeInsets.only(top: 10),
                        child: Text(_err, style: AppTextStyles.meta.copyWith(color: AppColors.danger))),
                ],
              ),
            ),
            Padding(
              padding: EdgeInsets.fromLTRB(20, 8, 20, 16 + MediaQuery.viewPaddingOf(context).bottom),
              child: SizedBox(
                width: double.infinity,
                child: ElevatedButton(
                  onPressed: _creating ? null : _submit,
                  style: ElevatedButton.styleFrom(
                    backgroundColor: AppColors.primary, foregroundColor: AppColors.onPrimary,
                    padding: const EdgeInsets.symmetric(vertical: 14),
                    shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(12)),
                  ),
                  child: Text(_creating ? 'Creating…' : 'Create instance',
                      style: AppTextStyles.body.copyWith(fontWeight: FontWeight.bold, color: AppColors.onPrimary)),
                ),
              ),
            ),
          ],
        ),
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

  InputDecoration _dec(String? hint) => InputDecoration(
        hintText: hint,
        hintStyle: AppTextStyles.nano.copyWith(color: AppColors.textFaint),
        isDense: true,
        contentPadding: const EdgeInsets.symmetric(horizontal: 12, vertical: 10),
        filled: true,
        fillColor: AppColors.surface,
        border: OutlineInputBorder(borderRadius: BorderRadius.circular(10), borderSide: BorderSide(color: AppColors.border)),
        enabledBorder: OutlineInputBorder(borderRadius: BorderRadius.circular(10), borderSide: BorderSide(color: AppColors.border)),
        focusedBorder: OutlineInputBorder(borderRadius: BorderRadius.circular(10), borderSide: const BorderSide(color: AppColors.primary)),
      );

  Widget _field(TextEditingController c, String label, {String? hint, bool number = false, bool obscure = false}) => Padding(
        padding: const EdgeInsets.only(bottom: 12),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Text(label, style: AppTextStyles.nano.copyWith(color: AppColors.textMuted)),
            const SizedBox(height: 6),
            TextField(
              controller: c,
              obscureText: obscure,
              keyboardType: number ? const TextInputType.numberWithOptions(decimal: true) : TextInputType.text,
              style: AppTextStyles.body.copyWith(color: AppColors.textHi),
              decoration: _dec(hint),
            ),
          ],
        ),
      );
}
