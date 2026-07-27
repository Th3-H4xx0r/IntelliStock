import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import '../../../../core/network/api_client.dart';
import '../../../../core/network/api_error.dart';
import '../../../../core/theme/app_colors.dart';
import '../../../../core/theme/app_text_styles.dart';
import '../../../../core/widgets/app_button.dart';
import '../../../../core/widgets/app_toggle.dart';
import '../../../../core/widgets/glass_card.dart';
import '../../../../core/widgets/material_symbols.dart';
import '../../application/onboarding_controller.dart';
import 'onboarding_form_widgets.dart';

/// Minimal inline Alpaca brokerage-link form.
class StepLinkBrokerage extends ConsumerStatefulWidget {
  const StepLinkBrokerage({super.key});

  @override
  ConsumerState<StepLinkBrokerage> createState() => _StepLinkBrokerageState();
}

class _StepLinkBrokerageState extends ConsumerState<StepLinkBrokerage> {
  final _alpacaNameCtrl = TextEditingController();
  final _alpacaKeyCtrl = TextEditingController();
  final _alpacaSecretCtrl = TextEditingController();
  bool _alpacaPaper = true;

  bool _busy = false;
  String? _message;
  bool _messageOk = false;

  final List<Map<String, dynamic>> _saved = [];

  @override
  void dispose() {
    _alpacaNameCtrl.dispose();
    _alpacaKeyCtrl.dispose();
    _alpacaSecretCtrl.dispose();
    super.dispose();
  }

  Future<void> _save() async {
    if (_alpacaNameCtrl.text.trim().isEmpty ||
        _alpacaKeyCtrl.text.trim().isEmpty ||
        _alpacaSecretCtrl.text.trim().isEmpty) {
      setState(() {
        _message = 'Name, API key, and secret are required.';
        _messageOk = false;
      });
      return;
    }

    setState(() {
      _busy = true;
      _message = 'Saving brokerage…';
      _messageOk = false;
    });

    try {
      final client = ref.read(apiClientProvider);
      final Map<String, dynamic> body = {
        'account_name': _alpacaNameCtrl.text.trim(),
        'brokerage_type': 'alpaca',
        'api_key': _alpacaKeyCtrl.text.trim(),
        'api_secret': _alpacaSecretCtrl.text.trim(),
        'paper': _alpacaPaper,
      };

      final data = await client.post<Map<String, dynamic>>(
        '/brokerages',
        body: body,
      );

      final name = (data['account_name'] ?? body['account_name']).toString();
      setState(() {
        _saved.add(data);
        _message = 'Brokerage "$name" linked.';
        _messageOk = true;
        _busy = false;
        _alpacaNameCtrl.clear();
        _alpacaKeyCtrl.clear();
        _alpacaSecretCtrl.clear();
        _alpacaPaper = true;
      });

      ref
          .read(onboardingControllerProvider.notifier)
          .updateCounts(
            brokerages:
                ref.read(onboardingControllerProvider).brokerageCount + 1,
          );
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
          Text('STEP 2 · LINK A BROKERAGE', style: AppTextStyles.eyebrow),
          const SizedBox(height: 8),
          Text(
            'Connect your trading account.',
            style: AppTextStyles.h2.copyWith(color: AppColors.textHi),
          ),
          const SizedBox(height: 8),
          Text(
            'Connect Alpaca in paper mode first to test without real money.',
            style: AppTextStyles.body.copyWith(color: AppColors.textMuted),
          ),
          if (_saved.isNotEmpty) ...[
            const SizedBox(height: 16),
            Text(
              'SAVED THIS SESSION',
              style: AppTextStyles.nano.copyWith(
                color: AppColors.textDim,
                fontWeight: FontWeight.w700,
                letterSpacing: 1.2,
              ),
            ),
            const SizedBox(height: 6),
            for (final b in _saved) ...[
              _SavedTile(brokerage: b),
              const SizedBox(height: 6),
            ],
          ],
          const SizedBox(height: 20),
          GlassCard(
            padding: const EdgeInsets.all(16),
            borderColor: AppColors.border,
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                _AlpacaForm(
                  nameCtrl: _alpacaNameCtrl,
                  keyCtrl: _alpacaKeyCtrl,
                  secretCtrl: _alpacaSecretCtrl,
                  paper: _alpacaPaper,
                  busy: _busy,
                  onPaperChanged: (v) => setState(() => _alpacaPaper = v),
                ),
                if (_message != null) ...[
                  const SizedBox(height: 10),
                  OnboardingMessageBanner(message: _message!, ok: _messageOk),
                ],
                const SizedBox(height: 14),
                Align(
                  alignment: Alignment.centerRight,
                  child: AppButton.primary(
                    label: _busy ? 'Saving…' : 'Link brokerage',
                    icon: symbol('link'),
                    busy: _busy,
                    onPressed: _save,
                  ),
                ),
              ],
            ),
          ),
        ],
      ),
    );
  }
}

class _AlpacaForm extends StatelessWidget {
  const _AlpacaForm({
    required this.nameCtrl,
    required this.keyCtrl,
    required this.secretCtrl,
    required this.paper,
    required this.busy,
    required this.onPaperChanged,
  });

  final TextEditingController nameCtrl;
  final TextEditingController keyCtrl;
  final TextEditingController secretCtrl;
  final bool paper;
  final bool busy;
  final ValueChanged<bool> onPaperChanged;

  @override
  Widget build(BuildContext context) {
    return Column(
      children: [
        OnboardingField(
          label: 'Account Name *',
          hint: 'e.g. alpaca-paper',
          controller: nameCtrl,
          enabled: !busy,
        ),
        const SizedBox(height: 12),
        OnboardingField(
          label: 'API Key *',
          hint: 'PK…',
          controller: keyCtrl,
          enabled: !busy,
        ),
        const SizedBox(height: 12),
        OnboardingField(
          label: 'API Secret *',
          hint: 'Secret…',
          controller: secretCtrl,
          obscure: true,
          enabled: !busy,
        ),
        const SizedBox(height: 12),
        Row(
          mainAxisAlignment: MainAxisAlignment.spaceBetween,
          children: [
            Text(
              'Paper mode',
              style: AppTextStyles.body.copyWith(color: AppColors.textMd),
            ),
            AppToggle(value: paper, onChanged: busy ? null : onPaperChanged),
          ],
        ),
      ],
    );
  }
}

class _SavedTile extends StatelessWidget {
  const _SavedTile({required this.brokerage});

  final Map<String, dynamic> brokerage;

  @override
  Widget build(BuildContext context) {
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 10),
      decoration: BoxDecoration(
        color: AppColors.fill(AppColors.success),
        borderRadius: BorderRadius.circular(8),
        border: Border.all(color: AppColors.stroke(AppColors.success)),
      ),
      child: Row(
        children: [
          Icon(symbol('account_balance'), size: 18, color: AppColors.success),
          const SizedBox(width: 10),
          Expanded(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Text(
                  (brokerage['account_name'] ?? '').toString(),
                  style: AppTextStyles.cardTitle.copyWith(
                    color: AppColors.textHi,
                  ),
                ),
                Text(
                  (brokerage['brokerage_type'] ?? '').toString(),
                  style: AppTextStyles.nano.copyWith(color: AppColors.textDim),
                ),
              ],
            ),
          ),
        ],
      ),
    );
  }
}
