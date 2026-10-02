import 'package:flutter/material.dart';
import 'package:intl/intl.dart';
import '../theme/app_colors.dart';

/// Number / date formatters, ported to match the web exactly.
const _dash = '—';

final NumberFormat _money2 = NumberFormat('#,##0.00', 'en_US');
final NumberFormat _money4 = NumberFormat('#,##0.0000', 'en_US');

/// `$1,234.56`, negatives `-$1,234.56`, null -> `—`.
String fmtMoney(num? v) {
  if (v == null) return _dash;
  final neg = v < 0;
  return '${neg ? '-' : ''}\$${_money2.format(v.abs())}';
}

/// `+$1,234.56` / `-$1,234.56` (sign before `$`), null -> `—`.
String fmtPnl(num? v) {
  if (v == null) return _dash;
  final sign = v < 0 ? '-' : '+';
  return '$sign\$${_money2.format(v.abs())}';
}

/// `+12.34%` / `-5.00%` (2dp, leading + for >=0), null -> `—`.
String fmtPct(num? v) {
  if (v == null || v.isNaN) return _dash;
  final sign = v >= 0 ? '+' : '-';
  return '$sign${v.abs().toStringAsFixed(2)}%';
}

/// LLM cost: `$0.00`; for <$1 uses 4dp `$0.0004`.
String fmtUsdCost(num? v) {
  if (v == null) return _dash;
  if (v != 0 && v.abs() < 1) return '\$${_money4.format(v)}';
  return '\$${_money2.format(v)}';
}

/// Token counts: `1.2M` / `3.4k` / raw.
String fmtTokens(num? v) {
  if (v == null) return _dash;
  if (v >= 1000000) return '${(v / 1000000).toStringAsFixed(1)}M';
  if (v >= 1000) return '${(v / 1000).toStringAsFixed(1)}k';
  return v.toString();
}

/// Short duration: `1.5s` / `3m 20s` / `2h 5m` / `1d 2h`.
String fmtDuration(num? seconds) {
  if (seconds == null) return _dash;
  final s = seconds.toDouble();
  if (s < 60) return '${s % 1 == 0 ? s.toInt() : s.toStringAsFixed(1)}s';
  if (s < 3600) {
    final m = (s / 60).floor();
    final rem = (s % 60).round();
    return '${m}m ${rem}s';
  }
  if (s < 86400) {
    final h = (s / 3600).floor();
    final m = ((s % 3600) / 60).floor();
    return '${h}h ${m}m';
  }
  final d = (s / 86400).floor();
  final h = ((s % 86400) / 3600).floor();
  return '${d}d ${h}h';
}

/// Elapsed: `Xd Xh Xm` / `Xh Xm Xs` / `Xm Xs` / `Xs`.
String fmtElapsed(num? seconds) {
  if (seconds == null) return _dash;
  final total = seconds.floor();
  final d = total ~/ 86400;
  final h = (total % 86400) ~/ 3600;
  final m = (total % 3600) ~/ 60;
  final s = total % 60;
  if (d > 0) return '${d}d ${h}h ${m}m';
  if (h > 0) return '${h}h ${m}m ${s}s';
  if (m > 0) return '${m}m ${s}s';
  return '${s}s';
}

/// Parse epoch seconds / ms / ISO into a DateTime (local).
DateTime? parseDateTime(dynamic v) {
  if (v == null) return null;
  if (v is DateTime) return v.toLocal();
  if (v is num) {
    // Heuristic: > 1e12 -> milliseconds, else seconds.
    final ms = v > 1000000000000 ? v.toInt() : (v * 1000).toInt();
    return DateTime.fromMillisecondsSinceEpoch(ms).toLocal();
  }
  if (v is String) {
    final n = num.tryParse(v);
    if (n != null) return parseDateTime(n);
    return DateTime.tryParse(v)?.toLocal();
  }
  return null;
}

final DateFormat _medium = DateFormat('MMM d, yyyy');
final DateFormat _shortTime = DateFormat('h:mm a');

/// Medium date + short time, e.g. `Jun 10, 2026, 2:14 PM`.
String fmtDateTime(dynamic v) {
  final dt = parseDateTime(v);
  if (dt == null) return _dash;
  return '${_medium.format(dt)}, ${_shortTime.format(dt)}';
}

String fmtDate(dynamic v) {
  final dt = parseDateTime(v);
  if (dt == null) return _dash;
  return _medium.format(dt);
}

/// Relative time: `Just now` / `5m ago` / `2h ago` / `3d ago`.
/// Pass [now] to compute against a fixed clock (testing / self-ticking widgets).
String fmtRelative(dynamic v, {DateTime? now}) {
  final dt = parseDateTime(v);
  if (dt == null) return _dash;
  final diff = (now ?? DateTime.now()).difference(dt);
  if (diff.inSeconds < 60) return 'Just now';
  if (diff.inMinutes < 60) return '${diff.inMinutes}m ago';
  if (diff.inHours < 24) return '${diff.inHours}h ago';
  return '${diff.inDays}d ago';
}

/// Color for a P&L value: success when >= 0, danger otherwise.
Color pnlColor(num? v) => (v ?? 0) >= 0 ? AppColors.success : AppColors.danger;
