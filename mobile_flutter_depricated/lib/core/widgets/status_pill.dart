import 'package:flutter/material.dart';
import '../theme/app_colors.dart';
import '../theme/app_text_styles.dart';

/// A dot + label pill (`bg-{color}/10 border-{color}/20 text-{color}`).
/// The dot pulses when [pulsing] is true (live / running states).
class StatusPill extends StatefulWidget {
  const StatusPill({
    super.key,
    required this.label,
    required this.color,
    this.pulsing = false,
  });

  final String label;
  final Color color;
  final bool pulsing;

  /// Convenience: map a status string to a sensible color.
  static Color colorForStatus(String? status) {
    switch (status?.toLowerCase()) {
      case 'running':
      case 'active':
      case 'completed':
      case 'finished':
      case 'passed':
        return AppColors.success;
      case 'paused':
      case 'paused_llm_critical':
        return AppColors.warning;
      case 'queued':
      case 'pending':
        return AppColors.warning;
      case 'building':
        return AppColors.info;
      case 'stopped':
      case 'cancelled':
      case 'error':
      case 'failed':
      case 'aborted_llm_failure':
        return AppColors.danger;
      default:
        return AppColors.textDim;
    }
  }

  @override
  State<StatusPill> createState() => _StatusPillState();
}

class _StatusPillState extends State<StatusPill>
    with SingleTickerProviderStateMixin {
  late final AnimationController _ctrl;

  @override
  void initState() {
    super.initState();
    _ctrl = AnimationController(
      vsync: this,
      duration: const Duration(milliseconds: 1200),
    );
    if (widget.pulsing) _ctrl.repeat(reverse: true);
  }

  @override
  void didUpdateWidget(StatusPill old) {
    super.didUpdateWidget(old);
    if (widget.pulsing && !_ctrl.isAnimating) {
      _ctrl.repeat(reverse: true);
    } else if (!widget.pulsing && _ctrl.isAnimating) {
      _ctrl.stop();
      _ctrl.value = 1;
    }
  }

  @override
  void dispose() {
    _ctrl.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 5),
      decoration: BoxDecoration(
        color: AppColors.fill(widget.color),
        borderRadius: BorderRadius.circular(999),
        border: Border.all(color: AppColors.stroke(widget.color)),
      ),
      child: Row(
        mainAxisSize: MainAxisSize.min,
        children: [
          FadeTransition(
            opacity: widget.pulsing
                ? Tween(begin: 0.4, end: 1.0).animate(_ctrl)
                : const AlwaysStoppedAnimation(1),
            child: Container(
              width: 6,
              height: 6,
              decoration:
                  BoxDecoration(color: widget.color, shape: BoxShape.circle),
            ),
          ),
          const SizedBox(width: 6),
          Text(
            widget.label,
            style: AppTextStyles.micro.copyWith(
              color: widget.color,
              fontWeight: FontWeight.w600,
            ),
          ),
        ],
      ),
    );
  }
}
