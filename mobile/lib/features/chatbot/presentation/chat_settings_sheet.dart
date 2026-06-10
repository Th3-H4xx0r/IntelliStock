import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../../core/theme/app_colors.dart';
import '../../../core/theme/app_text_styles.dart';
import '../../../core/widgets/common_widgets.dart';
import '../../../core/widgets/confirm_dialog.dart';
import '../application/chatbot_controller.dart';
import '../data/models/chat.dart';

/// Opens the chat settings sheet. Mirrors the web `ChatSettings.vue`:
/// model selection, the auto-run-safe-tools toggle, conversation actions
/// (new / delete), and an informational list of the tools the assistant can
/// use, grouped by safety.
Future<void> showChatSettings(BuildContext context) {
  return showModalBottomSheet<void>(
    context: context,
    isScrollControlled: true,
    backgroundColor: Colors.transparent,
    builder: (_) => const _ChatSettingsSheet(),
  );
}

class _ChatSettingsSheet extends ConsumerStatefulWidget {
  const _ChatSettingsSheet();

  @override
  ConsumerState<_ChatSettingsSheet> createState() => _ChatSettingsSheetState();
}

class _ChatSettingsSheetState extends ConsumerState<_ChatSettingsSheet> {
  @override
  void initState() {
    super.initState();
    // Load the model list (no-op if already loaded).
    Future.microtask(() => ref.read(chatbotProvider.notifier).loadModels());
  }

  @override
  Widget build(BuildContext context) {
    final st = ref.watch(chatbotProvider);
    final notifier = ref.read(chatbotProvider.notifier);
    final convo = st.activeConversation;
    final mq = MediaQuery.of(context);

    return Container(
      constraints: BoxConstraints(maxHeight: mq.size.height * 0.85),
      decoration: const BoxDecoration(
        color: Color(0xFF0a0716),
        borderRadius: BorderRadius.vertical(top: Radius.circular(20)),
        border: Border(
          top: BorderSide(color: AppColors.border),
          left: BorderSide(color: AppColors.border),
          right: BorderSide(color: AppColors.border),
        ),
      ),
      child: SafeArea(
        top: false,
        child: Column(
          mainAxisSize: MainAxisSize.min,
          children: [
            // Grab handle.
            Container(
              width: 36,
              height: 4,
              margin: const EdgeInsets.only(top: 10, bottom: 6),
              decoration: BoxDecoration(
                color: AppColors.border,
                borderRadius: BorderRadius.circular(2),
              ),
            ),
            // Title row.
            Padding(
              padding: const EdgeInsets.fromLTRB(16, 4, 8, 8),
              child: Row(
                children: [
                  Icon(Icons.settings, size: 18, color: AppColors.primary),
                  const SizedBox(width: 8),
                  Text(
                    'Chat settings',
                    style:
                        AppTextStyles.bodyHi.copyWith(color: AppColors.textHi),
                  ),
                  const Spacer(),
                  IconButton(
                    icon: Icon(Icons.close, size: 20, color: AppColors.textMuted),
                    onPressed: () => Navigator.of(context).pop(),
                    tooltip: 'Close',
                  ),
                ],
              ),
            ),
            const Divider(height: 1, color: AppColors.border),
            Flexible(
              child: ListView(
                padding: const EdgeInsets.fromLTRB(16, 16, 16, 16),
                children: [
                  _ModelSection(st: st, convo: convo, notifier: notifier),
                  const SizedBox(height: 20),
                  _AutoConfirmSection(convo: convo, notifier: notifier),
                  const SizedBox(height: 20),
                  _ConversationSection(convo: convo, notifier: notifier),
                  const SizedBox(height: 20),
                  _ToolsSection(toolCatalog: st.toolCatalog),
                ],
              ),
            ),
          ],
        ),
      ),
    );
  }
}

// ── Section header ──────────────────────────────────────────────────────────

class _SectionLabel extends StatelessWidget {
  const _SectionLabel(this.text);
  final String text;

  @override
  Widget build(BuildContext context) => Padding(
        padding: const EdgeInsets.only(bottom: 8),
        child: Text(
          text,
          style: AppTextStyles.eyebrow.copyWith(color: AppColors.textDim),
        ),
      );
}

// ── Model ─────────────────────────────────────────────────────────────────────

class _ModelSection extends StatelessWidget {
  const _ModelSection({
    required this.st,
    required this.convo,
    required this.notifier,
  });

  final ChatbotState st;
  final Conversation? convo;
  final ChatbotNotifier notifier;

