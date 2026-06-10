import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import '../../../core/theme/app_colors.dart';
import '../../../core/theme/app_text_styles.dart';
import '../../../core/widgets/app_button.dart';
import '../../../core/widgets/common_widgets.dart';
import '../application/chatbot_controller.dart';
import '../data/models/chat.dart';

/// First-run model picker — shown when the active conversation has no model.
///
/// Mirrors the Vue `ChatModelPicker.vue`.  Loads models from the controller
/// (which calls GET /models) and emits an `onConfirm(ChatModel)` callback.
class ChatModelPicker extends ConsumerStatefulWidget {
  const ChatModelPicker({
    super.key,
    required this.onConfirm,
  });

  final ValueChanged<ChatModel> onConfirm;

  @override
  ConsumerState<ChatModelPicker> createState() => _ChatModelPickerState();
}

class _ChatModelPickerState extends ConsumerState<ChatModelPicker> {
  String? _selectedId;

  @override
  void initState() {
    super.initState();
    // Kick model load; no-op if already loaded.
    Future.microtask(
        () => ref.read(chatbotProvider.notifier).loadModels());
  }

  @override
  Widget build(BuildContext context) {
    final st = ref.watch(chatbotProvider);
    final models = st.models;

    return SingleChildScrollView(
      padding: const EdgeInsets.all(20),
      child: Column(
        mainAxisSize: MainAxisSize.min,
        children: [
          // Bot icon
          Container(
            width: 56,
            height: 56,
            decoration: BoxDecoration(
              color: AppColors.primary.withValues(alpha: 0.15),
              borderRadius: BorderRadius.circular(16),
              border: Border.all(
                  color: AppColors.primary.withValues(alpha: 0.3)),
            ),
            child: Icon(Icons.smart_toy, color: AppColors.primary, size: 28),
          ),
          const SizedBox(height: 16),

          Text(
            'WELCOME',
            style: AppTextStyles.eyebrow.copyWith(color: AppColors.primary),
          ),
          const SizedBox(height: 4),
          Text(
            'Pick the model that powers me',
            style: AppTextStyles.h3.copyWith(color: AppColors.textHi),
            textAlign: TextAlign.center,
          ),
          const SizedBox(height: 8),
          Text(
            "I'll use this model for every reply in this conversation. "
            "You can swap it later in settings.",
            style: AppTextStyles.body.copyWith(color: AppColors.textDim),
            textAlign: TextAlign.center,
          ),
          const SizedBox(height: 24),

          // Loading / error / empty / list
          if (!st.modelsLoaded && models.isEmpty)
            const LoadingState(label: 'Loading models…')
          else if (st.modelsLoaded && models.isEmpty)
            Container(
              padding:
                  const EdgeInsets.symmetric(horizontal: 16, vertical: 12),
              decoration: BoxDecoration(
                color: AppColors.warning.withValues(alpha: 0.1),
                borderRadius: BorderRadius.circular(12),
                border: Border.all(
                    color: AppColors.warning.withValues(alpha: 0.3)),
              ),
              child: Text(
                'No models configured yet. Add one on the Models page.',
                style:
                    AppTextStyles.body.copyWith(color: AppColors.warning),
                textAlign: TextAlign.center,
              ),
            )
          else ...[
            Align(
              alignment: Alignment.centerLeft,
              child: Text(
                'MODEL',
                style: AppTextStyles.eyebrow
                    .copyWith(color: AppColors.textDim),
              ),
            ),
            const SizedBox(height: 6),
            // Model selection chips
            for (final m in models)
              _ModelTile(
                model: m,
                selected: _selectedId == m.id,
                onTap: () => setState(() => _selectedId = m.id),
              ),
          ],

          const SizedBox(height: 20),

          if (models.isNotEmpty)
            AppButton.primary(
              label: 'Start chatting',
              icon: Icons.check,
              busy: st.busy,
              onPressed: (_selectedId == null || st.busy)
                  ? null
                  : () {
                      final m = models.firstWhere(
                        (x) => x.id == _selectedId,
                        orElse: () => models.first,
                      );
                      widget.onConfirm(m);
                    },
            ),
        ],
      ),
    );
  }
}

class _ModelTile extends StatelessWidget {
  const _ModelTile({
    required this.model,
    required this.selected,
    required this.onTap,
  });
  final ChatModel model;
  final bool selected;
  final VoidCallback onTap;

  @override
  Widget build(BuildContext context) {
    return Padding(
      padding: const EdgeInsets.only(bottom: 6),
      child: GestureDetector(
        onTap: onTap,
        child: AnimatedContainer(
          duration: const Duration(milliseconds: 150),
          padding:
              const EdgeInsets.symmetric(horizontal: 12, vertical: 10),
          decoration: BoxDecoration(
            color: selected
                ? AppColors.primary.withValues(alpha: 0.12)
                : AppColors.surface.withValues(alpha: 0.5),
            borderRadius: BorderRadius.circular(10),
            border: Border.all(
              color: selected
                  ? AppColors.primary.withValues(alpha: 0.5)
                  : AppColors.border,
            ),
          ),
          child: Row(
            children: [
              Expanded(
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Text(
                      model.name,
                      style: AppTextStyles.bodyHi.copyWith(
                        color: selected
                            ? AppColors.primary
                            : AppColors.textHi,
                      ),
                    ),
                    if (model.provider.isNotEmpty ||
                        model.model.isNotEmpty)
                      Text(
                        '${model.provider} · ${model.model}'
                            .trim()
                            .replaceAll(RegExp(r'^·\s*|·\s*$'), ''),
                        style: AppTextStyles.micro
                            .copyWith(color: AppColors.textDim),
                      ),
                  ],
                ),
              ),
              if (selected)
                Icon(Icons.check_circle, color: AppColors.primary, size: 18),
            ],
          ),
        ),
      ),
    );
  }
}
