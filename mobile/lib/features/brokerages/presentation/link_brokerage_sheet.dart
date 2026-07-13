import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import '../../../core/theme/app_colors.dart';
import '../../../core/theme/app_text_styles.dart';
import '../../../core/widgets/app_button.dart';
import '../../../core/widgets/app_toggle.dart';
import '../../../core/widgets/common_widgets.dart';
import '../application/brokerages_controller.dart';
import '../data/brokerage_repository.dart';
import '../data/models/brokerage.dart';

// ── Public entry-point ────────────────────────────────────────────────────────

/// Show the Link / Edit bottom sheet.
///
/// [editAccount] — pass an existing [Brokerage] to open in edit mode.
Future<void> showLinkBrokerageSheet(
  BuildContext context, {
  Brokerage? editAccount,
}) {
  return showModalBottomSheet<void>(
    context: context,
    isScrollControlled: true,
    backgroundColor: Colors.transparent,
    builder: (_) => _LinkBrokerageSheet(editAccount: editAccount),
  );
}

// ── Internal sheet ────────────────────────────────────────────────────────────

class _LinkBrokerageSheet extends ConsumerStatefulWidget {
  const _LinkBrokerageSheet({this.editAccount});

  final Brokerage? editAccount;

  @override
  ConsumerState<_LinkBrokerageSheet> createState() =>
      _LinkBrokerageSheetState();
}

