import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';

import '../../../core/theme/app_colors.dart';
import '../../../core/theme/app_text_styles.dart';
import '../../../core/widgets/app_background.dart';
import '../../../core/widgets/app_button.dart';
import '../../../core/widgets/common_widgets.dart';
import '../../../core/widgets/confirm_dialog.dart';
import '../../../core/widgets/glass_card.dart';
import '../../../core/widgets/material_symbols.dart';
import '../../../core/widgets/status_pill.dart';
import '../../instances/data/models/instance.dart';
import '../data/crypto_repository.dart';
import 'crypto_instance_sheet.dart';

/// Full-screen pushed route for /crypto. Lists the crypto (kind='crypto', 24/7)
/// instances, each with start/stop/edit, and a button to create a new one.
/// Matches the standalone-screen convention (Scaffold + AppBackground + SafeArea).
class CryptoScreen extends ConsumerWidget {
  const CryptoScreen({super.key});

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final async = ref.watch(cryptoInstancesProvider);

    void refresh() => ref.invalidate(cryptoInstancesProvider);

    void openSheet({Instance? edit}) {
      showCryptoInstanceSheet(
        context,
        editInstanceId: edit?.id,
        editName: edit?.name,
        editBrokerageId: edit?.brokerageId,
        editConfig: edit?.cryptoConfig,
        editStocks: edit?.stocks,
        onSaved: refresh,
      );
    }

    return Scaffold(
      backgroundColor: AppColors.canvas,
      body: AppBackground(
        child: SafeArea(
          child: Column(
            children: [
              // App bar row
              Padding(
                padding:
                    const EdgeInsets.symmetric(horizontal: 20, vertical: 12),
                child: Row(
                  children: [
                    IconButton(
                      icon: const Icon(Icons.arrow_back, size: 22),
                      color: AppColors.textMuted,
                      onPressed: () => Navigator.of(context).pop(),
                    ),
                    const Spacer(),
                    IconButton(
                      icon: const Icon(Icons.refresh, size: 22),
                      color: AppColors.textMuted,
                      onPressed: refresh,
                    ),
                  ],
                ),
              ),
              Expanded(
                child: RefreshIndicator(
                  onRefresh: () async => refresh(),
                  color: AppColors.primary,
                  backgroundColor: AppColors.surface,
                  child: ListView(
                    padding: const EdgeInsets.fromLTRB(20, 0, 20, 40),
                    children: [
                      Row(
                        crossAxisAlignment: CrossAxisAlignment.start,
                        children: [
                          Expanded(
                            child: SectionHeader(
                              eyebrow: 'Trading',
                              title: 'Crypto',
                              subtitle:
                                  '24/7 bots — pin fixed coin weights, auto-discover the rest.',
                            ),
                          ),
                          const SizedBox(width: 12),
                          AppButton.primary(
                            label: 'New',
                            icon: symbol('add'),
                            dense: true,
                            onPressed: () => openSheet(),
                          ),
                        ],
                      ),
                      const SizedBox(height: 20),
                      async.when(
                        loading: () => const Padding(
                          padding: EdgeInsets.only(top: 40),
                          child: LoadingState(),
                        ),
                        error: (e, _) => ErrorBanner(
                          message: e.toString(),
                          onRetry: refresh,
                        ),
                        data: (instances) => instances.isEmpty
                            ? EmptyState(
                                icon: symbol('currency_bitcoin'),
                                title: 'No crypto instances yet',
                                subtitle:
                                    'Create a 24/7 crypto bot with a fixed + dynamic coin allocation.',
                                actionLabel: 'New crypto instance',
                                onAction: () => openSheet(),
                              )
                            : Column(
                                children: [
                                  for (final inst in instances) ...[
                                    _CryptoCard(
                                        inst: inst, onEdit: () => openSheet(edit: inst)),
                                    const SizedBox(height: 12),
                                  ],
                                ],
                              ),
                      ),
                    ],
                  ),
                ),
              ),
            ],
          ),
        ),
      ),
    );
  }
}

// ── Instance card ─────────────────────────────────────────────────────────────

class _CryptoCard extends ConsumerStatefulWidget {
  const _CryptoCard({required this.inst, required this.onEdit});
  final Instance inst;
  final VoidCallback onEdit;

  @override
  ConsumerState<_CryptoCard> createState() => _CryptoCardState();
}

class _CryptoCardState extends ConsumerState<_CryptoCard> {
  bool _busy = false;

