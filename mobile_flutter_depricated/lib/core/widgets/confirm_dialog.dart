import 'package:flutter/material.dart';
import '../theme/app_colors.dart';
import '../theme/app_text_styles.dart';
import 'app_button.dart';
import 'glass_card.dart';

/// A modal confirmation dialog with a colored icon header and Cancel/confirm
/// footer, matching the web modal pattern. Returns true if confirmed.
Future<bool> showConfirmDialog(
  BuildContext context, {
  required String title,
  required String body,
  String confirmLabel = 'Confirm',
  Color confirmColor = AppColors.danger,
  IconData icon = Icons.warning_amber_rounded,
  Future<void> Function()? onConfirm,
}) async {
  final result = await showDialog<bool>(
    context: context,
    barrierColor: Colors.black.withValues(alpha: 0.6),
    builder: (ctx) => _ConfirmDialog(
      title: title,
      body: body,
      confirmLabel: confirmLabel,
      confirmColor: confirmColor,
      icon: icon,
      onConfirm: onConfirm,
    ),
  );
  return result ?? false;
}

class _ConfirmDialog extends StatefulWidget {
  const _ConfirmDialog({
    required this.title,
    required this.body,
    required this.confirmLabel,
    required this.confirmColor,
    required this.icon,
    this.onConfirm,
  });

  final String title;
  final String body;
  final String confirmLabel;
  final Color confirmColor;
  final IconData icon;
  final Future<void> Function()? onConfirm;

  @override
  State<_ConfirmDialog> createState() => _ConfirmDialogState();
}

class _ConfirmDialogState extends State<_ConfirmDialog> {
  bool _busy = false;

  @override
  Widget build(BuildContext context) {
    return Dialog(
      backgroundColor: Colors.transparent,
      insetPadding: const EdgeInsets.all(24),
      child: ConstrainedBox(
        constraints: const BoxConstraints(maxWidth: 400),
        child: GlassCard(
          padding: EdgeInsets.zero,
          child: Column(
            mainAxisSize: MainAxisSize.min,
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Container(
                padding: const EdgeInsets.fromLTRB(20, 18, 20, 16),
                decoration: BoxDecoration(
                  border: Border(
                    top: BorderSide(color: widget.confirmColor, width: 2),
                    bottom: const BorderSide(color: AppColors.border),
                  ),
                ),
                child: Row(
                  children: [
                    Container(
                      width: 36,
                      height: 36,
                      decoration: BoxDecoration(
                        color: AppColors.fill(widget.confirmColor),
                        borderRadius: BorderRadius.circular(10),
                        border:
                            Border.all(color: AppColors.stroke(widget.confirmColor)),
                      ),
                      child: Icon(widget.icon,
                          size: 20, color: widget.confirmColor),
                    ),
                    const SizedBox(width: 12),
                    Expanded(
                      child: Text(widget.title,
                          style: AppTextStyles.h3.copyWith(fontWeight: FontWeight.w700)),
                    ),
                    IconButton(
                      icon: const Icon(Icons.close, size: 18),
                      color: AppColors.textMuted,
                      onPressed: _busy ? null : () => Navigator.pop(context, false),
                    ),
                  ],
                ),
              ),
              Padding(
                padding: const EdgeInsets.all(20),
                child: Text(widget.body, style: AppTextStyles.body),
              ),
              Padding(
                padding: const EdgeInsets.fromLTRB(20, 0, 20, 20),
                child: Row(
                  children: [
                    Expanded(
                      child: AppButton.ghost(
                        label: 'Cancel',
                        onPressed:
                            _busy ? null : () => Navigator.pop(context, false),
                      ),
                    ),
                    const SizedBox(width: 12),
                    Expanded(
                      child: AppButton.semantic(
                        label: widget.confirmLabel,
                        color: widget.confirmColor,
                        busy: _busy,
                        onPressed: () async {
                          if (widget.onConfirm != null) {
                            setState(() => _busy = true);
                            try {
                              await widget.onConfirm!();
                              if (context.mounted) Navigator.pop(context, true);
                            } catch (_) {
                              if (mounted) setState(() => _busy = false);
                            }
                          } else {
                            Navigator.pop(context, true);
                          }
                        },
                      ),
                    ),
                  ],
                ),
              ),
            ],
          ),
        ),
      ),
    );
  }
}
