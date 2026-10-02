import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import '../../../core/network/api_client.dart';
import '../../../core/polling/log_tailer.dart';
import '../../../core/theme/app_colors.dart';
import '../../../core/theme/app_text_styles.dart';
import '../../../core/widgets/glass_card.dart';
import '../../../core/widgets/material_symbols.dart';

/// Terminal-style live-log panel for the Nexus graph build.
///
/// Uses [LogTailer] with `pathBuilder: (n) => '/nexus-graph-builds/latest/logs?since_line=$n'`.
/// Polls every 2s while building, 15s while idle.
/// Auto-sticks to the bottom unless the user scrolls up.
class NexusLogsPanel extends ConsumerStatefulWidget {
  const NexusLogsPanel({super.key});

  @override
  ConsumerState<NexusLogsPanel> createState() => _NexusLogsPanelState();
}

class _NexusLogsPanelState extends ConsumerState<NexusLogsPanel> {
  late LogTailer _tailer;
  LogTailerState _tailerState = LogTailerState();
  final ScrollController _scrollCtrl = ScrollController();
  final TextEditingController _searchCtrl = TextEditingController();

  bool _open = false;
  bool _autoScroll = true;
  bool _showScrollFab = false;
  bool _isPaused = false;
  String _searchQuery = '';

  @override
  void initState() {
    super.initState();
    _buildTailer();
    _scrollCtrl.addListener(_onScroll);
    _searchCtrl.addListener(() {
      setState(() => _searchQuery = _searchCtrl.text.toLowerCase().trim());
    });
  }

  void _buildTailer() {
    _tailer = LogTailer(
      client: ref.read(apiClientProvider),
      pathBuilder: (n) => '/nexus-graph-builds/latest/logs?since_line=$n',
      runningInterval: const Duration(seconds: 2),
      idleInterval: const Duration(seconds: 15),
    );
    _tailer.stream.listen((s) {
      if (!mounted) return;
      setState(() => _tailerState = s);
      if (_autoScroll && _open) {
        WidgetsBinding.instance.addPostFrameCallback((_) => _scrollToBottom());
      }
    });
  }

  @override
  void dispose() {
    _tailer.dispose();
    _scrollCtrl.dispose();
    _searchCtrl.dispose();
    super.dispose();
  }

  void _onScroll() {
    if (!_scrollCtrl.hasClients) return;
    final pos = _scrollCtrl.position;
    final nearBottom = pos.maxScrollExtent - pos.pixels < 48;
    if (nearBottom != _autoScroll || !nearBottom != _showScrollFab) {
      setState(() {
        _autoScroll = nearBottom;
        _showScrollFab = !nearBottom;
      });
    }
  }

  void _scrollToBottom() {
    if (!_scrollCtrl.hasClients) return;
    _scrollCtrl.jumpTo(_scrollCtrl.position.maxScrollExtent);
  }

  void _toggleOpen() {
    setState(() => _open = !_open);
    if (_open) {
      _tailer.resume();
      _tailer.start();
    } else {
      _tailer.pause();
    }
  }

  void _togglePause() {
    if (_tailerState.loading) return;
    _isPaused ? _tailer.resume() : _tailer.pause();
    setState(() => _isPaused = !_isPaused);
  }

  Future<void> _copyLogs() async {
    final text = _tailerState.lines.map((l) => l.raw).join('\n');
    await Clipboard.setData(ClipboardData(text: text));
  }

  // ── Status helpers ──────────────────────────────────────────────────────────

  Color get _statusColor {
    final s = (_tailerState.finalStatus ?? '').toLowerCase();
    if (s == 'running' || s == 'building') return AppColors.info;
    if (s == 'failed') return AppColors.danger;
    if (s == 'completed') return AppColors.success;
    return AppColors.textFaint;
  }

  bool get _statusPulsing {
    final s = (_tailerState.finalStatus ?? '').toLowerCase();
    return s == 'running' || s == 'building';
  }

