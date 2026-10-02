import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import '../../../../core/network/api_client.dart';
import '../../../../core/network/api_error.dart';
import '../../../../core/theme/app_colors.dart';
import '../../../../core/theme/app_text_styles.dart';
import '../../../../core/widgets/app_button.dart';
import '../../../../core/widgets/common_widgets.dart';
import '../../../../core/widgets/glass_card.dart';
import '../../../../core/widgets/material_symbols.dart';
import 'onboarding_form_widgets.dart';

/// Connect step: load instances + brokerages, pick one of each, POST link.
class StepConnect extends ConsumerStatefulWidget {
  const StepConnect({super.key});

  @override
  ConsumerState<StepConnect> createState() => _StepConnectState();
}

class _StepConnectState extends ConsumerState<StepConnect> {
  List<Map<String, dynamic>> _instances = [];
  List<Map<String, dynamic>> _brokerages = [];
  bool _loading = true;
  String? _loadError;

  String? _selectedInstance;
  String? _selectedBrokerage;

  bool _busy = false;
  String? _message;
  bool _messageOk = false;

  @override
  void initState() {
    super.initState();
    _load();
  }

  Future<void> _load() async {
    setState(() {
      _loading = true;
      _loadError = null;
    });
    try {
      final client = ref.read(apiClientProvider);
      final results = await Future.wait([
        client.get<Map<String, dynamic>>('/instances'),
        client.get<Map<String, dynamic>>('/brokerages'),
      ]);
      final instData = results[0];
      final brData = results[1];

      final insts =
          (instData['instances'] as List? ??
                  instData['items'] as List? ??
                  const [])
              .whereType<Map<String, dynamic>>()
              .toList();
      final brs =
          (brData['accounts'] as List? ?? brData['items'] as List? ?? const [])
              .whereType<Map<String, dynamic>>()
              .toList();

      setState(() {
        _instances = insts;
        _brokerages = brs;
        _loading = false;
        if (insts.length == 1) {
          _selectedInstance =
              insts[0]['instance_id']?.toString() ?? insts[0]['id']?.toString();
        }
        if (brs.length == 1) {
          _selectedBrokerage = brs[0]['id']?.toString();
        }
      });
    } catch (e) {
      setState(() {
        _loading = false;
        _loadError = e.toString();
      });
    }
  }

  Future<void> _link() async {
    if (_selectedInstance == null || _selectedBrokerage == null) {
      setState(() {
        _message = 'Select both an instance and a brokerage.';
        _messageOk = false;
      });
      return;
    }

    setState(() {
      _busy = true;
      _message = 'Linking…';
      _messageOk = false;
    });

    try {
      final client = ref.read(apiClientProvider);
      await client.post<Map<String, dynamic>>(
        '/instances/${Uri.encodeComponent(_selectedInstance!)}/link-brokerage',
        body: {'brokerage_id': _selectedBrokerage},
      );
      setState(() {
        _busy = false;
        _message =
            'Linked! Your instance can now place orders through this brokerage.';
        _messageOk = true;
      });
    } on ApiError catch (e) {
      setState(() {
        _message = e.message;
        _messageOk = false;
        _busy = false;
      });
    } catch (e) {
      setState(() {
        _message = e.toString();
        _messageOk = false;
        _busy = false;
      });
    }
  }

