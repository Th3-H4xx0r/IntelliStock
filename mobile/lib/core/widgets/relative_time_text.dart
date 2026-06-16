import 'dart:async';
import 'package:flutter/material.dart';
import '../formatters/formatters.dart';

/// A `Text` that shows how long ago [timestamp] was ("Just now", "5m ago", …)
/// and re-renders itself on a timer so the label stays honest without any
/// parent rebuild. Pass [clock] in tests to control "now".
class RelativeTimeText extends StatefulWidget {
  const RelativeTimeText({
    super.key,
    required this.timestamp,
    this.style,
    this.tick = const Duration(seconds: 20),
    this.clock,
  });

  final DateTime? timestamp;
  final TextStyle? style;
  final Duration tick;
  final DateTime Function()? clock;

  @override
  State<RelativeTimeText> createState() => _RelativeTimeTextState();
}

class _RelativeTimeTextState extends State<RelativeTimeText> {
  Timer? _timer;

  @override
  void initState() {
    super.initState();
    _timer = Timer.periodic(widget.tick, (_) {
      if (mounted) setState(() {});
    });
  }

  @override
  void dispose() {
    _timer?.cancel();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    final ts = widget.timestamp;
    if (ts == null) return const SizedBox.shrink();
    final now = (widget.clock ?? DateTime.now)();
    return Text(fmtRelative(ts, now: now), style: widget.style);
  }
}