  Future<void> _run(Future<void> Function(CryptoRepository repo) action) async {
    setState(() => _busy = true);
    try {
      await action(ref.read(cryptoRepositoryProvider));
    } catch (_) {
      // Swallow — the refreshed list reflects the true state.
    } finally {
      if (mounted) setState(() => _busy = false);
      ref.invalidate(cryptoInstancesProvider);
    }
  }

  void _openBacktest(Instance inst) {
    showModalBottomSheet<void>(
      context: context,
      isScrollControlled: true,
      backgroundColor: Colors.transparent,
      builder: (_) => _CryptoBacktestSheet(inst: inst),
    );
  }

  Future<void> _confirmDelete() async {
    final inst = widget.inst;
    await showConfirmDialog(
      context,
      title: 'Delete instance',
      body:
          'Delete "${inst.name.isNotEmpty ? inst.name : inst.id}"? This cannot be undone.',
      confirmLabel: 'Delete',
      confirmColor: AppColors.danger,
      icon: symbol('delete'),
      onConfirm: () => _run((repo) => repo.deleteInstance(inst.id, force: true)),
    );
  }

  @override
  Widget build(BuildContext context) {
    final inst = widget.inst;
    final running = inst.runCommand;
    final crashed = inst.crashed;
    final fixed = inst.stocks;

    return GlassCard(
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
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
                        style: AppTextStyles.cardTitle,
                        overflow: TextOverflow.ellipsis),
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
                    label: crashed
                        ? 'Crashed'
                        : (running ? 'Running' : 'Stopped'),
                    color: crashed
                        ? AppColors.danger
                        : (running ? AppColors.success : AppColors.textDim),
                    pulsing: running && !crashed,
                  ),
                ],
              ),
            ],
          ),
          const SizedBox(height: 12),
          // Fixed coins summary
          if (fixed.isEmpty)
            Text('100% dynamic — fully auto-discovered.',
                style: AppTextStyles.meta.copyWith(color: AppColors.textDim))
          else
            Wrap(
              spacing: 6,
              runSpacing: 6,
              children: [
                for (final s in fixed)
                  Container(
                    padding:
                        const EdgeInsets.symmetric(horizontal: 8, vertical: 3),
                    decoration: BoxDecoration(
                      color: AppColors.surface,
                      borderRadius: BorderRadius.circular(6),
                      border: Border.all(color: AppColors.border),
                    ),
                    child: Text(s,
                        style:
                            AppTextStyles.mono(11, color: AppColors.textMd)),
                  ),
              ],
            ),
          const SizedBox(height: 12),
          Wrap(
            spacing: 6,
            runSpacing: 6,
            children: [
              _action(
                icon: symbol('edit'),
                label: 'Edit',
                color: AppColors.primary,
                onTap: _busy ? null : widget.onEdit,
              ),
              _action(
                icon: symbol('analytics'),
                label: 'Backtest',
                color: AppColors.info,
                onTap: _busy ? null : () => _openBacktest(inst),
              ),
              if (running)
                _action(
                  icon: _busy ? symbol('progress_activity') : symbol('stop'),
                  label: 'Stop',
                  color: AppColors.warning,
                  onTap: _busy
                      ? null
                      : () => _run((repo) => repo.stopInstance(inst.id)),
                )
              else
                _action(
                  icon: _busy
                      ? symbol('progress_activity')
                      : symbol('play_arrow'),
                  label: 'Start',
                  color: AppColors.success,
                  onTap: _busy
                      ? null
                      : () => _run((repo) => repo.startInstance(inst.id)),
                ),
              _action(
                icon: symbol('delete'),
                label: 'Delete',
                color: AppColors.danger,
                onTap: _busy ? null : _confirmDelete,
              ),
            ],
          ),
        ],
      ),
    );
  }

  Widget _action({
    required IconData icon,
    required String label,
    required Color color,
    VoidCallback? onTap,
  }) {
    final disabled = onTap == null;
    return Opacity(
      opacity: disabled ? 0.4 : 1,
      child: GestureDetector(
        onTap: onTap,
        child: Container(
          padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 6),
          decoration: BoxDecoration(
            color: AppColors.fill(color),
            borderRadius: BorderRadius.circular(8),
            border: Border.all(color: AppColors.stroke(color)),
          ),
          child: Row(
            mainAxisSize: MainAxisSize.min,
            children: [
              Icon(icon, size: 13, color: color),
              const SizedBox(width: 4),
              Text(label,
                  style: AppTextStyles.nano
                      .copyWith(color: color, fontWeight: FontWeight.w600)),
            ],
          ),
        ),
      ),
    );
  }
}