  @override
  Widget build(BuildContext context) {
    return SingleChildScrollView(
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Text('STEP 4 · CONNECT THE PIECES', style: AppTextStyles.eyebrow),
          const SizedBox(height: 8),
          Text(
            'How a trade actually flows.',
            style: AppTextStyles.h2.copyWith(color: AppColors.textHi),
          ),
          const SizedBox(height: 8),
          Text(
            'Your instance runs a strategy that asks a model. The model returns a buy/sell call. '
            'The instance sends that order through the linked brokerage.',
            style: AppTextStyles.body.copyWith(color: AppColors.textMuted),
          ),
          const SizedBox(height: 20),
          // Flow diagram.
          GlassCard(
            padding: const EdgeInsets.all(14),
            borderColor: AppColors.border,
            child: SingleChildScrollView(
              scrollDirection: Axis.horizontal,
              child: Row(
                children: [
                  for (final entry in const [
                    ('memory', 'Model', 'Reasons over signals'),
                    ('tune', 'Strategy', 'Picks tickers + capital'),
                    ('rocket_launch', 'Instance', 'Runs on a cadence'),
                    ('account_balance', 'Brokerage', 'Executes orders'),
                  ]) ...[
                    _FlowNode(icon: entry.$1, label: entry.$2, desc: entry.$3),
                    if (entry.$2 != 'Brokerage')
                      Padding(
                        padding: const EdgeInsets.only(
                          bottom: 16,
                          left: 4,
                          right: 4,
                        ),
                        child: Icon(
                          Icons.arrow_forward,
                          size: 14,
                          color: AppColors.textFaint,
                        ),
                      ),
                  ],
                ],
              ),
            ),
          ),
          const SizedBox(height: 20),
          // Linker form.
          GlassCard(
            padding: const EdgeInsets.all(16),
            borderColor: AppColors.border,
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Text(
                  'LINK AN INSTANCE TO A BROKERAGE',
                  style: AppTextStyles.nano.copyWith(
                    color: AppColors.textDim,
                    fontWeight: FontWeight.w700,
                    letterSpacing: 1.2,
                  ),
                ),
                const SizedBox(height: 12),
                if (_loading)
                  const LoadingState(label: 'Loading resources…')
                else if (_loadError != null)
                  Column(
                    children: [
                      Text(
                        'Could not load: $_loadError',
                        style: AppTextStyles.meta.copyWith(
                          color: AppColors.textDim,
                        ),
                      ),
                      const SizedBox(height: 8),
                      AppButton.ghost(label: 'Retry', onPressed: _load),
                    ],
                  )
                else if (_instances.isEmpty || _brokerages.isEmpty)
                  Text(
                    "You'll need at least one instance and one brokerage to link them here. "
                    "Skip for now and do it later from the Instances page.",
                    style: AppTextStyles.body.copyWith(
                      color: AppColors.textMuted,
                    ),
                  )
                else
                  Column(
                    children: [
                      _DropdownField(
                        label: 'Instance',
                        value: _selectedInstance,
                        items: _instances
                            .map(
                              (i) => DropdownMenuItem(
                                value:
                                    i['instance_id']?.toString() ??
                                    i['id']?.toString(),
                                child: Text(
                                  (i['name'] ?? i['instance_id'] ?? i['id'])
                                      .toString(),
                                  style: AppTextStyles.body.copyWith(
                                    color: AppColors.textHi,
                                  ),
                                ),
                              ),
                            )
                            .toList(),
                        onChanged: (v) => setState(() => _selectedInstance = v),
                        disabled: _busy,
                      ),
                      const SizedBox(height: 12),
                      _DropdownField(
                        label: 'Brokerage',
                        value: _selectedBrokerage,
                        items: _brokerages
                            .map(
                              (b) => DropdownMenuItem(
                                value: b['id']?.toString(),
                                child: Text(
                                  '${b['account_name']} (${b['brokerage_type']})',
                                  style: AppTextStyles.body.copyWith(
                                    color: AppColors.textHi,
                                  ),
                                ),
                              ),
                            )
                            .toList(),
                        onChanged: (v) =>
                            setState(() => _selectedBrokerage = v),
                        disabled: _busy,
                      ),
                      if (_message != null) ...[
                        const SizedBox(height: 10),
                        OnboardingMessageBanner(
                          message: _message!,
                          ok: _messageOk,
                        ),
                      ],
                      const SizedBox(height: 14),
                      Align(
                        alignment: Alignment.centerRight,
                        child: AppButton.primary(
                          label: _busy
                              ? 'Linking…'
                              : 'Link brokerage to instance',
                          icon: symbol('link'),
                          busy: _busy,
                          onPressed:
                              (_selectedInstance != null &&
                                  _selectedBrokerage != null)
                              ? _link
                              : null,
                        ),
                      ),
                    ],
                  ),
              ],
            ),
          ),
        ],
      ),
    );
  }
}

class _FlowNode extends StatelessWidget {
  const _FlowNode({
    required this.icon,
    required this.label,
    required this.desc,
  });

  final String icon;
  final String label;
  final String desc;

  @override
  Widget build(BuildContext context) {
    return SizedBox(
      width: 72,
      child: Column(
        children: [
          IconTile(icon: symbol(icon), color: AppColors.primary, size: 40),
          const SizedBox(height: 6),
          Text(
            label,
            textAlign: TextAlign.center,
            style: AppTextStyles.meta.copyWith(color: AppColors.textHi),
          ),
          const SizedBox(height: 2),
          Text(
            desc,
            textAlign: TextAlign.center,
            style: AppTextStyles.nano.copyWith(
              color: AppColors.textDim,
              height: 1.3,
            ),
          ),
        ],
      ),
    );
  }
}

class _DropdownField extends StatelessWidget {
  const _DropdownField({
    required this.label,
    required this.value,
    required this.items,
    required this.onChanged,
    this.disabled = false,
  });

  final String label;
  final String? value;
  final List<DropdownMenuItem<String>> items;
  final ValueChanged<String?> onChanged;
  final bool disabled;

  @override
  Widget build(BuildContext context) {
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Text(
          label,
          style: AppTextStyles.nano.copyWith(
            color: AppColors.textMuted,
            fontWeight: FontWeight.w500,
          ),
        ),
        const SizedBox(height: 6),
        DropdownButtonFormField<String>(
          initialValue: value,
          onChanged: disabled ? null : onChanged,
          dropdownColor: AppColors.surface,
          hint: Text(
            'Pick one…',
            style: AppTextStyles.body.copyWith(color: AppColors.textFaint),
          ),
          style: AppTextStyles.body.copyWith(color: AppColors.textHi),
          decoration: InputDecoration(
            contentPadding: const EdgeInsets.symmetric(
              horizontal: 12,
              vertical: 10,
            ),
            filled: true,
            fillColor: AppColors.surface,
            border: OutlineInputBorder(
              borderRadius: BorderRadius.circular(8),
              borderSide: const BorderSide(color: AppColors.border),
            ),
            enabledBorder: OutlineInputBorder(
              borderRadius: BorderRadius.circular(8),
              borderSide: const BorderSide(color: AppColors.border),
            ),
            focusedBorder: OutlineInputBorder(
              borderRadius: BorderRadius.circular(8),
              borderSide: const BorderSide(
                color: AppColors.primary,
                width: 1.5,
              ),
            ),
          ),
          items: items,
        ),
      ],
    );
  }
}
