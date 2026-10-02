import 'package:flutter/material.dart';
import '../theme/app_colors.dart';
import '../theme/app_text_styles.dart';

/// The "type HALT / CLOSE {SYMBOL} / instance-id to confirm" safety gate.
/// Calls [onMatchChanged] whenever the typed text starts/stops matching
/// [phrase] exactly.
class TypedConfirmField extends StatefulWidget {
  const TypedConfirmField({
    super.key,
    required this.phrase,
    required this.onMatchChanged,
    this.label,
  });

  final String phrase;
  final ValueChanged<bool> onMatchChanged;
  final String? label;

  @override
  State<TypedConfirmField> createState() => _TypedConfirmFieldState();
}

class _TypedConfirmFieldState extends State<TypedConfirmField> {
  final _controller = TextEditingController();
  bool _matched = false;

  @override
  void initState() {
    super.initState();
    _controller.addListener(_check);
  }

  void _check() {
    final m = _controller.text.trim() == widget.phrase;
    if (m != _matched) {
      _matched = m;
      widget.onMatchChanged(m);
    }
  }

  @override
  void dispose() {
    _controller.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Text(
          widget.label ?? 'Type "${widget.phrase}" to confirm',
          style: AppTextStyles.micro,
        ),
        const SizedBox(height: 6),
        TextField(
          controller: _controller,
          autocorrect: false,
          enableSuggestions: false,
          style: AppTextStyles.mono(14, color: AppColors.textHi),
          decoration: InputDecoration(hintText: widget.phrase),
        ),
      ],
    );
  }
}
