import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../../core/theme/app_colors.dart';
import '../../../core/theme/app_text_styles.dart';
import '../../../core/widgets/common_widgets.dart';
import '../../../core/widgets/glass_card.dart';
import '../../../core/widgets/material_symbols.dart';
import '../../dashboard/application/dashboard_controller.dart';
import '../../dashboard/data/dashboard_repository.dart';
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
  bool _killing = false;

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

  Future<void> _kill(String bid) async {
    final ok = await showDialog<bool>(
      context: context,
      builder: (c) => AlertDialog(
        backgroundColor: AppColors.panel,
        shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(16)),
        title: Text('Stop Kalshi instance?', style: AppTextStyles.cardTitle),
        content: Text(
          'This halts the linked instance and cancels all resting orders on this account.',
          style: AppTextStyles.body.copyWith(color: AppColors.textMuted),
        ),
        actions: [
          TextButton(onPressed: () => Navigator.pop(c, false), child: const Text('Cancel')),
          TextButton(
            onPressed: () => Navigator.pop(c, true),
            child: Text('KILL', style: AppTextStyles.body.copyWith(color: AppColors.danger, fontWeight: FontWeight.bold)),
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
    final accounts = _kalshiAccounts();
    _selectedId ??= accounts.isNotEmpty ? accounts.first.id : null;
    final selectedId = _selectedId;

    final instancesAsync = selectedId == null ? null : ref.watch(kalshiInstancesProvider(selectedId));
    final instances = instancesAsync?.value ?? const <KalshiInstance>[];
    final instance = instances.isNotEmpty ? instances.first : null;
    final running = instance?.running ?? false;

    return Scaffold(
      backgroundColor: Colors.transparent,
      appBar: AppBar(
        backgroundColor: Colors.transparent,
        elevation: 0,
        titleSpacing: 16,
        title: Row(
          children: [
            Icon(symbol('sports_soccer'), color: AppColors.primary, size: 22),
            const SizedBox(width: 8),
            Text('Kalshi', style: AppTextStyles.h2),
          ],
        ),
        actions: [
          if (instance != null) ...[
            _StartStopButton(running: running, busy: _killing, onTap: () => _startStop(!running, instance.id)),
            if (running)
              Padding(
                padding: const EdgeInsets.only(left: 8, right: 12),
                child: _KillButton(busy: _killing, onTap: () => _kill(selectedId!)),
              )
            else
              const SizedBox(width: 12),
          ],
        ],
      ),
      body: selectedId == null
          ? Padding(
              padding: const EdgeInsets.all(16),
              child: EmptyState(
                icon: symbol('sports_soccer'),
                title: 'No Kalshi account linked',
                subtitle: 'Link a Kalshi brokerage (demo or live) to create a trading instance.',
              ),
            )
          : RefreshIndicator(
              color: AppColors.primary,
              onRefresh: _refresh,
              child: ListView(
                padding: const EdgeInsets.fromLTRB(16, 4, 16, 24),
                children: [
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
                      onAction: () => _showCreateSheet(selectedId),
                    )
                  else ...[
                    _InstanceStatus(instance: instance),
                    const SizedBox(height: 12),
                    _PortfolioCard(brokerageId: selectedId),
                    const SizedBox(height: 12),
                    _EdgeRadarCard(brokerageId: selectedId),
                    const SizedBox(height: 12),
                    _PositionsCard(brokerageId: selectedId),
                  ],
                ],
              ),
            ),
    );
  }

  Future<void> _startStop(bool start, String instanceId) async {
    if (_killing) return;
    setState(() => _killing = true);
    try {
      final repo = ref.read(kalshiRepositoryProvider);
      start ? await repo.startInstance(instanceId) : await repo.stopInstance(instanceId);
      if (_selectedId != null) ref.invalidate(kalshiInstancesProvider(_selectedId!));
    } finally {
      if (mounted) setState(() => _killing = false);
    }
  }

  void _showCreateSheet(String brokerageId) {
    showModalBottomSheet(
      context: context,
      isScrollControlled: true,
      backgroundColor: Colors.transparent,
      builder: (_) => _CreateInstanceSheet(
        brokerageId: brokerageId,
        onCreated: () {
          if (_selectedId != null) ref.invalidate(kalshiInstancesProvider(_selectedId!));
        },
      ),
    );
  }
}

// ── Shared bits ──────────────────────────────────────────────────────────────

class _KillButton extends StatelessWidget {
  const _KillButton({required this.busy, required this.onTap});
  final bool busy;
  final VoidCallback onTap;

  @override
  Widget build(BuildContext context) {
    return GestureDetector(
      onTap: busy ? null : onTap,
      child: Container(
        padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 8),
        decoration: BoxDecoration(
          color: AppColors.fill(AppColors.danger),
          borderRadius: BorderRadius.circular(10),
          border: Border.all(color: AppColors.stroke(AppColors.danger)),
        ),
        child: Row(
          mainAxisSize: MainAxisSize.min,
          children: [
            Icon(Icons.stop_circle_outlined, size: 16, color: AppColors.danger),
            const SizedBox(width: 6),
            Text(busy ? 'Killing…' : 'Kill',
                style: AppTextStyles.meta.copyWith(color: AppColors.danger, fontWeight: FontWeight.bold)),
          ],
        ),
      ),
    );
  }
}

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
          style: AppTextStyles.body.copyWith(color: AppColors.textMd),
          items: accounts
              .map((a) => DropdownMenuItem(value: a.id, child: Text(a.accountName)))
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

