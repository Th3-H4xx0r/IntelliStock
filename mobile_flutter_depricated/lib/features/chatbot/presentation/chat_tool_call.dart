import 'package:flutter/material.dart';
import 'dart:convert';
import '../../../core/theme/app_colors.dart';
import '../../../core/theme/app_text_styles.dart';
import '../../../core/widgets/app_button.dart';
import '../data/models/chat.dart';

/// Card shown when a message has `status == 'pending_confirmation'`.
///
/// Mirrors the Vue `ChatToolCall.vue` component:
///  • safe → primary/violet card — "Run tool"
///  • write → amber card — "This will change your workspace"
///  • destructive → red card — requires typed CONFIRM
class ChatToolCallCard extends StatefulWidget {
  const ChatToolCallCard({
    super.key,
    required this.message,
    required this.busy,
    required this.onApprove,
    required this.onDecline,
  });

  final ChatMessage message;
  final bool busy;
  final VoidCallback onApprove;
  final VoidCallback onDecline;

  @override
  State<ChatToolCallCard> createState() => _ChatToolCallCardState();
}

class _ChatToolCallCardState extends State<ChatToolCallCard> {
  bool _argsExpanded = false;
  final _confirmCtrl = TextEditingController();

  @override
  void dispose() {
    _confirmCtrl.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    final tool = widget.message.pendingTool;
    final safety = tool?.safety ?? 'write';

    final Color borderColor;
    final Color bgColor;
    final Color textColor;
    final String tierLabel;
    final IconData tierIcon;

    switch (safety) {
      case 'safe':
        borderColor = AppColors.primary.withValues(alpha: 0.3);
        bgColor = AppColors.primary.withValues(alpha: 0.05);
        textColor = AppColors.primary;
        tierLabel = 'Run tool';
        tierIcon = Icons.play_circle_outline;
      case 'destructive':
        borderColor = AppColors.danger.withValues(alpha: 0.4);
        bgColor = AppColors.danger.withValues(alpha: 0.1);
        textColor = AppColors.danger;
        tierLabel = 'DESTRUCTIVE — confirm carefully';
        tierIcon = Icons.warning_amber_outlined;
      default: // 'write'
        borderColor = AppColors.warning.withValues(alpha: 0.3);
        bgColor = AppColors.warning.withValues(alpha: 0.05);
        textColor = AppColors.warning;
        tierLabel = 'This will change your workspace';
        tierIcon = Icons.play_circle_outline;
    }

    final requiresTyped = safety == 'destructive';
    final typedOk = !requiresTyped || _confirmCtrl.text == 'CONFIRM';

    String argsPreview = '';
    if (tool != null && tool.arguments.isNotEmpty) {
      try {
        argsPreview =
            const JsonEncoder.withIndent('  ').convert(tool.arguments);
      } catch (_) {}
    }

    return Container(
      constraints: const BoxConstraints(maxWidth: 360),
      padding: const EdgeInsets.all(14),
      decoration: BoxDecoration(
        color: bgColor,
        borderRadius: BorderRadius.circular(16),
        border: Border.all(color: borderColor),
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          // Header
          Row(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Icon(tierIcon, color: textColor, size: 18),
              const SizedBox(width: 8),
              Expanded(
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Text(
                      tierLabel.toUpperCase(),
                      style: AppTextStyles.eyebrow
                          .copyWith(color: textColor.withValues(alpha: 0.7)),
                    ),
                    const SizedBox(height: 2),
                    Text(
                      tool?.name ?? 'tool',
                      style: AppTextStyles.cardTitle
                          .copyWith(color: textColor),
                    ),
                    if (tool?.description != null) ...[
                      const SizedBox(height: 4),
                      Text(
                        tool!.description!,
                        style: AppTextStyles.body
                            .copyWith(color: textColor.withValues(alpha: 0.8)),
                      ),
                    ],
                  ],
                ),
              ),
            ],
          ),

          // Collapsible args
          if (argsPreview.isNotEmpty && argsPreview != '{}') ...[
            const SizedBox(height: 10),
            GestureDetector(
              onTap: () =>
                  setState(() => _argsExpanded = !_argsExpanded),
              child: Row(
                children: [
                  Icon(
                    _argsExpanded
                        ? Icons.expand_less
                        : Icons.expand_more,
                    size: 14,
                    color: textColor.withValues(alpha: 0.6),
                  ),
                  const SizedBox(width: 4),
                  Text(
                    'Arguments',
                    style: AppTextStyles.micro.copyWith(
                      color: textColor.withValues(alpha: 0.6),
                    ),
                  ),
                ],
              ),
            ),
            if (_argsExpanded) ...[
              const SizedBox(height: 6),
              Container(
                width: double.infinity,
                padding: const EdgeInsets.all(8),
                decoration: BoxDecoration(
                  color: Colors.black.withValues(alpha: 0.3),
                  borderRadius: BorderRadius.circular(8),
                ),
                child: SingleChildScrollView(
                  scrollDirection: Axis.horizontal,
                  child: Text(
                    argsPreview,
                    style: AppTextStyles.mono(10, color: AppColors.textMd),
                  ),
                ),
              ),
            ],
          ],

          // Typed confirm for destructive
          if (requiresTyped) ...[
            const SizedBox(height: 12),
            Text(
              'Type CONFIRM to proceed',
              style: AppTextStyles.micro.copyWith(
                color: textColor.withValues(alpha: 0.8),
                fontWeight: FontWeight.w600,
                letterSpacing: 0.5,
              ),
            ),
            const SizedBox(height: 6),
            TextField(
              controller: _confirmCtrl,
              onChanged: (_) => setState(() {}),
              autocorrect: false,
              style: AppTextStyles.mono(12, color: AppColors.textHi),
              decoration: InputDecoration(
                isDense: true,
                contentPadding: const EdgeInsets.symmetric(
                    horizontal: 10, vertical: 8),
                enabledBorder: OutlineInputBorder(
                  borderRadius: BorderRadius.circular(8),
                  borderSide:
                      BorderSide(color: textColor.withValues(alpha: 0.3)),
                ),
                focusedBorder: OutlineInputBorder(
                  borderRadius: BorderRadius.circular(8),
                  borderSide: BorderSide(color: textColor),
                ),
                fillColor: Colors.black.withValues(alpha: 0.4),
                filled: true,
              ),
            ),
          ],

          // Actions
          const SizedBox(height: 12),
          Row(
            mainAxisAlignment: MainAxisAlignment.end,
            children: [
              AppButton.semantic(
                label: 'Decline',
                color: AppColors.textMuted,
                onPressed: widget.busy ? null : widget.onDecline,
              ),
              const SizedBox(width: 8),
              AppButton.semantic(
                label: 'Approve & run',
                icon: Icons.check,
                color: textColor,
                busy: widget.busy,
                onPressed: (widget.busy || !typedOk)
                    ? null
                    : widget.onApprove,
              ),
            ],
          ),
        ],
      ),
    );
  }
}
