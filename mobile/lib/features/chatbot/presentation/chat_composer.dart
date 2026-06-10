import 'package:flutter/material.dart';
import '../../../core/theme/app_colors.dart';
import '../../../core/theme/app_text_styles.dart';

/// Bottom input area — auto-growing TextField + send button.
///
/// Enter (without shift) triggers send.  Shift+Enter inserts a newline.
/// Mirrors the Vue `ChatComposer.vue`.
class ChatComposer extends StatefulWidget {
  const ChatComposer({
    super.key,
    required this.onSend,
    this.busy = false,
    this.disabled = false,
    this.placeholder = 'Ask me anything…',
  });

  final ValueChanged<String> onSend;
  final bool busy;
  final bool disabled;
  final String placeholder;

  @override
  State<ChatComposer> createState() => _ChatComposerState();
}

class _ChatComposerState extends State<ChatComposer> {
  final _ctrl = TextEditingController();
  final _focus = FocusNode();
  bool _hasText = false;

  @override
  void initState() {
    super.initState();
    _ctrl.addListener(() {
      final has = _ctrl.text.trim().isNotEmpty;
      if (has != _hasText) setState(() => _hasText = has);
    });
  }

  @override
  void dispose() {
    _ctrl.dispose();
    _focus.dispose();
    super.dispose();
  }

  void _send() {
    final text = _ctrl.text.trim();
    if (text.isEmpty || widget.busy || widget.disabled) return;
    widget.onSend(text);
    _ctrl.clear();
    setState(() => _hasText = false);
  }

  @override
  Widget build(BuildContext context) {
    return SafeArea(
      top: false,
      child: Container(
        decoration: BoxDecoration(
          color: const Color(0xFF0a0f13).withValues(alpha: 0.85),
          border: Border(
            top: BorderSide(color: AppColors.border, width: 1),
          ),
        ),
        padding: const EdgeInsets.fromLTRB(12, 10, 12, 10),
        child: Column(
          mainAxisSize: MainAxisSize.min,
          children: [
            Container(
              decoration: BoxDecoration(
                color: AppColors.surface.withValues(alpha: 0.7),
                borderRadius: BorderRadius.circular(14),
                border: Border.all(color: AppColors.border),
              ),
              child: Row(
                crossAxisAlignment: CrossAxisAlignment.end,
                children: [
                  Expanded(
                    child: Padding(
                      padding: const EdgeInsets.fromLTRB(12, 8, 4, 8),
                      child: TextField(
                        controller: _ctrl,
                        focusNode: _focus,
                        enabled: !widget.disabled && !widget.busy,
                        maxLines: 6,
                        minLines: 1,
                        keyboardType: TextInputType.multiline,
                        textInputAction: TextInputAction.newline,
                        style: AppTextStyles.body
                            .copyWith(color: AppColors.textHi),
                        decoration: InputDecoration(
                          hintText: widget.placeholder,
                          hintStyle: AppTextStyles.body
                              .copyWith(color: AppColors.textFaint),
                          border: InputBorder.none,
                          isDense: true,
                          contentPadding: EdgeInsets.zero,
                        ),
                        onSubmitted: (_) => _send(),
                      ),
                    ),
                  ),
                  Padding(
                    padding: const EdgeInsets.all(4),
                    child: _SendButton(
                      busy: widget.busy,
                      disabled: widget.disabled || !_hasText,
                      onTap: _send,
                    ),
                  ),
                ],
              ),
            ),
          ],
        ),
      ),
    );
  }
}

class _SendButton extends StatelessWidget {
  const _SendButton({
    required this.busy,
    required this.disabled,
    required this.onTap,
  });

  final bool busy;
  final bool disabled;
  final VoidCallback onTap;

  @override
  Widget build(BuildContext context) {
    return GestureDetector(
      onTap: (busy || disabled) ? null : onTap,
      child: AnimatedContainer(
        duration: const Duration(milliseconds: 150),
        width: 36,
        height: 36,
        decoration: BoxDecoration(
          color: (busy || disabled)
              ? AppColors.surface
              : AppColors.primary,
          borderRadius: BorderRadius.circular(10),
        ),
        child: Center(
          child: busy
              ? SizedBox(
                  width: 16,
                  height: 16,
                  child: CircularProgressIndicator(
                    strokeWidth: 2,
                    color: AppColors.textDim,
                  ),
                )
              : Icon(
                  Icons.send,
                  size: 16,
                  color: disabled
                      ? AppColors.textFaint
                      : AppColors.onPrimary,
                ),
        ),
      ),
    );
  }
}