// ── Backtest sheet ──────────────────────────────────────────────────────────────

const _kCryptoGrans = <(String, String)>[
  ('300', '5 min'),
  ('900', '15 min'),
  ('3600', '1 hour'),
  ('86400', '1 day'),
];
const _kBandGran = <String, String>{'high': '300', 'medium': '900', 'low': '3600'};

/// Configure + launch a backtest of a crypto instance's own configured
/// allocation. Sends the instance's slash-pairs to POST /backtests (empty ⇒
/// pure auto-discovery) and opens the generic result view on success.
class _CryptoBacktestSheet extends ConsumerStatefulWidget {
  const _CryptoBacktestSheet({required this.inst});
  final Instance inst;

  @override
  ConsumerState<_CryptoBacktestSheet> createState() =>
      _CryptoBacktestSheetState();
}

class _CryptoBacktestSheetState extends ConsumerState<_CryptoBacktestSheet> {
  late DateTime _start;
  late DateTime _end;
  late String _gran;
  final _cashCtrl = TextEditingController(text: '10000');
  bool _busy = false;
  String? _err;

  @override
  void initState() {
    super.initState();
    _end = DateTime.now();
    _start = _end.subtract(const Duration(days: 90));
    final band =
        (widget.inst.cryptoConfig?['band'] ?? '').toString().toLowerCase();
    _gran = _kBandGran[band] ?? '900';
  }

  @override
  void dispose() {
    _cashCtrl.dispose();
    super.dispose();
  }

  String _fmtDate(DateTime d) =>
      '${d.year.toString().padLeft(4, '0')}-${d.month.toString().padLeft(2, '0')}-${d.day.toString().padLeft(2, '0')}';

  Future<void> _pickDate({required bool isStart}) async {
    final picked = await showDatePicker(
      context: context,
      initialDate: isStart ? _start : _end,
      firstDate: DateTime(2018),
      lastDate: DateTime.now(),
    );
    if (picked != null) {
      setState(() => isStart ? _start = picked : _end = picked);
    }
  }

  Future<void> _submit() async {
    if (!_start.isBefore(_end)) {
      setState(() => _err = 'End date must be after start date');
      return;
    }
    setState(() {
      _busy = true;
      _err = null;
    });
    try {
      final res = await ref.read(cryptoRepositoryProvider).createBacktest(
            instanceId: widget.inst.id,
            stocks: widget.inst.stocks,
            startDate: _fmtDate(_start),
            endDate: _fmtDate(_end),
            granularity: _gran,
            initialCash: double.tryParse(_cashCtrl.text) ?? 10000,
          );
      final id = (res['id'] ?? res['backtest_id'])?.toString();
      if (!mounted) return;
      // Capture the router BEFORE popping — after pop this sheet's context is
      // unmounted and context.push would throw.
      final router = GoRouter.of(context);
      Navigator.pop(context);
      router.push(
          id != null && id.isNotEmpty ? '/backtests/$id' : '/backtests');
    } catch (e) {
      if (mounted) {
        setState(() {
          _err = e.toString();
          _busy = false;
        });
      }
    }
  }

