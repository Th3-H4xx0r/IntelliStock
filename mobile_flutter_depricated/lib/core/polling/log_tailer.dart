import 'dart:async';
import '../network/api_client.dart';
import '../theme/app_colors.dart';
import 'package:flutter/material.dart';

enum LogLevel { error, warn, success, info, normal }

class LogLine {
  LogLine({required this.raw, this.ts, required this.message, required this.level});

  final String raw;
  final DateTime? ts;
  final String message;
  final LogLevel level;

  Color get color => switch (level) {
        LogLevel.error => AppColors.danger,
        LogLevel.warn => AppColors.warning,
        LogLevel.success => AppColors.success,
        LogLevel.info => AppColors.info,
        LogLevel.normal => AppColors.textMd,
      };
}

/// Parse a `[timestamp] message` line and classify its level.
LogLine parseLogLine(String raw) {
  DateTime? ts;
  String message = raw;
  final match = RegExp(r'^\[([^\]]+)\]\s?(.*)$', dotAll: true).firstMatch(raw);
  if (match != null) {
    ts = DateTime.tryParse(match.group(1)!.trim());
    message = match.group(2) ?? '';
  }
  return LogLine(raw: raw, ts: ts, message: message, level: _classify(raw));
}

LogLevel _classify(String line) {
  final l = line.toLowerCase();
  if (l.contains('error') || l.contains('traceback') || l.contains('failed') ||
      l.contains('exception')) {
    return LogLevel.error;
  }
  if (l.contains('warn') || l.contains('retry') || l.contains('skip')) {
    return LogLevel.warn;
  }
  if (l.contains('completed') || l.contains('success') || l.contains('passed') ||
      l.contains(' ok')) {
    return LogLevel.success;
  }
  if (l.contains('broker')) return LogLevel.info;
  return LogLevel.normal;
}

class LogTailerState {
  LogTailerState({
    this.lines = const [],
    this.nextLine = 0,
    this.totalLines = 0,
    this.truncated = false,
    this.finalStatus,
    this.source = 'file',
    this.buildId,
    this.loading = true,
    this.error,
  });

  final List<LogLine> lines;
  final int nextLine;
  final int totalLines;
  final bool truncated;
  final String? finalStatus;
  final String source;
  final String? buildId;
  final bool loading;
  final Object? error;

  LogTailerState copyWith({
    List<LogLine>? lines,
    int? nextLine,
    int? totalLines,
    bool? truncated,
    String? finalStatus,
    String? source,
    String? buildId,
    bool? loading,
    Object? error,
    bool clearError = false,
  }) {
    return LogTailerState(
      lines: lines ?? this.lines,
      nextLine: nextLine ?? this.nextLine,
      totalLines: totalLines ?? this.totalLines,
      truncated: truncated ?? this.truncated,
      finalStatus: finalStatus ?? this.finalStatus,
      source: source ?? this.source,
      buildId: buildId ?? this.buildId,
      loading: loading ?? this.loading,
      error: clearError ? null : (error ?? this.error),
    );
  }
}

/// Cursor-based log tailer matching the web `live-logs?since_line=` pattern.
///
/// Polls 2-5s while running / 15s idle, immediately re-polls on `truncated`,
/// backs off `[2,5,10,30]s` on error, and caps in-memory lines at 10k.
class LogTailer {
  LogTailer({
    required this.client,
    required this.pathBuilder,
    this.runningInterval = const Duration(seconds: 5),
    this.idleInterval = const Duration(seconds: 15),
  });

  final ApiClient client;

  /// Builds the request path given the current cursor.
  final String Function(int sinceLine) pathBuilder;
  final Duration runningInterval;
  final Duration idleInterval;

  static const int _maxLines = 10000;
  static const List<int> _backoff = [2, 5, 10, 30];

  final _controller = StreamController<LogTailerState>.broadcast();
  LogTailerState _state = LogTailerState();
  Timer? _timer;
  bool _paused = false;
  bool _disposed = false;
  bool _polling = false;
  int _errorStreak = 0;

  Stream<LogTailerState> get stream => _controller.stream;
  LogTailerState get state => _state;

  void start() {
    _poll();
  }

  void _emit(LogTailerState s) {
    _state = s;
    if (!_controller.isClosed) _controller.add(s);
  }

  Future<void> _poll() async {
    if (_disposed || _paused || _polling) return;
    _polling = true;
    try {
      final data = await client.get<Map<String, dynamic>>(
        pathBuilder(_state.nextLine),
      );
      _errorStreak = 0;

      final source = (data['source'] ?? 'file').toString();
      final buildId = data['id']?.toString();

      // A new build / source swap reseeds the cursor.
      var lines = _state.lines;
      var nextLine = _state.nextLine;
      if (buildId != _state.buildId && _state.buildId != null) {
        lines = [];
        nextLine = 0;
      }

      final rawLogs = (data['logs'] as List? ?? const []).cast<String>();
      final parsed = rawLogs.map(parseLogLine).toList();
      var merged = [...lines, ...parsed];
      if (merged.length > _maxLines) {
        merged = merged.sublist(merged.length - _maxLines);
      }

      final truncated = data['truncated'] == true;
      _emit(_state.copyWith(
        lines: merged,
        nextLine: (data['next_line'] as num?)?.toInt() ?? nextLine,
        totalLines: (data['total_lines'] as num?)?.toInt() ?? merged.length,
        truncated: truncated,
        finalStatus: data['final_status']?.toString(),
        source: source,
        buildId: buildId,
        loading: false,
        clearError: true,
      ));

      _scheduleNext(truncated ? Duration.zero : _activeInterval());
    } catch (e) {
      _errorStreak++;
      _emit(_state.copyWith(loading: false, error: e));
      final idx = (_errorStreak - 1).clamp(0, _backoff.length - 1);
      _scheduleNext(Duration(seconds: _backoff[idx]));
    } finally {
      _polling = false;
    }
  }

  Duration _activeInterval() {
    final s = _state.finalStatus?.toLowerCase();
    final running = s == null || s == 'running' || s == 'building';
    return running ? runningInterval : idleInterval;
  }

  void _scheduleNext(Duration d) {
    _timer?.cancel();
    if (_disposed || _paused) return;
    _timer = Timer(d, _poll);
  }

  void pause() {
    _paused = true;
    _timer?.cancel();
  }

  void resume() {
    if (!_paused) return;
    _paused = false;
    _poll();
  }

  void dispose() {
    _disposed = true;
    _timer?.cancel();
    _controller.close();
  }
}