class _LinkBrokerageSheetState extends ConsumerState<_LinkBrokerageSheet>
    with SingleTickerProviderStateMixin {
  late final TabController _tabCtrl;
  bool get _editMode => widget.editAccount != null;

  // ── Alpaca form state ──────────────────────────────────────────────────────
  final _alpacaNameCtrl = TextEditingController();
  final _alpacaKeyCtrl = TextEditingController();
  final _alpacaSecretCtrl = TextEditingController();
  bool _alpacaPaper = true;
  String _alpacaFeed = 'iex';

  // ── Alpaca test-suite state ───────────────────────────────────────────────
  bool _testRunning = false;
  Map<String, dynamic>? _testResult;
  bool _showTestPanel = false;

  // ── Robinhood form state ──────────────────────────────────────────────────
  final _rhNameCtrl = TextEditingController();
  final _rhAccessCtrl = TextEditingController();
  final _rhRefreshCtrl = TextEditingController();
  final _rhDeviceCtrl = TextEditingController();
  int _rhStep = 1; // 1 = credentials, 2 = account picker
  List<Map<String, dynamic>> _rhAccounts = [];
  String? _rhSelected;

  // ── Binance.US form state (spot 0.00% maker / 0.02% taker) ────────────────
  final _busNameCtrl = TextEditingController();
  final _busKeyCtrl = TextEditingController();
  final _busSecretCtrl = TextEditingController();
  bool _busPaper = true;

  // ── Shared submission state ───────────────────────────────────────────────
  bool _submitting = false;
  String? _submitMsg;
  bool _submitOk = false;

  @override
  void initState() {
    super.initState();
    _tabCtrl = TabController(
      length: 3,
      vsync: this,
      initialIndex: _editMode ? _tabIndexFor(widget.editAccount!.brokerageType) : 0,
    );
    _tabCtrl.addListener(() {
      if (!_tabCtrl.indexIsChanging) {
        setState(() {
          _submitMsg = null;
          _submitOk = false;
          _rhStep = 1;
          _rhAccounts = [];
          _rhSelected = null;
        });
      }
    });

    // Pre-fill in edit mode.
    if (_editMode) {
      final a = widget.editAccount!;
      if (a.brokerageType == 'alpaca') {
        _alpacaNameCtrl.text = a.accountName;
        _alpacaPaper = a.paper;
        _alpacaFeed = a.alpacaDataFeed ?? 'iex';
      } else if (a.brokerageType == 'binanceus') {
        _busNameCtrl.text = a.accountName;
        _busPaper = a.paper;
      } else {
        _rhNameCtrl.text = a.accountName;
      }
    }
  }

  // Tab order: 0 = Alpaca, 1 = Robinhood, 2 = Binance.US.
  int _tabIndexFor(String brokerageType) {
    switch (brokerageType) {
      case 'robinhood':
        return 1;
      case 'binanceus':
        return 2;
      default:
        return 0;
    }
  }

  @override
  void dispose() {
    _tabCtrl.dispose();
    _alpacaNameCtrl.dispose();
    _alpacaKeyCtrl.dispose();
    _alpacaSecretCtrl.dispose();
    _rhNameCtrl.dispose();
    _rhAccessCtrl.dispose();
    _rhRefreshCtrl.dispose();
    _rhDeviceCtrl.dispose();
    _busNameCtrl.dispose();
    _busKeyCtrl.dispose();
    _busSecretCtrl.dispose();
    super.dispose();
  }

  // ── Helpers ───────────────────────────────────────────────────────────────

  void _setMsg(String? msg, {bool ok = false}) =>
      setState(() { _submitMsg = msg; _submitOk = ok; });

  void _setSubmitting(bool v) => setState(() => _submitting = v);


  // ── Alpaca: run test suite ────────────────────────────────────────────────

  Future<void> _runAlpacaTest() async {
    final key = _alpacaKeyCtrl.text.trim();
    final secret = _alpacaSecretCtrl.text.trim();
    final hasFormCreds = key.isNotEmpty && secret.isNotEmpty;
    final canTestStored = _editMode && widget.editAccount!.id.isNotEmpty;

    if (!hasFormCreds && !canTestStored) {
      setState(() {
        _testResult = {
          'ok': false,
          'summary': {'passed': 0, 'failed': 0, 'total': 0},
          'tests': <dynamic>[],
          'hints': ['Fill in both API Key ID and Secret Key first.'],
        };
        _showTestPanel = true;
      });
      return;
    }

    setState(() {
      _testRunning = true;
      _testResult = null;
      _showTestPanel = true;
    });

    try {
      final body = hasFormCreds
          ? {
              'key': key,
              'secret': secret,
              'paper': _alpacaPaper,
              'alpaca_data_feed': _alpacaFeed,
            }
          : {
              'brokerage_id': widget.editAccount!.id,
              'paper': _alpacaPaper,
              'alpaca_data_feed': _alpacaFeed,
            };
      final result = await ref.read(brokerageRepositoryProvider).testAlpaca(body);
      if (mounted) setState(() { _testResult = result; _testRunning = false; });
    } catch (e) {
      if (mounted) {
        setState(() {
          _testResult = {
            'ok': false,
            'summary': {'passed': 0, 'failed': 0, 'total': 0},
            'tests': <dynamic>[],
            'hints': ['Network error: $e'],
          };
          _testRunning = false;
        });
      }
    }
  }

  // ── Alpaca: save (with optional bypass) ──────────────────────────────────

  Future<void> _submitAlpaca({bool bypassTest = false}) async {
    final name = _alpacaNameCtrl.text.trim();
    final key = _alpacaKeyCtrl.text.trim();
    final secret = _alpacaSecretCtrl.text.trim();

    if (name.isEmpty) { _setMsg('Account name is required'); return; }
    if (!_editMode && key.isEmpty) { _setMsg('API Key ID is required'); return; }
    if (!_editMode && secret.isEmpty) { _setMsg('Secret Key is required'); return; }

    // Run pre-save test when we have form creds and bypass is not requested.
    if (!bypassTest && key.isNotEmpty && secret.isNotEmpty) {
      _setSubmitting(true);
      _setMsg('Validating credentials…');
      try {
        final result = await ref.read(brokerageRepositoryProvider).testAlpaca({
          'key': key,
          'secret': secret,
          'paper': _alpacaPaper,
          'alpaca_data_feed': _alpacaFeed,
        });
        if (result['ok'] == true) {
          setState(() { _testResult = result; });
          // All passed — fall through to save.
        } else {
          setState(() {
            _testResult = result;
            _showTestPanel = true;
            _submitMsg = 'Credential test failed — review below. Tap "Save Anyway" to bypass.';
            _submitOk = false;
            _submitting = false;
          });
          return;
        }
      } catch (e) {
        setState(() {
          _submitMsg = 'Pre-save test errored ($e); tap Save again to bypass.';
          _submitOk = false;
          _submitting = false;
        });
        return;
      }
    }

    _setSubmitting(true);
    _setMsg('');
    try {
      final body = _editMode
          ? {
              if (name.isNotEmpty) 'account_name': name,
              if (key.isNotEmpty) 'key': key,
              if (secret.isNotEmpty) 'secret': secret,
              'paper': _alpacaPaper,
              'alpaca_data_feed': _alpacaFeed,
            }
          : {
              'brokerage_type': 'alpaca',
              'account_name': name,
              'key': key,
              'secret': secret,
              'paper': _alpacaPaper,
              'alpaca_data_feed': _alpacaFeed,
            };

      if (_editMode) {
        await ref.read(brokerageRepositoryProvider).edit(
            widget.editAccount!.id, body);
      } else {
        await ref.read(brokerageRepositoryProvider).link(body);
      }
      _setMsg(_editMode ? 'Account updated!' : 'Account linked!', ok: true);
      _setSubmitting(false);
      await ref.read(brokeragesControllerProvider.notifier).refresh();
      if (mounted) {
        await Future<void>.delayed(const Duration(milliseconds: 1200));
        if (mounted) Navigator.of(context).pop();
      }
    } catch (e) {
      _setMsg(e.toString());
      _setSubmitting(false);
    }
  }

  // ── Robinhood step 1 → 2 ─────────────────────────────────────────────────

  Future<void> _rhFetchAccounts() async {
    final name = _rhNameCtrl.text.trim();
    final access = _rhAccessCtrl.text.trim();
    final refresh = _rhRefreshCtrl.text.trim();

    if (name.isEmpty) { _setMsg('Account name is required'); return; }
    // Edit-mode with no new tokens → save name only.
    if (_editMode && access.isEmpty) { await _submitRobinhood(); return; }
    if (access.isEmpty) { _setMsg('Access token is required'); return; }
    if (refresh.isEmpty) { _setMsg('Refresh token is required'); return; }

    _setSubmitting(true);
    _setMsg('Fetching accounts…');
    try {
      final result = await ref.read(brokerageRepositoryProvider).robinhoodAccounts({
        'access_token': access,
        'refresh_token': refresh,
        if (_rhDeviceCtrl.text.trim().isNotEmpty)
          'device_token': _rhDeviceCtrl.text.trim(),
      });
      final accounts = (result['accounts'] as List<dynamic>? ?? [])
          .whereType<Map<String, dynamic>>()
          .toList();
      final selectable =
          accounts.where((a) => a['management_type'] != 'managed').toList();
      setState(() {
        _rhAccounts = accounts;
        _rhSelected = selectable.length == 1
            ? selectable[0]['account_number'] as String?
            : null;
        _rhStep = 2;
        _submitMsg = null;
        _submitting = false;
      });
    } catch (e) {
      _setMsg(e.toString());
      _setSubmitting(false);
    }
  }

  // ── Robinhood final save ──────────────────────────────────────────────────

  Future<void> _submitRobinhood() async {
    final name = _rhNameCtrl.text.trim();
    final access = _rhAccessCtrl.text.trim();
    final rfresh = _rhRefreshCtrl.text.trim();

    if (!_editMode && (_rhSelected == null || _rhSelected!.isEmpty)) {
      _setMsg('Please select an account');
      return;
    }

    _setSubmitting(true);
    _setMsg('');
    try {
      final body = _editMode
          ? {
              if (name.isNotEmpty) 'account_name': name,
              if (access.isNotEmpty) 'access_token': access,
              if (rfresh.isNotEmpty) 'refresh_token': rfresh,
              if (_rhDeviceCtrl.text.trim().isNotEmpty)
                'device_token': _rhDeviceCtrl.text.trim(),
              if (_rhSelected != null && _rhSelected!.isNotEmpty)
                'account_number': _rhSelected,
            }
          : {
              'brokerage_type': 'robinhood',
              'account_name': name,
              'access_token': access,
              'refresh_token': rfresh,
              if (_rhDeviceCtrl.text.trim().isNotEmpty)
                'device_token': _rhDeviceCtrl.text.trim(),
              'account_number': _rhSelected,
            };

      if (_editMode) {
        await ref
            .read(brokerageRepositoryProvider)
            .edit(widget.editAccount!.id, body);
      } else {
        await ref.read(brokerageRepositoryProvider).link(body);
      }
      _setMsg(_editMode ? 'Account updated!' : 'Account linked!', ok: true);
      _setSubmitting(false);
      await ref.read(brokeragesControllerProvider.notifier).refresh();
      if (mounted) {
        await Future<void>.delayed(const Duration(milliseconds: 1200));
        if (mounted) Navigator.of(context).pop();
      }
    } catch (e) {
      _setMsg(e.toString());
      _setSubmitting(false);
    }
  }

  // ── Binance.US save ───────────────────────────────────────────────────────

  Future<void> _submitBinanceus() async {
    final name = _busNameCtrl.text.trim();
    final key = _busKeyCtrl.text.trim();
    final secret = _busSecretCtrl.text.trim();

    if (name.isEmpty) { _setMsg('Account name is required'); return; }
    if (!_editMode && key.isEmpty) { _setMsg('API Key is required'); return; }
    if (!_editMode && secret.isEmpty) { _setMsg('Secret Key is required'); return; }

    _setSubmitting(true);
    _setMsg('');
    try {
      final body = _editMode
          ? {
              if (name.isNotEmpty) 'account_name': name,
              if (key.isNotEmpty) 'key': key,
              if (secret.isNotEmpty) 'secret': secret,
              'paper': _busPaper,
            }
          : {
              'brokerage_type': 'binanceus',
              'account_name': name,
              'key': key,
              'secret': secret,
              'paper': _busPaper,
            };

      if (_editMode) {
        await ref
            .read(brokerageRepositoryProvider)
            .edit(widget.editAccount!.id, body);
      } else {
        await ref.read(brokerageRepositoryProvider).link(body);
      }
      _setMsg(_editMode ? 'Account updated!' : 'Account linked!', ok: true);
      _setSubmitting(false);
      await ref.read(brokeragesControllerProvider.notifier).refresh();
      if (mounted) {
        await Future<void>.delayed(const Duration(milliseconds: 1200));
        if (mounted) Navigator.of(context).pop();
      }
    } catch (e) {
      _setMsg(e.toString());
      _setSubmitting(false);
    }
  }

  // ── UI ────────────────────────────────────────────────────────────────────

  @override
  Widget build(BuildContext context) {
    final mq = MediaQuery.of(context);
    return Container(
      margin: EdgeInsets.only(top: mq.padding.top + 40),
      decoration: BoxDecoration(
        color: AppColors.panel,
        borderRadius: const BorderRadius.vertical(top: Radius.circular(20)),
        border: Border.all(color: AppColors.border),
      ),
      child: Column(
        mainAxisSize: MainAxisSize.min,
        children: [
          _buildDragHandle(),
          _buildHeader(),
          if (!_editMode) _buildTabBar(),
          Flexible(
            child: SingleChildScrollView(
              padding: EdgeInsets.only(bottom: mq.viewInsets.bottom + 24),
              child: Padding(
                padding: const EdgeInsets.symmetric(horizontal: 20),
                child: _editMode
                    ? _buildEditBody()
                    : TabBarView(
                        controller: _tabCtrl,
                        physics: const NeverScrollableScrollPhysics(),
                        children: [
                          _buildAlpacaTab(),
                          _buildRobinhoodTab(),
                          _buildBinanceusTab(),
                        ],
                      ),
              ),
            ),
          ),
        ],
      ),
    );
  }

  Widget _buildDragHandle() => Center(
        child: Container(
          margin: const EdgeInsets.only(top: 12, bottom: 4),
          width: 36,
          height: 4,
          decoration: BoxDecoration(
            color: AppColors.border,
            borderRadius: BorderRadius.circular(999),
          ),
        ),
      );

  Widget _buildHeader() {
    return Padding(
      padding: const EdgeInsets.fromLTRB(20, 12, 8, 12),
      child: Row(
        children: [
          Expanded(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Text(
                  _editMode
                      ? 'Edit Brokerage Account'
                      : 'Link Brokerage Account',
                  style: AppTextStyles.h3,
                ),
                if (!_editMode && _tabCtrl.index == 1 && _rhStep == 2)
                  Padding(
                    padding: const EdgeInsets.only(top: 2),
                    child: RichText(
                      text: TextSpan(
                        style: AppTextStyles.micro,
                        children: [
                          TextSpan(
                            text: '1. Credentials',
                            style: AppTextStyles.micro
                                .copyWith(color: AppColors.textDim),
                          ),
                          TextSpan(
                            text: ' › ',
                            style: AppTextStyles.micro
                                .copyWith(color: AppColors.textDim),
                          ),
                          TextSpan(
                            text: '2. Select Account',
                            style: AppTextStyles.micro
                                .copyWith(color: AppColors.primary),
                          ),
                        ],
                      ),
                    ),
                  ),
              ],
            ),
          ),
          IconButton(
            icon: const Icon(Icons.close, size: 20),
            color: AppColors.textMuted,
            onPressed: _submitting ? null : () => Navigator.of(context).pop(),
          ),
        ],
      ),
    );
  }

  Widget _buildTabBar() {
    return Padding(
      padding: const EdgeInsets.fromLTRB(20, 0, 20, 16),
      child: Container(
        padding: const EdgeInsets.all(4),
        decoration: BoxDecoration(
          color: AppColors.surface,
          borderRadius: BorderRadius.circular(10),
          border: Border.all(color: AppColors.border),
        ),
        child: TabBar(
          controller: _tabCtrl,
          dividerColor: Colors.transparent,
          indicator: BoxDecoration(
            color: AppColors.primary,
            borderRadius: BorderRadius.circular(7),
          ),
          labelColor: AppColors.onPrimary,
          unselectedLabelColor: AppColors.textMuted,
          labelStyle: AppTextStyles.cardTitle,
          isScrollable: false,
          labelPadding: const EdgeInsets.symmetric(horizontal: 4),
          tabs: const [
            Tab(text: 'Alpaca'),
            Tab(text: 'Robinhood'),
            Tab(text: 'Binance.US'),
          ],
        ),
      ),
    );
  }

  // In edit mode we just show the right tab body directly (no TabBar).
  Widget _buildEditBody() {
    switch (widget.editAccount!.brokerageType) {
      case 'alpaca':
        return _buildAlpacaTab();
      case 'binanceus':
        return _buildBinanceusTab();
      default:
        return _buildRobinhoodTab();
    }
  }

  // ── Alpaca tab ─────────────────────────────────────────────────────────────

  Widget _buildAlpacaTab() {
    return Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: [
        _field('Account Name', _alpacaNameCtrl,
            placeholder: 'e.g. My Paper Trading'),
        const SizedBox(height: 14),
        _field('API Key ID', _alpacaKeyCtrl,
            placeholder: 'PKXXXXXXXXXXXXXXXXXXXXXXXX', mono: true),
        const SizedBox(height: 14),
        _field(
          'Secret Key${_editMode ? ' (leave blank to keep existing)' : ''}',
          _alpacaSecretCtrl,
          placeholder: _editMode
              ? 'Leave blank to keep existing'
              : '●●●●●●●●●●●●●●●●●●●●●●●●',
          obscure: true,
          mono: true,
        ),
        const SizedBox(height: 14),
        Row(
          children: [
            AppToggle(
              value: _alpacaPaper,
              onChanged: (v) => setState(() => _alpacaPaper = v),
            ),
            const SizedBox(width: 10),
            Expanded(
              child: RichText(
                text: TextSpan(
                  style: AppTextStyles.body,
                  text: 'Paper trading  ',
                  children: [
                    TextSpan(
                      text: _alpacaPaper
                          ? '(paper-api.alpaca.markets)'
                          : '(api.alpaca.markets)',
                      style: AppTextStyles.meta,
                    ),
                  ],
                ),
              ),
            ),
          ],
        ),
        const SizedBox(height: 14),
        Text('Market Data Feed', style: AppTextStyles.meta),
        const SizedBox(height: 6),
        _DropdownField(
          value: _alpacaFeed,
          items: const {'iex': 'IEX (free — Basic Market Data)', 'sip': 'SIP (paid — Algo Trader Plus)'},
          onChanged: (v) => setState(() => _alpacaFeed = v ?? 'iex'),
        ),
        const SizedBox(height: 4),
        Text(
          'Paper accounts have free IEX. Live accounts need a subscription for IEX or SIP.',
          style: AppTextStyles.nano.copyWith(color: AppColors.textDim),
        ),
        const SizedBox(height: 16),
        // Test suite panel (shown after Test button pressed)
        if (_showTestPanel) ...[
          _buildTestPanel(),
          const SizedBox(height: 12),
        ],
        _buildStatusMsg(),
        const SizedBox(height: 12),
        Row(
          children: [
            // Test button
            Expanded(
              child: OutlinedButton.icon(
                onPressed: (_submitting || _testRunning) ? null : _runAlpacaTest,
                icon: _testRunning
                    ? const SizedBox(
                        width: 14,
                        height: 14,
                        child: CircularProgressIndicator(strokeWidth: 2),
                      )
                    : const Icon(Icons.network_check, size: 16),
                label: Text(_testRunning ? 'Testing…' : 'Test'),
                style: OutlinedButton.styleFrom(
                  foregroundColor: AppColors.primary,
                  side: BorderSide(color: AppColors.primary.withValues(alpha: 0.5)),
                  textStyle: AppTextStyles.cardTitle,
                ),
              ),
            ),
            const SizedBox(width: 10),
            // Save button
            Expanded(
              flex: 2,
              child: AppButton.primary(
                label: _editMode ? 'Save Changes' : 'Link Account',
                busy: _submitting,
                onPressed: _submitting ? null : () => _submitAlpaca(),
              ),
            ),
          ],
        ),
        // Save Anyway when test failed
        if (_showTestPanel &&
            _testResult != null &&
            _testResult!['ok'] != true &&
            !_testRunning) ...[
          const SizedBox(height: 8),
          OutlinedButton(
            onPressed: _submitting ? null : () => _submitAlpaca(bypassTest: true),
            style: OutlinedButton.styleFrom(
              foregroundColor: AppColors.warning,
              side: BorderSide(color: AppColors.warning.withValues(alpha: 0.4)),
              textStyle: AppTextStyles.cardTitle,
            ),
            child: const Text('Save Anyway'),
          ),
        ],
        const SizedBox(height: 8),
      ],
    );
  }

  Widget _buildTestPanel() {
    if (_testRunning) {
      return _infoBox(
        color: AppColors.info,
        child: Row(children: [
          const SizedBox(
            width: 14,
            height: 14,
            child: CircularProgressIndicator(strokeWidth: 2),
          ),
          const SizedBox(width: 10),
          Text('Running 5-endpoint probe against Alpaca…',
              style: AppTextStyles.meta.copyWith(color: AppColors.info)),
        ]),
      );
    }
    if (_testResult == null) return const SizedBox.shrink();

    final result = _testResult!;
    final summary =
        result['summary'] as Map<String, dynamic>? ?? {};
    final total = (summary['total'] as num?)?.toInt() ?? 0;
    final failed = (summary['failed'] as num?)?.toInt() ?? 0;
    final ok = result['ok'] == true;
    final tests = (result['tests'] as List<dynamic>? ?? []);
    final hints = (result['hints'] as List<dynamic>? ?? []);

    return Container(
      decoration: BoxDecoration(
        color: AppColors.panelAlt,
        borderRadius: BorderRadius.circular(10),
        border: Border.all(color: AppColors.border),
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          // Summary header
          Container(
            padding: const EdgeInsets.symmetric(horizontal: 14, vertical: 10),
            decoration: BoxDecoration(
              color: ok
                  ? AppColors.fill(AppColors.success)
                  : (total == 0
                      ? AppColors.fill(AppColors.warning)
                      : AppColors.fill(AppColors.danger)),
              borderRadius:
                  const BorderRadius.vertical(top: Radius.circular(10)),
              border: Border(
                bottom: BorderSide(color: AppColors.border),
              ),
            ),
            child: Row(
              children: [
                Icon(
                  total == 0
                      ? Icons.info_outline
                      : (ok ? Icons.check_circle_outline : Icons.error_outline),
                  size: 16,
                  color: total == 0
                      ? AppColors.warning
                      : (ok ? AppColors.success : AppColors.danger),
                ),
                const SizedBox(width: 8),
                Expanded(
                  child: Text(
                    total == 0
                        ? 'Probe could not run — see hints below.'
                        : ok
                            ? 'All $total tests passed'
                            : '$failed of $total tests failed',
                    style: AppTextStyles.meta.copyWith(
                      color: total == 0
                          ? AppColors.warning
                          : (ok ? AppColors.success : AppColors.danger),
                      fontWeight: FontWeight.w600,
                    ),
                  ),
                ),
                GestureDetector(
                  onTap: () => setState(() => _showTestPanel = false),
                  child:
                      Icon(Icons.close, size: 16, color: AppColors.textMuted),
                ),
              ],
            ),
          ),
          // Per-test chips
          if (tests.isNotEmpty)
            Padding(
              padding: const EdgeInsets.all(12),
              child: Column(
                children: tests.whereType<Map<String, dynamic>>().map((t) {
                  final tok = t['ok'] == true;
                  final tColor = tok ? AppColors.success : AppColors.danger;
                  return Container(
                    margin: const EdgeInsets.only(bottom: 6),
                    padding: const EdgeInsets.symmetric(
                        horizontal: 10, vertical: 8),
                    decoration: BoxDecoration(
                      color: AppColors.fill(tColor),
                      borderRadius: BorderRadius.circular(8),
                      border:
                          Border.all(color: AppColors.stroke(tColor)),
                    ),
                    child: Row(
                      crossAxisAlignment: CrossAxisAlignment.start,
                      children: [
                        Icon(
                            tok
                                ? Icons.check_circle_outline
                                : Icons.cancel_outlined,
                            size: 14,
                            color: tColor),
                        const SizedBox(width: 8),
                        Expanded(
                          child: Column(
                            crossAxisAlignment: CrossAxisAlignment.start,
                            children: [
                              Row(
                                mainAxisAlignment:
                                    MainAxisAlignment.spaceBetween,
                                children: [
                                  Text(
                                    t['name'] as String? ?? '',
                                    style: AppTextStyles.mono(11,
                                        color: AppColors.textHi),
                                  ),
                                  if (t['status'] != null)
                                    Text(
                                      'HTTP ${t['status']}',
                                      style: AppTextStyles.nano
                                          .copyWith(color: tColor),
                                    ),
                                ],
                              ),
                              if ((t['message'] as String?)?.isNotEmpty ==
                                  true) ...[
                                const SizedBox(height: 2),
                                Text(
                                  t['message'] as String,
                                  style: AppTextStyles.nano.copyWith(
                                      color: tok
                                          ? AppColors.textDim
                                          : AppColors.danger),
                                ),
                              ],
                            ],
                          ),
                        ),
                      ],
                    ),
                  );
                }).toList(),
              ),
            ),
          // Hints
          if (hints.isNotEmpty)
            Container(
              margin: const EdgeInsets.fromLTRB(12, 0, 12, 12),
              padding: const EdgeInsets.all(10),
              decoration: BoxDecoration(
                color: AppColors.fill(AppColors.warning),
                borderRadius: BorderRadius.circular(8),
                border: Border.all(
                    color: AppColors.warning.withValues(alpha: 0.3)),
              ),
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Row(children: [
                    Icon(Icons.lightbulb_outline,
                        size: 13, color: AppColors.warning),
                    const SizedBox(width: 4),
                    Text('Hints',
                        style: AppTextStyles.nano.copyWith(
                            color: AppColors.warning,
                            fontWeight: FontWeight.w600)),
                  ]),
                  const SizedBox(height: 4),
                  ...hints.map((h) => Padding(
                        padding: const EdgeInsets.only(top: 2),
                        child: Text(
                          h.toString(),
                          style: AppTextStyles.nano
                              .copyWith(color: AppColors.warning),
                        ),
                      )),
                ],
              ),
            ),
        ],
      ),
    );
  }

  // ── Binance.US tab ─────────────────────────────────────────────────────────

  Widget _buildBinanceusTab() {
    return Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: [
        _infoBox(
          color: AppColors.primary,
          child: RichText(
            text: TextSpan(
              style: AppTextStyles.meta.copyWith(color: AppColors.primary),
              text: 'Spot fees: ',
              children: [
                TextSpan(
                  text: '0.00% maker / 0.02% taker',
                  style: AppTextStyles.meta.copyWith(
                      color: AppColors.primary, fontWeight: FontWeight.w700),
                ),
                const TextSpan(
                  text:
                      ' — ~12× cheaper than Alpaca crypto (0.25%), which is what makes '
                      'high-frequency strategies viable. Create a read+trade API key '
                      'at binance.us (no withdrawal permission needed).',
                ),
              ],
            ),
          ),
        ),
        const SizedBox(height: 14),
        _field('Account Name', _busNameCtrl,
            placeholder: 'e.g. Binance.US Paper'),
        const SizedBox(height: 14),
        _field('API Key', _busKeyCtrl,
            placeholder: 'Binance.US API key', mono: true),
        const SizedBox(height: 14),
        _field(
          'Secret Key${_editMode ? ' (leave blank to keep existing)' : ''}',
          _busSecretCtrl,
          placeholder: _editMode
              ? 'Leave blank to keep existing'
              : '●●●●●●●●●●●●●●●●●●●●●●●●',
          obscure: true,
          mono: true,
        ),
        const SizedBox(height: 14),
        Row(
          children: [
            AppToggle(
              value: _busPaper,
              onChanged: (v) => setState(() => _busPaper = v),
            ),
            const SizedBox(width: 10),
            Expanded(
              child: RichText(
                text: TextSpan(
                  style: AppTextStyles.body,
                  text: 'Paper trading  ',
                  children: [
                    TextSpan(
                      text: _busPaper
                          ? '(simulated fills vs. live price)'
                          : '(live signed orders · real money)',
                      style: AppTextStyles.meta,
                    ),
                  ],
                ),
              ),
            ),
          ],
        ),
        if (!_busPaper) ...[
          const SizedBox(height: 10),
          _infoBox(
            color: AppColors.warning,
            child: Text(
              '⚠ Live account — instances bound here place real Binance.US MARKET '
              'orders with real funds.',
              style: AppTextStyles.meta.copyWith(color: AppColors.warning),
            ),
          ),
        ],
        const SizedBox(height: 16),
        _buildStatusMsg(),
        const SizedBox(height: 12),
        Row(children: [
          Expanded(
            child: AppButton.ghost(
              label: 'Cancel',
              onPressed:
                  _submitting ? null : () => Navigator.of(context).pop(),
            ),
          ),
          const SizedBox(width: 10),
          Expanded(
            flex: 2,
            child: AppButton.primary(
              label: _editMode ? 'Save Changes' : 'Link Account',
              busy: _submitting,
              onPressed: _submitting ? null : _submitBinanceus,
            ),
          ),
        ]),
        const SizedBox(height: 8),
      ],
    );
  }

  // ── Robinhood tab ──────────────────────────────────────────────────────────

  Widget _buildRobinhoodTab() {
    return _rhStep == 1 ? _buildRhStep1() : _buildRhStep2();
  }

  Widget _buildRhStep1() {
    return Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: [
        if (_editMode)
          _infoBox(
            color: AppColors.info,
            child: Text(
              'Update account name and/or paste new tokens. Leave tokens blank to only update the name.',
              style: AppTextStyles.meta.copyWith(color: AppColors.info),
            ),
          )
        else
          _infoBox(
            color: AppColors.warning,
            child: Text(
              'Paste your Robinhood tokens obtained from the CLI tool.',
              style: AppTextStyles.meta.copyWith(color: AppColors.warning),
            ),
          ),
        const SizedBox(height: 14),
        _field('Account Name', _rhNameCtrl,
            placeholder: 'e.g. My Robinhood Account'),
        const SizedBox(height: 14),
        _field('Access Token', _rhAccessCtrl,
            placeholder: 'Paste access token', multiline: true),
        const SizedBox(height: 14),
        _field('Refresh Token', _rhRefreshCtrl,
            placeholder: 'Paste refresh token', multiline: true),
        const SizedBox(height: 14),
        _field('Device Token (optional)', _rhDeviceCtrl,
            placeholder: 'Leave blank to auto-generate', mono: true),
        const SizedBox(height: 16),
        _buildStatusMsg(),
        const SizedBox(height: 12),
        Row(children: [
          Expanded(
            child: AppButton.ghost(
              label: 'Cancel',
              onPressed:
                  _submitting ? null : () => Navigator.of(context).pop(),
            ),
          ),
          const SizedBox(width: 10),
          Expanded(
            flex: 2,
            child: AppButton.primary(
              label: _editMode
                  ? (_rhAccessCtrl.text.trim().isNotEmpty
                      ? 'Next'
                      : 'Save Changes')
                  : 'Fetch Accounts',
              busy: _submitting,
              onPressed: _submitting ? null : _rhFetchAccounts,
            ),
          ),
        ]),
        const SizedBox(height: 8),
      ],
    );
  }

  Widget _buildRhStep2() {
    return Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: [
        RichText(
          text: TextSpan(
            style: AppTextStyles.meta,
            text: 'Select the account to link as ',
            children: [
              TextSpan(
                text: _rhNameCtrl.text,
                style: AppTextStyles.meta.copyWith(
                    color: AppColors.textHi, fontWeight: FontWeight.w600),
              ),
              const TextSpan(text: '.'),
            ],
          ),
        ),
        const SizedBox(height: 12),
        ..._rhAccounts.map((a) {
          final isManaged = a['management_type'] == 'managed';
          final acctNum = a['account_number'] as String? ?? '';
          final selected = _rhSelected == acctNum;
          final equity = _parseDouble(a['equity']);
          final bp = _parseDouble(a['buying_power']);
          final acctType = a['account_type'] as String?;
          final displayName = a['display_name'] as String? ?? acctNum;

          return Padding(
            padding: const EdgeInsets.only(bottom: 8),
            child: GestureDetector(
              onTap: isManaged
                  ? null
                  : () => setState(() => _rhSelected = acctNum),
              child: Opacity(
                opacity: isManaged ? 0.4 : 1,
                child: Container(
                  padding: const EdgeInsets.all(14),
                  decoration: BoxDecoration(
                    color: selected
                        ? AppColors.primary.withValues(alpha: 0.08)
                        : AppColors.surface.withValues(alpha: 0.5),
                    borderRadius: BorderRadius.circular(12),
                    border: Border.all(
                      color: selected
                          ? AppColors.primary
                          : AppColors.border,
                    ),
                  ),
                  child: Row(
                    children: [
                      // Radio dot
                      Container(
                        width: 16,
                        height: 16,
                        decoration: BoxDecoration(
                          shape: BoxShape.circle,
                          border: Border.all(
                            color: selected
                                ? AppColors.primary
                                : AppColors.textDim,
                            width: 2,
                          ),
                        ),
                        child: selected
                            ? Center(
                                child: Container(
                                  width: 8,
                                  height: 8,
                                  decoration: const BoxDecoration(
                                    color: AppColors.primary,
                                    shape: BoxShape.circle,
                                  ),
                                ),
                              )
                            : null,
                      ),
                      const SizedBox(width: 10),
                      // Account info
                      Expanded(
                        child: Column(
                          crossAxisAlignment: CrossAxisAlignment.start,
                          children: [
                            Row(children: [
                              Text(displayName,
                                  style: AppTextStyles.bodyHi),
                              if (isManaged) ...[
                                const SizedBox(width: 6),
                                AppBadge(
                                    label: 'Managed',
                                    color: AppColors.textDim),
                              ],
                            ]),
                            Text(
                              '$acctNum${acctType != null ? ' · ${_capitalize(acctType)}' : ''}',
                              style: AppTextStyles.mono(11,
                                  color: AppColors.textDim),
                            ),
                          ],
                        ),
                      ),
                      // Equity + buying power
                      Column(
                        crossAxisAlignment: CrossAxisAlignment.end,
                        children: [
                          Text(
                            equity != null ? '\$${equity.toStringAsFixed(2)}' : '—',
                            style: AppTextStyles.value,
                          ),
                          Text(
                            'BP: ${bp != null ? '\$${bp.toStringAsFixed(2)}' : '—'}',
                            style: AppTextStyles.meta,
                          ),
                        ],
                      ),
                    ],
                  ),
                ),
              ),
            ),
          );
        }),
        const SizedBox(height: 12),
        _buildStatusMsg(),
        const SizedBox(height: 8),
        Row(children: [
          Expanded(
            child: AppButton.ghost(
              label: 'Back',
              onPressed: _submitting
                  ? null
                  : () => setState(() {
                        _rhStep = 1;
                        _submitMsg = null;
                      }),
            ),
          ),
          const SizedBox(width: 10),
          Expanded(
            flex: 2,
            child: AppButton.primary(
              label: _editMode ? 'Save Changes' : 'Link Account',
              busy: _submitting,
              onPressed:
                  (_submitting || (_rhSelected == null && !_editMode))
                      ? null
                      : _submitRobinhood,
            ),
          ),
        ]),
        const SizedBox(height: 8),
      ],
    );
  }

  // ── Shared status message ─────────────────────────────────────────────────

  Widget _buildStatusMsg() {
    if (_submitMsg == null || _submitMsg!.isEmpty) {
      return const SizedBox.shrink();
    }
    return _infoBox(
      color: _submitOk ? AppColors.success : AppColors.danger,
      child: Text(
        _submitMsg!,
        style: AppTextStyles.meta.copyWith(
          color: _submitOk ? AppColors.success : AppColors.danger,
        ),
      ),
    );
  }

  // ── Small helpers ─────────────────────────────────────────────────────────

  Widget _field(
    String label,
    TextEditingController ctrl, {
    String placeholder = '',
    bool obscure = false,
    bool mono = false,
    bool multiline = false,
  }) {
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Text(label, style: AppTextStyles.meta),
        const SizedBox(height: 6),
        TextField(
          controller: ctrl,
          obscureText: obscure,
          maxLines: multiline ? 3 : 1,
          style: mono
              ? AppTextStyles.mono(13, color: AppColors.textHi)
              : AppTextStyles.bodyHi,
          decoration: InputDecoration(
            hintText: placeholder,
            hintStyle: AppTextStyles.body.copyWith(color: AppColors.textFaint),
            filled: true,
            fillColor: AppColors.surface,
            contentPadding:
                const EdgeInsets.symmetric(horizontal: 12, vertical: 10),
            border: OutlineInputBorder(
              borderRadius: BorderRadius.circular(8),
              borderSide: BorderSide(color: AppColors.border),
            ),
            enabledBorder: OutlineInputBorder(
              borderRadius: BorderRadius.circular(8),
              borderSide: BorderSide(color: AppColors.border),
            ),
            focusedBorder: OutlineInputBorder(
              borderRadius: BorderRadius.circular(8),
              borderSide: BorderSide(color: AppColors.primary),
            ),
          ),
          onChanged: (_) => setState(() {}), // rebuild for Fetch Accounts label
        ),
      ],
    );
  }

  Widget _infoBox({required Color color, required Widget child}) {
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 10),
      decoration: BoxDecoration(
        color: AppColors.fill(color),
        borderRadius: BorderRadius.circular(8),
        border: Border.all(color: AppColors.stroke(color)),
      ),
      child: child,
    );
  }

  double? _parseDouble(dynamic v) {
    if (v == null) return null;
    if (v is num) return v.toDouble();
    if (v is String) return double.tryParse(v);
    return null;
  }

  String _capitalize(String s) =>
      s.isEmpty ? s : s[0].toUpperCase() + s.substring(1);
}

// ── Simple dropdown ───────────────────────────────────────────────────────────

class _DropdownField extends StatelessWidget {
  const _DropdownField({
    required this.value,
    required this.items,
    required this.onChanged,
  });

  final String value;
  final Map<String, String> items;
  final ValueChanged<String?> onChanged;

  @override
  Widget build(BuildContext context) {
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 12),
      decoration: BoxDecoration(
        color: AppColors.surface,
        borderRadius: BorderRadius.circular(8),
        border: Border.all(color: AppColors.border),
      ),
      child: DropdownButtonHideUnderline(
        child: DropdownButton<String>(
          value: value,
          isExpanded: true,
          dropdownColor: AppColors.panel,
          style: AppTextStyles.bodyHi,
          iconEnabledColor: AppColors.textMuted,
          onChanged: onChanged,
          items: items.entries
              .map((e) => DropdownMenuItem(
                    value: e.key,
                    child: Text(e.value),
                  ))
              .toList(),
        ),
      ),
    );
  }
}
