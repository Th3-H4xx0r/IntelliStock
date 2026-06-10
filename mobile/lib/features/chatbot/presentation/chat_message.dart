import 'package:flutter/material.dart';
import '../../../core/theme/app_colors.dart';
import '../../../core/theme/app_text_styles.dart';
import '../data/models/chat.dart';
import 'chat_rich_block.dart';

/// Renders a single [ChatMessage] as a chat bubble.
///
/// User messages → right-aligned violet bubble.
/// Assistant messages → left-aligned dark-surface bubble with optional
///   rich blocks rendered below the text.
/// Tool messages → left-aligned monospace dim result snippet.
///
/// Messages with `status == 'pending_confirmation'` are NOT rendered here;
/// the parent list routes those to [ChatToolCallCard] instead.
class ChatMessageBubble extends StatelessWidget {
  const ChatMessageBubble({super.key, required this.message});

  final ChatMessage message;

  bool get _isUser => message.role == 'user';
  bool get _isTool => message.role == 'tool';
  bool get _isAssistant => message.role == 'assistant';

  @override
  Widget build(BuildContext context) {
    // Filter out navigate blocks — the dock handles routing from those.
    final visibleBlocks = message.blocks
        .where((b) => (b['type'] ?? '') != 'navigate')
        .toList();

    return Align(
      alignment: _isUser ? Alignment.centerRight : Alignment.centerLeft,
      child: ConstrainedBox(
        constraints: BoxConstraints(
          maxWidth: MediaQuery.of(context).size.width * 0.82,
        ),
        child: Column(
          crossAxisAlignment:
              _isUser ? CrossAxisAlignment.end : CrossAxisAlignment.start,
          children: [
            // Tool-call chips on assistant messages
            if (_isAssistant && message.toolCalls.isNotEmpty)
              Wrap(
                spacing: 6,
                runSpacing: 4,
                children: [
                  for (final tc in message.toolCalls)
                    _ToolChip(name: tc.name),
                ],
              ),

            // Main bubble
            if (message.content != null && message.content!.isNotEmpty ||
                (visibleBlocks.isEmpty && message.toolCalls.isEmpty))
              _Bubble(message: message, isUser: _isUser, isTool: _isTool),

            // Rich blocks
            for (final b in visibleBlocks)
              Padding(
                padding: const EdgeInsets.only(top: 6),
                child: ChatRichBlock(block: b),
              ),

            // Timestamp
            if (message.createdAt != null && !_isTool)
              Padding(
                padding: const EdgeInsets.only(top: 3, left: 2, right: 2),
                child: Text(
                  _fmtTime(message.createdAt!),
                  style: AppTextStyles.nano
                      .copyWith(color: AppColors.textFaint),
                ),
              ),
          ],
        ),
      ),
    );
  }

  String _fmtTime(DateTime dt) {
    final h = dt.hour.toString().padLeft(2, '0');
    final m = dt.minute.toString().padLeft(2, '0');
    return '$h:$m';
  }
}

class _Bubble extends StatelessWidget {
  const _Bubble({
    required this.message,
    required this.isUser,
    required this.isTool,
  });

  final ChatMessage message;
  final bool isUser;
  final bool isTool;

  @override
  Widget build(BuildContext context) {
    final content = message.content ?? '';

    Color bg;
    Color textColor;
    BorderRadius radius;

    if (isUser) {
      bg = AppColors.primary;
      textColor = AppColors.onPrimary;
      radius = const BorderRadius.only(
        topLeft: Radius.circular(18),
        topRight: Radius.circular(18),
        bottomLeft: Radius.circular(18),
        bottomRight: Radius.circular(4),
      );
    } else if (isTool) {
      bg = AppColors.surface.withValues(alpha: 0.4);
      textColor = AppColors.textDim;
      radius = const BorderRadius.only(
        topLeft: Radius.circular(4),
        topRight: Radius.circular(18),
        bottomLeft: Radius.circular(18),
        bottomRight: Radius.circular(18),
      );
    } else {
      bg = AppColors.surface.withValues(alpha: 0.7);
      textColor = AppColors.textMd;
      radius = const BorderRadius.only(
        topLeft: Radius.circular(4),
        topRight: Radius.circular(18),
        bottomLeft: Radius.circular(18),
        bottomRight: Radius.circular(18),
      );
    }

    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 14, vertical: 10),
      decoration: BoxDecoration(
        color: bg,
        borderRadius: radius,
        border: isUser
            ? null
            : Border.all(color: AppColors.border.withValues(alpha: 0.6)),
        boxShadow: [
          BoxShadow(
            color: Colors.black.withValues(alpha: 0.15),
            blurRadius: 6,
            offset: const Offset(0, 2),
          ),
        ],
      ),
      child: isTool
          ? Text(
              '${message.name != null ? "${message.name} → " : ""}'
              '${content.length > 200 ? "${content.substring(0, 200)}…" : content}',
              style: AppTextStyles.mono(11, color: textColor),
              maxLines: 2,
              overflow: TextOverflow.ellipsis,
            )
          : message.role == 'assistant'
              ? _RichText(content: content, textColor: textColor)
              : Text(
                  content,
                  style: AppTextStyles.body.copyWith(
                    color: textColor,
                    fontWeight: isUser ? FontWeight.w500 : FontWeight.normal,
                  ),
                ),
    );
  }
}

/// For assistant messages, prefer rendering markdown if there are markdown
/// blocks in message.blocks; otherwise render as plain text.
class _RichText extends StatelessWidget {
  const _RichText({required this.content, required this.textColor});
  final String content;
  final Color textColor;

  @override
  Widget build(BuildContext context) {
    return Text(
      content,
      style: AppTextStyles.body.copyWith(color: textColor),
    );
  }
}

class _ToolChip extends StatelessWidget {
  const _ToolChip({required this.name});
  final String name;

  @override
  Widget build(BuildContext context) {
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 3),
      decoration: BoxDecoration(
        color: AppColors.primary.withValues(alpha: 0.1),
        borderRadius: BorderRadius.circular(6),
        border: Border.all(
          color: AppColors.primary.withValues(alpha: 0.25),
        ),
      ),
      child: Row(
        mainAxisSize: MainAxisSize.min,
        children: [
          Icon(Icons.build, color: AppColors.primary, size: 11),
          const SizedBox(width: 4),
          Text(
            name.toUpperCase(),
            style: AppTextStyles.nano.copyWith(
              color: AppColors.primary,
              letterSpacing: 0.6,
            ),
          ),
        ],
      ),
    );
  }
}