class _PortfolioCard extends ConsumerWidget {
  const _PortfolioCard({required this.brokerageId});
  final String brokerageId;

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final async = ref.watch(kalshiPortfolioProvider(brokerageId));
    return _KCard(
      icon: 'monitoring',
      title: 'Portfolio value',
      child: async.when(
        loading: () => const SizedBox(height: 56, child: LoadingState()),
        error: (e, _) => ErrorBanner(message: '$e', onRetry: () => ref.invalidate(kalshiPortfolioProvider(brokerageId))),
        data: (p) {
          final positive = p.dayChange >= 0;
          final color = positive ? AppColors.success : AppColors.danger;
          return Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Row(
                crossAxisAlignment: CrossAxisAlignment.baseline,
                textBaseline: TextBaseline.alphabetic,
                children: [
                  Text('\$${p.value.toStringAsFixed(2)}',
                      style: AppTextStyles.value.copyWith(fontSize: 26, color: AppColors.textHi)),
                  const SizedBox(width: 8),
                  Icon(positive ? Icons.trending_up : Icons.trending_down, size: 16, color: color),
                  const SizedBox(width: 2),
                  Text('${positive ? '+' : '-'}\$${p.dayChange.abs().toStringAsFixed(2)}',
                      style: AppTextStyles.meta.copyWith(color: color, fontWeight: FontWeight.bold)),
                ],
              ),
              if (p.series.length > 1) ...[
                const SizedBox(height: 14),
                SizedBox(height: 56, child: CustomPaint(painter: _Sparkline(p.series), size: Size.infinite)),
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
            : Column(
                children: positions.map((p) {
                  final u = p.unrealizedCents;
                  final positive = (u ?? 0) >= 0;
                  return _rowItem(
                    '${p.marketTicker}  ${p.side} ×${p.contracts}',
                    Text(
                      u == null ? '—' : '${positive ? '+' : ''}\$${(u / 100).toStringAsFixed(2)}',
                      style: AppTextStyles.value.copyWith(
                        color: u == null ? AppColors.textDim : (positive ? AppColors.success : AppColors.danger),
                        fontSize: 14,
                      ),
                    ),
                  );
                }).toList(),
              ),
      ),
    );
  }
}

class _Sparkline extends CustomPainter {
  _Sparkline(this.data);
  final List<double> data;

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
      i == 0 ? path.moveTo(x, y) : path.lineTo(x, y);
    }
    // Soft violet fill under the line, matching the web chart.
    final fill = Path.from(path)
      ..lineTo(size.width, size.height)
      ..lineTo(0, size.height)
      ..close();
    canvas.drawPath(
      fill,
      Paint()
        ..shader = LinearGradient(
          begin: Alignment.topCenter,
          end: Alignment.bottomCenter,
          colors: [AppColors.primary.withValues(alpha: 0.28), AppColors.primary.withValues(alpha: 0.0)],
        ).createShader(Rect.fromLTWH(0, 0, size.width, size.height)),
    );
    canvas.drawPath(
      path,
      Paint()
        ..color = AppColors.primary
        ..style = PaintingStyle.stroke
        ..strokeWidth = 2.5
        ..strokeJoin = StrokeJoin.round,
    );
  }

  @override
  bool shouldRepaint(covariant _Sparkline old) => old.data != data;
}