  @override
  Widget build(BuildContext context) {
    final bottom = MediaQuery.viewInsetsOf(context).bottom;
    final tickers = [
      for (final s in widget.inst.stocks) s.split('/').first,
    ];
    return Padding(
      padding: EdgeInsets.only(bottom: bottom),
      child: Container(
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
                Icon(symbol('analytics'), color: AppColors.info, size: 20),
                const SizedBox(width: 8),
                Text('Backtest crypto instance',
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
                padding: const EdgeInsets.fromLTRB(20, 4, 20, 16),
                shrinkWrap: true,
                children: [
                  Text(
                    'Simulate ${widget.inst.name.isNotEmpty ? widget.inst.name : widget.inst.id}\'s '
                    'current allocation over a historical window. Crypto fills '
                    'include the taker fee.',
                    style: AppTextStyles.nano.copyWith(color: AppColors.textDim),
                  ),
                  const SizedBox(height: 14),
                  Text('ALLOCATION UNDER TEST', style: AppTextStyles.eyebrow),
                  const SizedBox(height: 8),
                  if (tickers.isEmpty)
                    Text('100% dynamic — the backtest auto-discovers its universe.',
                        style: AppTextStyles.nano
                            .copyWith(color: AppColors.textDim))
                  else
                    Wrap(
                      spacing: 6,
                      runSpacing: 6,
                      children: [
                        for (final t in tickers)
                          Container(
                            padding: const EdgeInsets.symmetric(
                                horizontal: 8, vertical: 3),
                            decoration: BoxDecoration(
                              color: AppColors.surface,
                              borderRadius: BorderRadius.circular(6),
                              border: Border.all(color: AppColors.border),
                            ),
                            child: Text(t,
                                style: AppTextStyles.mono(11,
                                    color: AppColors.textMd)),
                          ),
                      ],
                    ),
                  const SizedBox(height: 16),
                  Row(
                    children: [
                      Expanded(
                          child: _dateField('Start', _start, () => _pickDate(isStart: true))),
                      const SizedBox(width: 10),
                      Expanded(
                          child: _dateField('End', _end, () => _pickDate(isStart: false))),
                    ],
                  ),
                  const SizedBox(height: 14),
                  Text('Granularity', style: AppTextStyles.micro),
                  const SizedBox(height: 6),
                  Wrap(
                    spacing: 6,
                    runSpacing: 6,
                    children: [
                      for (final g in _kCryptoGrans)
                        GestureDetector(
                          onTap: () => setState(() => _gran = g.$1),
                          child: Container(
                            padding: const EdgeInsets.symmetric(
                                horizontal: 12, vertical: 6),
                            decoration: BoxDecoration(
                              color: _gran == g.$1
                                  ? AppColors.fill(AppColors.primary)
                                  : Colors.transparent,
                              borderRadius: BorderRadius.circular(7),
                              border: Border.all(
                                color: _gran == g.$1
                                    ? AppColors.stroke(AppColors.primary)
                                    : AppColors.border,
                              ),
                            ),
                            child: Text(g.$2,
                                style: AppTextStyles.meta.copyWith(
                                  color: _gran == g.$1
                                      ? AppColors.primary
                                      : AppColors.textMuted,
                                  fontWeight: FontWeight.w600,
                                )),
                          ),
                        ),
                    ],
                  ),
                  const SizedBox(height: 14),
                  Text('Initial cash (\$)', style: AppTextStyles.micro),
                  const SizedBox(height: 6),
                  TextField(
                    controller: _cashCtrl,
                    keyboardType: TextInputType.number,
                    style: AppTextStyles.body.copyWith(color: AppColors.textHi),
                    decoration: InputDecoration(
                      isDense: true,
                      prefixText: '\$ ',
                      prefixStyle:
                          AppTextStyles.nano.copyWith(color: AppColors.textMuted),
                      contentPadding: const EdgeInsets.symmetric(
                          horizontal: 12, vertical: 10),
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
                  if (_err != null) ...[
                    const SizedBox(height: 12),
                    Text(_err!,
                        style: AppTextStyles.meta
                            .copyWith(color: AppColors.danger)),
                  ],
                ],
              ),
            ),
            Padding(
              padding: EdgeInsets.fromLTRB(
                  20, 8, 20, 16 + MediaQuery.viewPaddingOf(context).bottom),
              child: SizedBox(
                width: double.infinity,
                child: ElevatedButton(
                  onPressed: _busy ? null : _submit,
                  style: ElevatedButton.styleFrom(
                    backgroundColor: AppColors.primary,
                    foregroundColor: AppColors.onPrimary,
                    padding: const EdgeInsets.symmetric(vertical: 14),
                    shape: RoundedRectangleBorder(
                        borderRadius: BorderRadius.circular(12)),
                  ),
                  child: Text(_busy ? 'Queuing…' : 'Run backtest',
                      style: AppTextStyles.body.copyWith(
                          fontWeight: FontWeight.bold,
                          color: AppColors.onPrimary)),
                ),
              ),
            ),
          ],
        ),
      ),
    );
  }

  Widget _dateField(String label, DateTime value, VoidCallback onTap) {
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Text(label, style: AppTextStyles.micro),
        const SizedBox(height: 6),
        GestureDetector(
          onTap: onTap,
          child: Container(
            padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 11),
            decoration: BoxDecoration(
              color: AppColors.surface,
              borderRadius: BorderRadius.circular(10),
              border: Border.all(color: AppColors.border),
            ),
            child: Row(
              children: [
                Icon(Icons.calendar_today,
                    size: 14, color: AppColors.textDim),
                const SizedBox(width: 8),
                Text(_fmtDate(value),
                    style:
                        AppTextStyles.body.copyWith(color: AppColors.textHi)),
              ],
            ),
          ),
        ),
      ],
    );
  }
}
