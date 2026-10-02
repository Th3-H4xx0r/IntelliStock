import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import '../../../../core/network/api_client.dart';
import '../../../../core/network/api_error.dart';
import '../../../../core/theme/app_colors.dart';
import '../../../../core/theme/app_text_styles.dart';
import '../../../../core/widgets/app_button.dart';
import '../../../../core/widgets/glass_card.dart';
import '../../../../core/widgets/material_symbols.dart';
import '../../application/onboarding_controller.dart';
import 'onboarding_form_widgets.dart';

/// Minimal inline model-add form.
///
/// The full LlmConfigForm is owned by another agent — this is a lean, self-
/// contained version that covers the most-common fields.
class StepAddModel extends ConsumerStatefulWidget {
  const StepAddModel({super.key});

  @override
  ConsumerState<StepAddModel> createState() => _StepAddModelState();
}

const _providers = [
  'gemini',
  'openai',
  'azure',
  'nvidia',
  'ollama',
  'bedrock',
  'claude-cli',
  'codex-cli',
];

class _StepAddModelState extends ConsumerState<StepAddModel> {
  final _nameCtrl = TextEditingController();
  final _modelCtrl = TextEditingController();
  final _apiKeyCtrl = TextEditingController();

  String _selectedProvider = 'gemini';
  bool _busy = false;
  String? _message;
  bool _messageOk = false;

  // Models saved this session.
  final List<Map<String, dynamic>> _saved = [];

  @override
  void dispose() {
    _nameCtrl.dispose();
    _modelCtrl.dispose();
    _apiKeyCtrl.dispose();
    super.dispose();
  }

  bool get _canSubmit =>
      _nameCtrl.text.trim().isNotEmpty && _modelCtrl.text.trim().isNotEmpty;

  Future<void> _testAndSave() async {
    if (!_canSubmit) {
      setState(() {
        _message = 'Name and model are required.';
        _messageOk = false;
      });
      return;
    }
    setState(() {
      _busy = true;
      _message = 'Saving model…';
      _messageOk = false;
    });

    try {
      final client = ref.read(apiClientProvider);
      final body = <String, dynamic>{
        'name': _nameCtrl.text.trim(),
        'provider': _selectedProvider,
        'model': _modelCtrl.text.trim(),
      };
      final key = _apiKeyCtrl.text.trim();
      if (key.isNotEmpty) body['api_key'] = key;

      final data = await client.post<Map<String, dynamic>>(
        '/models',
        body: body,
      );

      final name = (data['name'] ?? body['name']).toString();
      setState(() {
        _saved.add(data);
        _message = 'Model "$name" saved.';
        _messageOk = true;
        _busy = false;
        _nameCtrl.clear();
        _modelCtrl.clear();
        _apiKeyCtrl.clear();
        _selectedProvider = 'gemini';
      });

      // Update counts in the controller.
      ref.read(onboardingControllerProvider.notifier).updateCounts(
            models: ref.read(onboardingControllerProvider).modelCount + 1,
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
          Text('STEP 1 · ADD A MODEL', style: AppTextStyles.eyebrow),
          const SizedBox(height: 8),
          Text(
            'Pick the brain that powers your trades.',
            style: AppTextStyles.h2.copyWith(color: AppColors.textHi),
          ),
          const SizedBox(height: 8),
          Text(
            'Drop in an API key for any supported LLM provider. '
            "You can add more later — Gemini's free tier works great as a starter.",
            style: AppTextStyles.body.copyWith(color: AppColors.textMuted),
          ),
          // Show saved models.
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
            for (final m in _saved) ...[
              _SavedModelTile(model: m),
              const SizedBox(height: 6),
            ],
          ],
          const SizedBox(height: 20),
          // Form card.
          GlassCard(
            padding: const EdgeInsets.all(16),
            borderColor: AppColors.border,
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                OnboardingField(
                  label: 'Model Name *',
                  hint: 'e.g. gemini-flash-prod',
                  controller: _nameCtrl,
                  enabled: !_busy,
                  onChanged: (_) => setState(() {}),
                ),
                const SizedBox(height: 12),
                _ProviderDropdown(
                  value: _selectedProvider,
                  enabled: !_busy,
                  onChanged: (v) =>
                      setState(() => _selectedProvider = v ?? 'gemini'),
                ),
                const SizedBox(height: 12),
                OnboardingField(
                  label: 'Model ID *',
                  hint: 'e.g. gemini-2.5-flash',
                  controller: _modelCtrl,
                  enabled: !_busy,
                  onChanged: (_) => setState(() {}),
                ),
                const SizedBox(height: 12),
                OnboardingField(
                  label: 'API Key',
                  hint: 'sk-…',
                  controller: _apiKeyCtrl,
                  obscure: true,
                  enabled: !_busy,
                ),
                if (_message != null) ...[
                  const SizedBox(height: 10),
                  OnboardingMessageBanner(message: _message!, ok: _messageOk),
                ],
                const SizedBox(height: 14),
                Align(
                  alignment: Alignment.centerRight,
                  child: AppButton.primary(
                    label: _busy ? 'Saving…' : 'Test & save',
                    icon: symbol('bolt'),
                    busy: _busy,
                    onPressed: _canSubmit ? _testAndSave : null,
                  ),
                ),
              ],
            ),
          ),
          const SizedBox(height: 8),
        ],
      ),
    );
  }
}

class _SavedModelTile extends StatelessWidget {
  const _SavedModelTile({required this.model});

  final Map<String, dynamic> model;

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
          Icon(symbol('memory'), size: 18, color: AppColors.success),
          const SizedBox(width: 10),
          Expanded(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Text(
                  (model['name'] ?? '').toString(),
                  style:
                      AppTextStyles.cardTitle.copyWith(color: AppColors.textHi),
                ),
                Text(
                  '${model['provider']} · ${model['model']}',
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

class _ProviderDropdown extends StatelessWidget {
  const _ProviderDropdown({
    required this.value,
    required this.onChanged,
    this.enabled = true,
  });

  final String value;
  final ValueChanged<String?> onChanged;
  final bool enabled;

  @override
  Widget build(BuildContext context) {
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Text(
          'Provider',
          style: AppTextStyles.nano.copyWith(
            color: AppColors.textMuted,
            fontWeight: FontWeight.w500,
          ),
        ),
        const SizedBox(height: 6),
        DropdownButtonFormField<String>(
          initialValue: value,
          onChanged: enabled ? onChanged : null,
          dropdownColor: AppColors.surface,
          style: AppTextStyles.body.copyWith(color: AppColors.textHi),
          decoration: InputDecoration(
            contentPadding:
                const EdgeInsets.symmetric(horizontal: 12, vertical: 10),
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
              borderSide:
                  const BorderSide(color: AppColors.primary, width: 1.5),
            ),
          ),
          items: _providers
              .map((p) => DropdownMenuItem(
                    value: p,
                    child: Text(p,
                        style: AppTextStyles.body
                            .copyWith(color: AppColors.textHi)),
                  ))
              .toList(),
        ),
      ],
    );
  }
}