// ── Lifecycle bits ───────────────────────────────────────────────────────────

class _StartStopButton extends StatelessWidget {
  const _StartStopButton({required this.running, required this.busy, required this.onTap});
  final bool running;
  final bool busy;
  final VoidCallback onTap;

  @override
  Widget build(BuildContext context) {
    final color = running ? AppColors.warning : AppColors.primary;
    return GestureDetector(
      onTap: busy ? null : onTap,
      child: Container(
        padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 8),
        decoration: BoxDecoration(
          color: AppColors.fill(color),
          borderRadius: BorderRadius.circular(10),
          border: Border.all(color: AppColors.stroke(color)),
        ),
        child: Row(
          mainAxisSize: MainAxisSize.min,
          children: [
            Icon(running ? Icons.pause : Icons.play_arrow, size: 16, color: color),
            const SizedBox(width: 6),
            Text(running ? 'Stop' : 'Start',
                style: AppTextStyles.meta.copyWith(color: color, fontWeight: FontWeight.bold)),
          ],
        ),
      ),
    );
  }
}

class _InstanceStatus extends StatelessWidget {
  const _InstanceStatus({required this.instance});
  final KalshiInstance instance;

  @override
  Widget build(BuildContext context) {
    final running = instance.running;
    return GlassCard(
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

class _CreateInstanceSheet extends ConsumerStatefulWidget {
  const _CreateInstanceSheet({required this.brokerageId, required this.onCreated});
  final String brokerageId;
  final VoidCallback onCreated;

  @override
  ConsumerState<_CreateInstanceSheet> createState() => _CreateInstanceSheetState();
}

class _CreateInstanceSheetState extends ConsumerState<_CreateInstanceSheet> {
  final _name = TextEditingController();
  final _leagues = TextEditingController(text: 'EPL, Serie B, Ligue 2');
  final _edge = TextEditingController(text: '3');
  final _kelly = TextEditingController(text: '0.25');
  final _bankroll = TextEditingController(text: '1000');
  final _maxContracts = TextEditingController(text: '50');
  final _exposure = TextEditingController(text: '60');
  final _leagueCap = TextEditingController(text: '25');
  final _dailyLoss = TextEditingController(text: '400');
  final _poll = TextEditingController(text: '60');
  bool _creating = false;
  String _err = '';

  @override
  void dispose() {
    for (final c in [_name, _leagues, _edge, _kelly, _bankroll, _maxContracts, _exposure, _leagueCap, _dailyLoss, _poll]) {
      c.dispose();
    }
    super.dispose();
  }

  double _d(TextEditingController c, double dflt) => double.tryParse(c.text.trim()) ?? dflt;
  int _i(TextEditingController c, int dflt) => int.tryParse(c.text.trim()) ?? dflt;

  Future<void> _submit() async {
    if (_name.text.trim().isEmpty) { setState(() => _err = 'Name is required'); return; }
    setState(() { _creating = true; _err = ''; });
    try {
      await ref.read(kalshiRepositoryProvider).createInstance(widget.brokerageId, {
        'name': _name.text.trim(),
        'leagues': _leagues.text.split(',').map((s) => s.trim()).where((s) => s.isNotEmpty).toList(),
        'edge_threshold': _d(_edge, 3) / 100,
        'kelly_fraction': _d(_kelly, 0.25),
        'max_contracts_per_market': _i(_maxContracts, 50),
        'max_open_exposure_frac': _d(_exposure, 60) / 100,
        'per_league_cap_frac': _d(_leagueCap, 25) / 100,
        'daily_loss_cap_dollars': _d(_dailyLoss, 400),
        'bankroll_dollars': _d(_bankroll, 1000),
        'poll_seconds': _i(_poll, 60),
      });
      if (mounted) Navigator.pop(context);
      widget.onCreated();
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
        constraints: BoxConstraints(maxHeight: MediaQuery.sizeOf(context).height * 0.85),
        decoration: const BoxDecoration(
          color: AppColors.panel,
          borderRadius: BorderRadius.vertical(top: Radius.circular(20)),
        ),
        child: Column(
          mainAxisSize: MainAxisSize.min,
          children: [
            Padding(
              padding: const EdgeInsets.fromLTRB(20, 16, 20, 8),
              child: Row(
                children: [
                  Icon(symbol('smart_toy'), color: AppColors.primary, size: 20),
                  const SizedBox(width: 8),
                  Text('Create Kalshi instance', style: AppTextStyles.cardTitle),
                  const Spacer(),
                  GestureDetector(onTap: () => Navigator.pop(context), child: Icon(Icons.close, color: AppColors.textDim)),
                ],
              ),
            ),
            Flexible(
              child: ListView(
                padding: const EdgeInsets.fromLTRB(20, 4, 20, 12),
                children: [
                  _field(_name, 'Instance name', hint: 'e.g. Soccer edge — demo'),
                  _field(_leagues, 'Leagues (comma-separated)'),
                  Row(children: [
                    Expanded(child: _field(_edge, 'Edge threshold (%)', number: true)),
                    const SizedBox(width: 12),
                    Expanded(child: _field(_kelly, 'Kelly fraction', number: true)),
                  ]),
                  Row(children: [
                    Expanded(child: _field(_bankroll, 'Bankroll (\$)', number: true)),
                    const SizedBox(width: 12),
                    Expanded(child: _field(_maxContracts, 'Max contracts', number: true)),
                  ]),
                  Row(children: [
                    Expanded(child: _field(_exposure, 'Max exposure (%)', number: true)),
                    const SizedBox(width: 12),
                    Expanded(child: _field(_leagueCap, 'Per-league cap (%)', number: true)),
                  ]),
                  Row(children: [
                    Expanded(child: _field(_dailyLoss, 'Daily-loss cap (\$)', number: true)),
                    const SizedBox(width: 12),
                    Expanded(child: _field(_poll, 'Scan cadence (s)', number: true)),
                  ]),
                  if (_err.isNotEmpty)
                    Padding(padding: const EdgeInsets.only(top: 6),
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
                    backgroundColor: AppColors.primary,
                    foregroundColor: AppColors.onPrimary,
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

  Widget _field(TextEditingController c, String label, {String? hint, bool number = false}) => Padding(
        padding: const EdgeInsets.only(bottom: 12),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Text(label, style: AppTextStyles.nano.copyWith(color: AppColors.textMuted)),
            const SizedBox(height: 6),
            TextField(
              controller: c,
              keyboardType: number ? const TextInputType.numberWithOptions(decimal: true) : TextInputType.text,
              style: AppTextStyles.body.copyWith(color: AppColors.textHi),
              decoration: InputDecoration(
                hintText: hint,
                hintStyle: AppTextStyles.body.copyWith(color: AppColors.textFaint),
                isDense: true,
                contentPadding: const EdgeInsets.symmetric(horizontal: 12, vertical: 10),
                filled: true,
                fillColor: AppColors.surface,
                border: OutlineInputBorder(borderRadius: BorderRadius.circular(10), borderSide: BorderSide(color: AppColors.border)),
                enabledBorder: OutlineInputBorder(borderRadius: BorderRadius.circular(10), borderSide: BorderSide(color: AppColors.border)),
                focusedBorder: OutlineInputBorder(borderRadius: BorderRadius.circular(10), borderSide: const BorderSide(color: AppColors.primary)),
              ),
            ),
          ],
        ),
      );
}