  String get _friendlyStatus {
    final s = (_tailerState.finalStatus ?? '').toLowerCase();
    if (s == 'running' || s == 'building') return 'Building';
    if (s == 'completed') return 'Completed';
    if (s == 'failed') return 'Failed';
    if (s == 'none') return 'Not running';
    return 'Idle';
  }

  String get _shortBuildId {
    final id = _tailerState.buildId;
    if (id == null) return 'latest';
    final s = id.toString();
    return s.length > 8 ? s.substring(s.length - 8) : s;
  }

  List<LogLine> get _filteredLines {
    if (_searchQuery.isEmpty) return _tailerState.lines;
    return _tailerState.lines
        .where((l) => l.raw.toLowerCase().contains(_searchQuery))
        .toList();
  }

  @override
  Widget build(BuildContext context) {
    return GlassCard(
      padding: EdgeInsets.zero,
      borderColor: AppColors.border,
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        mainAxisSize: MainAxisSize.min,
        children: [
          _buildHeader(),
          if (_open) _buildBody(),
        ],
      ),
    );
  }

  // ── Header ──────────────────────────────────────────────────────────────────

  Widget _buildHeader() {
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 10),
      decoration: BoxDecoration(
        color: AppColors.panelAlt,
        borderRadius: _open
            ? const BorderRadius.vertical(top: Radius.circular(16))
            : BorderRadius.circular(16),
      ),
      child: Row(
        children: [
          _AnimatedDot(color: _statusColor, pulsing: _statusPulsing),
          const SizedBox(width: 8),
          Expanded(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Text(
                  'nexus-$_shortBuildId.log',
                  style: AppTextStyles.mono(11, color: AppColors.textMd),
                  overflow: TextOverflow.ellipsis,
                ),
                if (_tailerState.finalStatus != null &&
                    _tailerState.finalStatus != 'none')
                  Text(
                    '$_friendlyStatus'
                    '${_tailerState.lines.isNotEmpty ? ' · ${_tailerState.lines.length} lines' : ''}',
                    style: AppTextStyles.nano,
                  ),
                if (_tailerState.source == 'db')
                  Text(
                    '(last 500 — log file not available)',
                    style: AppTextStyles.nano
                        .copyWith(color: AppColors.warning.withValues(alpha: 0.7)),
                  ),
              ],
            ),
          ),
          if (_open && _tailerState.lines.isNotEmpty) ...[
            _headerBtn(
              _isPaused ? symbol('play_arrow') : symbol('pause'),
              _isPaused ? 'Resume' : 'Pause',
              _togglePause,
              color: _isPaused ? AppColors.warning : AppColors.textMuted,
            ),
            const SizedBox(width: 4),
            _headerBtn(Icons.content_copy_outlined, 'Copy', _copyLogs),
            const SizedBox(width: 4),
          ],
          _ViewHideButton(open: _open, onTap: _toggleOpen),
        ],
      ),
    );
  }

  Widget _headerBtn(
    IconData icon,
    String tooltip,
    VoidCallback onTap, {
    Color color = AppColors.textMuted,
  }) {
    return GestureDetector(
      onTap: onTap,
      child: Tooltip(
        message: tooltip,
        child: Container(
          padding: const EdgeInsets.all(6),
          child: Icon(icon, size: 16, color: color),
        ),
      ),
    );
  }

  // ── Search field ─────────────────────────────────────────────────────────────

  Widget _buildSearchField() {
    return Container(
      height: 32,
      margin: const EdgeInsets.fromLTRB(12, 8, 12, 4),
      child: TextField(
        controller: _searchCtrl,
        style: AppTextStyles.mono(11, color: AppColors.textMd),
        decoration: InputDecoration(
          hintText: 'Search logs...',
          hintStyle: AppTextStyles.mono(11, color: AppColors.textFaint),
          prefixIcon:
              Icon(symbol('search'), size: 14, color: AppColors.textFaint),
          contentPadding:
              const EdgeInsets.symmetric(vertical: 4, horizontal: 8),
          isDense: true,
          filled: true,
          fillColor: AppColors.surface.withValues(alpha: 0.6),
          border: OutlineInputBorder(
            borderRadius: BorderRadius.circular(6),
            borderSide: BorderSide(color: AppColors.border),
          ),
          enabledBorder: OutlineInputBorder(
            borderRadius: BorderRadius.circular(6),
            borderSide: BorderSide(color: AppColors.border),
          ),
          focusedBorder: OutlineInputBorder(
            borderRadius: BorderRadius.circular(6),
            borderSide:
                BorderSide(color: AppColors.info.withValues(alpha: 0.5)),
          ),
        ),
      ),
    );
  }

  // ── Body ─────────────────────────────────────────────────────────────────────

  Widget _buildBody() {
    return Container(
      decoration: const BoxDecoration(
        color: AppColors.panelAlt,
        borderRadius: BorderRadius.vertical(bottom: Radius.circular(16)),
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          const Divider(height: 1, color: Color(0xFF21262D)),
          if (_tailerState.lines.isNotEmpty) _buildSearchField(),
          _buildLogArea(),
        ],
      ),
    );
  }

  Widget _buildLogArea() {
    if (_tailerState.loading && _tailerState.lines.isEmpty) {
      return Padding(
        padding: const EdgeInsets.all(20),
        child: Row(
          children: [
            const SizedBox(
              width: 14,
              height: 14,
              child: CircularProgressIndicator(strokeWidth: 1.5),
            ),
            const SizedBox(width: 10),
            Text('Loading logs…', style: AppTextStyles.mono(11)),
          ],
        ),
      );
    }

    if (_tailerState.lines.isEmpty) {
      final s = _tailerState.finalStatus ?? '';
      String msg;
      if (_tailerState.error != null) {
        msg = "Can't reach logs endpoint. Retrying…";
      } else if (s == 'none' || s.isEmpty) {
        msg = 'No active build. Trigger a Nexus build to see live logs.';
      } else {
        msg = 'Waiting for log output…';
      }
      return Padding(
        padding: const EdgeInsets.all(20),
        child: Text(
          msg,
          style: AppTextStyles.mono(11, color: AppColors.textFaint)
              .merge(const TextStyle(fontStyle: FontStyle.italic)),
        ),
      );
    }

    final lines = _filteredLines;

    return Stack(
      children: [
        ConstrainedBox(
          constraints: const BoxConstraints(maxHeight: 400),
          child: ListView.builder(
            controller: _scrollCtrl,
            physics: const ClampingScrollPhysics(),
            itemCount: lines.length,
            itemBuilder: (_, idx) => _LogRow(line: lines[idx]),
          ),
        ),
        if (_showScrollFab)
          Positioned(
            bottom: 8,
            right: 12,
            child: GestureDetector(
              onTap: () {
                setState(() {
                  _autoScroll = true;
                  _showScrollFab = false;
                });
                _scrollToBottom();
              },
              child: Container(
                padding:
                    const EdgeInsets.symmetric(horizontal: 10, vertical: 5),
                decoration: BoxDecoration(
                  color: AppColors.fill(AppColors.info),
                  borderRadius: BorderRadius.circular(999),
                  border: Border.all(color: AppColors.stroke(AppColors.info)),
                ),
                child: Row(
                  mainAxisSize: MainAxisSize.min,
                  children: [
                    Icon(symbol('arrow_downward'),
                        size: 12, color: AppColors.info),
                    const SizedBox(width: 4),
                    Text(
                      'Jump to latest',
                      style: AppTextStyles.nano.copyWith(
                        color: AppColors.info,
                        fontWeight: FontWeight.w600,
                      ),
                    ),
                  ],
                ),
              ),
            ),
          ),
      ],
    );
  }
}