  @override
  Widget build(BuildContext context) {
    final models = st.models;
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        const _SectionLabel('MODEL'),
        if (!st.modelsLoaded && models.isEmpty)
          const LoadingState(label: 'Loading models…')
        else if (st.modelsLoaded && models.isEmpty)
          Container(
            padding: const EdgeInsets.symmetric(horizontal: 14, vertical: 12),
            decoration: BoxDecoration(
              color: AppColors.warning.withValues(alpha: 0.1),
              borderRadius: BorderRadius.circular(12),
              border:
                  Border.all(color: AppColors.warning.withValues(alpha: 0.3)),
            ),
            child: Text(
              'No models configured yet. Add one on the Models page.',
              style: AppTextStyles.body.copyWith(color: AppColors.warning),
            ),
          )
        else
          for (final m in models)
            _ModelRow(
              model: m,
              selected: convo?.modelId == m.id,
              busy: st.busy,
              onTap: convo?.modelId == m.id || st.busy
                  ? null
                  : () => notifier.setModel(m.id),
            ),
      ],
    );
  }
}

class _ModelRow extends StatelessWidget {
  const _ModelRow({
    required this.model,
    required this.selected,
    required this.busy,
    required this.onTap,
  });

  final ChatModel model;
  final bool selected;
  final bool busy;
  final VoidCallback? onTap;

  @override
  Widget build(BuildContext context) {
    final sub = '${model.provider} · ${model.model}'
        .trim()
        .replaceAll(RegExp(r'^·\s*|·\s*$'), '');
    return Padding(
      padding: const EdgeInsets.only(bottom: 6),
      child: GestureDetector(
        onTap: onTap,
        child: AnimatedContainer(
          duration: const Duration(milliseconds: 150),
          padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 10),
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
                        color: selected ? AppColors.primary : AppColors.textHi,
                      ),
                    ),
                    if (sub.isNotEmpty)
                      Text(
                        sub,
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

// ── Auto-confirm safe tools ───────────────────────────────────────────────────

class _AutoConfirmSection extends StatelessWidget {
  const _AutoConfirmSection({required this.convo, required this.notifier});

  final Conversation? convo;
  final ChatbotNotifier notifier;

  @override
  Widget build(BuildContext context) {
    final enabled = convo != null;
    final value = convo?.autoConfirmSafeTools ?? false;
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        const _SectionLabel('TOOLS'),
        Row(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Expanded(
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Text(
                    'Auto-run safe (read-only) tools',
                    style:
                        AppTextStyles.body.copyWith(color: AppColors.textMd),
                  ),
                  const SizedBox(height: 2),
                  Text(
                    'When on, the assistant can call read-only tools like '
                    'list_instances without asking. Mutating tools always '
                    'require approval.',
                    style: AppTextStyles.nano.copyWith(
                      color: AppColors.textFaint,
                      height: 1.4,
                    ),
                  ),
                ],
              ),
            ),
            const SizedBox(width: 8),
            Switch.adaptive(
              value: value,
              activeThumbColor: AppColors.primary,
              onChanged: enabled
                  ? (v) => notifier.setAutoConfirmSafe(value: v)
                  : null,
            ),
          ],
        ),
      ],
    );
  }
}

// ── Conversation actions ──────────────────────────────────────────────────────

class _ConversationSection extends StatelessWidget {
  const _ConversationSection({required this.convo, required this.notifier});

  final Conversation? convo;
  final ChatbotNotifier notifier;

  @override
  Widget build(BuildContext context) {
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        const _SectionLabel('CONVERSATION'),
        Row(
          children: [
            Expanded(
              child: _ActionButton(
                icon: Icons.add,
                label: 'New conversation',
                color: AppColors.primary,
                onTap: () async {
                  await notifier.startNewConversation();
                  if (context.mounted) Navigator.of(context).pop();
                },
              ),
            ),
            const SizedBox(width: 10),
            Expanded(
              child: _ActionButton(
                icon: Icons.delete_outline,
                label: 'Delete',
                color: AppColors.danger,
                onTap: convo == null
                    ? null
                    : () async {
                        final ok = await showConfirmDialog(
                          context,
                          title: 'Delete conversation',
                          body:
                              'This permanently deletes this conversation. '
                              'This cannot be undone.',
                          confirmLabel: 'Delete',
                          confirmColor: AppColors.danger,
                          icon: Icons.delete_outline,
                        );
                        if (!ok) return;
                        await notifier.deleteConversation();
                        if (context.mounted) Navigator.of(context).pop();
                      },
              ),
            ),
          ],
        ),
      ],
    );
  }
}

