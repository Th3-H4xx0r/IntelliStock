import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';

import '../../../core/theme/app_colors.dart';
import '../../../core/theme/app_text_styles.dart';
import '../../../core/widgets/material_symbols.dart';
import '../../instances/data/models/instance.dart';
import '../data/crypto_repository.dart';

// Bar cadence is DERIVED from the instance's Band (set in create/edit) — not a
// separate control. High/Medium/Low → 5/15/60-minute bars.
const _kBandGran = <String, String>{'high': '300', 'medium': '900', 'low': '3600'};
const _kBandCadence = <String, String>{
  'high': 'High · ~5-minute bars',
  'medium': 'Medium · ~15-minute bars',
  'low': 'Low · ~60-minute bars',
};

// Emulate fees: value the strategy at a chosen venue's taker fee instead of the
// instance's own brokerage. 'default' = use the instance's brokerage.
const _kFeeVenues = <(String, String)>[
  ('default', "Default — instance's brokerage"),
  ('binanceus', 'Binance.US — 0.02% taker'),
  ('alpaca', 'Alpaca — 0.25% taker'),
  ('kraken', 'Kraken — 0.26% taker'),
  ('coinbase', 'Coinbase Advanced — 0.60% taker'),
];

/// Opens the crypto backtest sheet. When [onCreated] is provided the sheet calls
/// it after queuing (so a detail screen can refresh in place); otherwise it
/// navigates to the generic backtest result view.
Future<void> showCryptoBacktestSheet(
  BuildContext context, {
  required Instance inst,
  VoidCallback? onCreated,
}) {
  return showModalBottomSheet<void>(
    context: context,
    isScrollControlled: true,
    backgroundColor: Colors.transparent,
    builder: (_) => CryptoBacktestSheet(inst: inst, onCreated: onCreated),
  );
}

/// Configure + launch a backtest of a crypto instance's own configured
/// allocation. Sends the instance's slash-pairs to POST /backtests (empty ⇒
/// pure auto-discovery).
class CryptoBacktestSheet extends ConsumerStatefulWidget {
  const CryptoBacktestSheet({super.key, required this.inst, this.onCreated});
  final Instance inst;
  final VoidCallback? onCreated;

  @override
  ConsumerState<CryptoBacktestSheet> createState() =>
      _CryptoBacktestSheetState();
}

class _CryptoBacktestSheetState extends ConsumerState<CryptoBacktestSheet> {
  late DateTime _start;
  late DateTime _end;
  late String _gran;
  String _feeVenue = 'default';
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

  String get _cadenceLabel {
    final band =
        (widget.inst.cryptoConfig?['band'] ?? 'medium').toString().toLowerCase();
    return _kBandCadence[band] ?? _kBandCadence['medium']!;
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
            emulateFeeVenue: _feeVenue,
          );
      final id = (res['id'] ?? res['backtest_id'])?.toString();
      if (!mounted) return;
      // Capture the router BEFORE popping — after pop this sheet's context is
      // unmounted and context.push would throw.
      final router = GoRouter.of(context);
      Navigator.pop(context);
      if (widget.onCreated != null) {
        widget.onCreated!();
      } else {
        router.push(
            id != null && id.isNotEmpty ? '/backtests/$id' : '/backtests');
      }
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
                  Text('Cadence', style: AppTextStyles.micro),
                  const SizedBox(height: 6),
                  Container(
                    padding: const EdgeInsets.symmetric(
                        horizontal: 12, vertical: 11),
                    decoration: BoxDecoration(
                      color: AppColors.surface,
                      borderRadius: BorderRadius.circular(10),
                      border: Border.all(color: AppColors.border),
                    ),
                    child: Row(
                      children: [
                        Icon(Icons.schedule, size: 15, color: AppColors.textDim),
                        const SizedBox(width: 8),
                        Expanded(
                          child: Text(_cadenceLabel,
                              style: AppTextStyles.body
                                  .copyWith(color: AppColors.textHi)),
                        ),
                        Text('from Band',
                            style: AppTextStyles.nano
                                .copyWith(color: AppColors.textDim)),
                      ],
                    ),
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
                  const SizedBox(height: 14),
                  Text('Emulate fees', style: AppTextStyles.micro),
                  const SizedBox(height: 6),
                  Container(
                    padding: const EdgeInsets.symmetric(horizontal: 12),
                    decoration: BoxDecoration(
                      color: AppColors.surface,
                      borderRadius: BorderRadius.circular(10),
                      border: Border.all(color: AppColors.border),
                    ),
                    child: DropdownButtonHideUnderline(
                      child: DropdownButton<String>(
                        value: _feeVenue,
                        isExpanded: true,
                        dropdownColor: AppColors.panel,
                        style: AppTextStyles.body.copyWith(color: AppColors.textHi),
                        iconEnabledColor: AppColors.textMuted,
                        onChanged: (v) => setState(() => _feeVenue = v ?? 'default'),
                        items: [
                          for (final v in _kFeeVenues)
                            DropdownMenuItem(value: v.$1, child: Text(v.$2)),
                        ],
                      ),
                    ),
                  ),
                  const SizedBox(height: 4),
                  Text(
                    _feeVenue == 'default'
                        ? "Uses this instance's own brokerage fee."
                        : 'Emulated — fills charged at the selected venue\'s fee. Compare a strategy across venues (fees make or break high-frequency crypto).',
                    style: AppTextStyles.nano.copyWith(color: AppColors.textDim),
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
                Icon(Icons.calendar_today, size: 14, color: AppColors.textDim),
                const SizedBox(width: 8),
                Text(_fmtDate(value),
                    style: AppTextStyles.body.copyWith(color: AppColors.textHi)),
              ],
            ),
          ),
        ),
      ],
    );
  }
}