// ── Log row ───────────────────────────────────────────────────────────────────

class _LogRow extends StatelessWidget {
  const _LogRow({required this.line});
  final LogLine line;

  @override
  Widget build(BuildContext context) {
    return Container(
      decoration: const BoxDecoration(
        border: Border(
          left: BorderSide(color: Colors.transparent, width: 2),
        ),
      ),
      child: Row(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          if (line.ts != null)
            SizedBox(
              width: 88,
              child: Padding(
                padding: const EdgeInsets.only(left: 10, top: 2, bottom: 2),
                child: Text(
                  _fmtTs(line.ts!),
                  style: AppTextStyles.mono(9, color: AppColors.textFaint),
                ),
              ),
            )
          else
            const SizedBox(width: 10),
          Expanded(
            child: Padding(
              padding: const EdgeInsets.symmetric(vertical: 2, horizontal: 6),
              child: Text(
                line.message,
                style: AppTextStyles.mono(10, color: line.color),
              ),
            ),
          ),
        ],
      ),
    );
  }

  String _fmtTs(DateTime ts) {
    final m = ts.month.toString().padLeft(2, '0');
    final d = ts.day.toString().padLeft(2, '0');
    final hh = ts.hour.toString().padLeft(2, '0');
    final mm = ts.minute.toString().padLeft(2, '0');
    final ss = ts.second.toString().padLeft(2, '0');
    return '$m-$d, $hh:$mm:$ss';
  }
}