class _ActionButton extends StatelessWidget {
  const _ActionButton({
    required this.icon,
    required this.label,
    required this.color,
    required this.onTap,
  });

  final IconData icon;
  final String label;
  final Color color;
  final VoidCallback? onTap;

  @override
  Widget build(BuildContext context) {
    final disabled = onTap == null;
    final c = disabled ? AppColors.textFaint : color;
    return GestureDetector(
      onTap: onTap,
      child: Container(
        padding: const EdgeInsets.symmetric(vertical: 11),
        decoration: BoxDecoration(
          color: c.withValues(alpha: 0.10),
          borderRadius: BorderRadius.circular(10),
          border: Border.all(color: c.withValues(alpha: 0.35)),
        ),
        child: Row(
          mainAxisAlignment: MainAxisAlignment.center,
          children: [
            Icon(icon, size: 16, color: c),
            const SizedBox(width: 6),
            Text(
              label,
              style: AppTextStyles.micro
                  .copyWith(color: c, fontWeight: FontWeight.w600),
            ),
          ],
        ),
      ),
    );
  }
}

// ── Tool catalog (informational) ──────────────────────────────────────────────

class _ToolsSection extends StatelessWidget {
  const _ToolsSection({required this.toolCatalog});

  final List<Map<String, dynamic>> toolCatalog;

  @override
  Widget build(BuildContext context) {
    if (toolCatalog.isEmpty) return const SizedBox.shrink();

    String nameOf(Map<String, dynamic> t) =>
        (t['name'] ?? t['function'] ?? t['tool'] ?? '').toString();
    String safetyOf(Map<String, dynamic> t) =>
        (t['safety'] ?? 'write').toString();

    final safe = <String>[];
    final confirm = <String>[];
    final destructive = <String>[];
    for (final t in toolCatalog) {
      final n = nameOf(t);
      if (n.isEmpty) continue;
      switch (safetyOf(t)) {
        case 'safe':
          safe.add(n);
        case 'destructive':
          destructive.add(n);
        default:
          confirm.add(n);
      }
    }

    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        const _SectionLabel('TOOLS THE ASSISTANT CAN USE'),
        if (safe.isNotEmpty)
          _ToolGroup(
            title: 'Read-only · auto-runnable',
            color: AppColors.success,
            tools: safe,
          ),
        if (confirm.isNotEmpty)
          _ToolGroup(
            title: 'Confirm to run',
            color: AppColors.warning,
            tools: confirm,
          ),
        if (destructive.isNotEmpty)
          _ToolGroup(
            title: 'Destructive · always confirm',
            color: AppColors.danger,
            tools: destructive,
          ),
      ],
    );
  }
}

class _ToolGroup extends StatelessWidget {
  const _ToolGroup({
    required this.title,
    required this.color,
    required this.tools,
  });

  final String title;
  final Color color;
  final List<String> tools;

  @override
  Widget build(BuildContext context) {
    return Theme(
      data: Theme.of(context).copyWith(dividerColor: Colors.transparent),
      child: ExpansionTile(
        tilePadding: EdgeInsets.zero,
        childrenPadding: const EdgeInsets.only(left: 16, bottom: 8),
        expandedCrossAxisAlignment: CrossAxisAlignment.start,
        dense: true,
        leading: Container(
          width: 8,
          height: 8,
          decoration: BoxDecoration(color: color, shape: BoxShape.circle),
        ),
        title: Text(
          '$title (${tools.length})',
          style: AppTextStyles.micro.copyWith(color: AppColors.textMd),
        ),
        iconColor: AppColors.textDim,
        collapsedIconColor: AppColors.textDim,
        children: [
          Wrap(
            spacing: 6,
            runSpacing: 6,
            children: [
              for (final t in tools)
                Container(
                  padding:
                      const EdgeInsets.symmetric(horizontal: 8, vertical: 3),
                  decoration: BoxDecoration(
                    color: AppColors.surface.withValues(alpha: 0.6),
                    borderRadius: BorderRadius.circular(6),
                    border: Border.all(color: AppColors.border),
                  ),
                  child: Text(
                    t,
                    style: AppTextStyles.nano.copyWith(
                      color: AppColors.textDim,
                      fontFamily: 'monospace',
                    ),
                  ),
                ),
            ],
          ),
        ],
      ),
    );
  }
}
