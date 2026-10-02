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

/// Minimal instance-creation form.
/// Instance ID: `^[a-z0-9_-]+$`
/// Cadence: 1min / 5min / 15min / 1hr chips.
class StepCreateInstance extends ConsumerStatefulWidget {
  const StepCreateInstance({super.key});

  @override
  ConsumerState<StepCreateInstance> createState() =>
      _StepCreateInstanceState();
}

const _cadences = ['1min', '5min', '15min', '1hr'];
// Backend enum values matching each cadence chip.
const _cadenceValues = ['1m', '5m', '15m', '1h'];

class _StepCreateInstanceState extends ConsumerState<StepCreateInstance> {
  final _idCtrl = TextEditingController();
  final _nameCtrl = TextEditingController();

  String _cadence = '5min';
  bool _busy = false;
  String? _message;
  bool _messageOk = false;
  String? _idError;

  final List<Map<String, dynamic>> _saved = [];

  static final _idRegex = RegExp(r'^[a-z0-9_-]+$');

  @override
  void dispose() {
    _idCtrl.dispose();
    _nameCtrl.dispose();
    super.dispose();
  }

  void _validateId(String v) {
    setState(() {
      if (v.isEmpty) {
        _idError = null;
      } else if (!_idRegex.hasMatch(v)) {
        _idError = 'Only lowercase letters, digits, - and _ allowed.';
      } else {
        _idError = null;
      }
    });
  }

  bool get _canSubmit =>
      _idCtrl.text.trim().isNotEmpty &&
      _nameCtrl.text.trim().isNotEmpty &&
      _idError == null;

  Future<void> _create() async {
    if (!_canSubmit) return;

    setState(() {
      _busy = true;
      _message = 'Creating instance…';
      _messageOk = false;
    });

    try {
      final client = ref.read(apiClientProvider);
      final cadenceIdx = _cadences.indexOf(_cadence);
      final cadenceVal =
          cadenceIdx >= 0 ? _cadenceValues[cadenceIdx] : '5m';

      final body = <String, dynamic>{
        'instance_id': _idCtrl.text.trim(),
        'name': _nameCtrl.text.trim(),
        'cadence': cadenceVal,
      };

      final data = await client.post<Map<String, dynamic>>(
        '/instances',
        body: body,
      );

      final name = (data['name'] ?? body['name']).toString();
      setState(() {
        _saved.add(data);
        _message = 'Instance "$name" created.';
        _messageOk = true;
        _busy = false;
        _idCtrl.clear();
        _nameCtrl.clear();
        _cadence = '5min';
        _idError = null;
      });

      ref.read(onboardingControllerProvider.notifier).updateCounts(
            instances:
                ref.read(onboardingControllerProvider).instanceCount + 1,
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
          Text('STEP 3 · CREATE AN INSTANCE', style: AppTextStyles.eyebrow),
          const SizedBox(height: 8),
          Text(
            'Spin up your first instance.',
            style: AppTextStyles.h2.copyWith(color: AppColors.textHi),
          ),
          const SizedBox(height: 8),
          Text(
            'An instance is the runtime that runs a strategy on a cadence '
            'and places orders through your linked brokerage.',
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
            for (final inst in _saved) ...[
              _SavedTile(instance: inst),
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
                // Instance ID with inline validation.
                OnboardingField(
                  label: 'Instance ID *',
                  hint: 'e.g. my-bot',
                  controller: _idCtrl,
                  enabled: !_busy,
                  onChanged: _validateId,
                  errorText: _idError,
                ),
                const SizedBox(height: 4),
                Text(
                  'Lowercase letters, digits, hyphens and underscores only.',
                  style: AppTextStyles.nano
                      .copyWith(color: AppColors.textFaint),
                ),
                const SizedBox(height: 12),
                OnboardingField(
                  label: 'Display Name *',
                  hint: 'e.g. My First Bot',
                  controller: _nameCtrl,
                  enabled: !_busy,
                  onChanged: (_) => setState(() {}),
                ),
                const SizedBox(height: 12),
                // Cadence chips.
                Text(
                  'Cadence',
                  style: AppTextStyles.nano.copyWith(
                    color: AppColors.textMuted,
                    fontWeight: FontWeight.w500,
                  ),
                ),
                const SizedBox(height: 8),
                Wrap(
                  spacing: 8,
                  children: _cadences.map((c) {
                    final selected = _cadence == c;
                    return GestureDetector(
                      onTap: _busy
                          ? null
                          : () => setState(() => _cadence = c),
                      child: AnimatedContainer(
                        duration: const Duration(milliseconds: 150),
                        padding: const EdgeInsets.symmetric(
                            horizontal: 14, vertical: 6),
                        decoration: BoxDecoration(
                          color: selected
                              ? AppColors.fill(AppColors.primary)
                              : AppColors.surface,
                          borderRadius: BorderRadius.circular(999),
                          border: Border.all(
                            color: selected
                                ? AppColors.primary
                                : AppColors.border,
                          ),
                        ),
                        child: Text(
                          c,
                          style: AppTextStyles.meta.copyWith(
                            color: selected
                                ? AppColors.primary
                                : AppColors.textMuted,
                            fontWeight: selected
                                ? FontWeight.w600
                                : FontWeight.w400,
                          ),
                        ),
                      ),
                    );
                  }).toList(),
                ),
                if (_message != null) ...[
                  const SizedBox(height: 10),
                  OnboardingMessageBanner(message: _message!, ok: _messageOk),
                ],
                const SizedBox(height: 14),
                Align(
                  alignment: Alignment.centerRight,
                  child: AppButton.primary(
                    label: _busy ? 'Creating…' : 'Create instance',
                    icon: symbol('rocket_launch'),
                    busy: _busy,
                    onPressed: _canSubmit ? _create : null,
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

class _SavedTile extends StatelessWidget {
  const _SavedTile({required this.instance});

  final Map<String, dynamic> instance;

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
          Icon(symbol('rocket_launch'), size: 18, color: AppColors.success),
          const SizedBox(width: 10),
          Expanded(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Text(
                  (instance['name'] ??
                          instance['instance_id'] ??
                          '')
                      .toString(),
                  style:
                      AppTextStyles.cardTitle.copyWith(color: AppColors.textHi),
                ),
                Text(
                  (instance['instance_id'] ?? '').toString(),
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