// ── Animated pulsing dot ──────────────────────────────────────────────────────

class _AnimatedDot extends StatefulWidget {
  const _AnimatedDot({required this.color, required this.pulsing});
  final Color color;
  final bool pulsing;

  @override
  State<_AnimatedDot> createState() => _AnimatedDotState();
}

class _AnimatedDotState extends State<_AnimatedDot>
    with SingleTickerProviderStateMixin {
  late final AnimationController _ctrl = AnimationController(
    vsync: this,
    duration: const Duration(milliseconds: 1000),
  );

  @override
  void initState() {
    super.initState();
    if (widget.pulsing) _ctrl.repeat(reverse: true);
  }

  @override
  void didUpdateWidget(_AnimatedDot old) {
    super.didUpdateWidget(old);
    if (widget.pulsing && !_ctrl.isAnimating) {
      _ctrl.repeat(reverse: true);
    } else if (!widget.pulsing) {
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
    return FadeTransition(
      opacity: widget.pulsing
          ? Tween(begin: 0.3, end: 1.0).animate(_ctrl)
          : const AlwaysStoppedAnimation(1),
      child: Container(
        width: 8,
        height: 8,
        decoration:
            BoxDecoration(color: widget.color, shape: BoxShape.circle),
      ),
    );
  }
}

// ── View/Hide toggle button ───────────────────────────────────────────────────

class _ViewHideButton extends StatelessWidget {
  const _ViewHideButton({required this.open, required this.onTap});
  final bool open;
  final VoidCallback onTap;

  @override
  Widget build(BuildContext context) {
    final color = open ? AppColors.info : AppColors.textMuted;
    return GestureDetector(
      onTap: onTap,
      child: Container(
        padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 5),
        decoration: BoxDecoration(
          color: open ? AppColors.fill(AppColors.info) : Colors.transparent,
          borderRadius: BorderRadius.circular(6),
          border: Border.all(
              color: open
                  ? AppColors.stroke(AppColors.info)
                  : AppColors.border),
        ),
        child: Row(
          mainAxisSize: MainAxisSize.min,
          children: [
            Icon(
              open ? symbol('visibility_off') : symbol('terminal'),
              size: 13,
              color: color,
            ),
            const SizedBox(width: 4),
            Text(
              open ? 'Hide Logs' : 'View Build Logs',
              style: AppTextStyles.micro.copyWith(
                color: color,
                fontWeight: FontWeight.w600,
              ),
            ),
          ],
        ),
      ),
    );
  }
}
