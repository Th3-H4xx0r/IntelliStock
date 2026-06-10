import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';
import '../../../core/formatters/formatters.dart';
import '../../../core/theme/app_colors.dart';
import '../../../core/theme/app_text_styles.dart';
import '../../../core/widgets/common_widgets.dart';
import '../../../core/widgets/glass_card.dart';
import '../../../core/widgets/status_pill.dart';
import '../../../core/widgets/app_button.dart';
import '../../../core/widgets/confirm_dialog.dart';
import '../../../core/widgets/material_symbols.dart';
import '../../../core/widgets/skeleton.dart';
import '../application/backtests_list_controller.dart';
import '../data/models/backtest.dart';

const _perPageOptions = [10, 15, 25, 50, 100];

class BacktestsScreen extends ConsumerWidget {
  const BacktestsScreen({super.key});

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final s = ref.watch(backtestsListControllerProvider);
    final ctrl = ref.read(backtestsListControllerProvider.notifier);

    return Scaffold(
      backgroundColor: AppColors.canvas,
      body: SafeArea(
        child: RefreshIndicator(
          color: AppColors.primary,
          backgroundColor: AppColors.surface,
          onRefresh: ctrl.refresh,
          child: CustomScrollView(
            slivers: [
              // ── Header ──────────────────────────────────────────────────────
              SliverToBoxAdapter(
                child: Padding(
                  padding:
                      const EdgeInsets.fromLTRB(16, 20, 16, 0),
                  child: Row(
                    children: [
                      IconTile(
                          icon: symbol('analytics'),
                          color: AppColors.primary),
                      const SizedBox(width: 12),
                      Expanded(
                        child: Column(
                          crossAxisAlignment:
                              CrossAxisAlignment.start,
                          children: [
                            Text('All Backtests',
                                style: AppTextStyles.h2),
                            Text(
                              '${s.total} total',
                              style: AppTextStyles.meta
                                  .copyWith(
                                      color: AppColors.textDim),
                            ),
                          ],
                        ),
                      ),
                      IconButton(
                        icon: s.loading
                            ? const SizedBox(
                                width: 18,
                                height: 18,
                                child: CircularProgressIndicator(
                                    strokeWidth: 2),
                              )
                            : Icon(symbol('refresh'),
                                color: AppColors.textMuted),
                        onPressed: s.loading
                            ? null
                            : ctrl.refresh,
                      ),
                    ],
                  ),
                ),
              ),

              // ── Toolbar ─────────────────────────────────────────────────────
              SliverToBoxAdapter(
                child: Padding(
                  padding:
                      const EdgeInsets.fromLTRB(16, 16, 16, 8),
                  child: Row(
                    children: [
                      Text(
                        'Backtests (${s.total})',
                        style: AppTextStyles.eyebrow,
                      ),
                      const Spacer(),
                      Text('Per page  ',
                          style: AppTextStyles.meta
                              .copyWith(color: AppColors.textDim)),
                      _PerPageSelector(
                        value: s.perPage,
                        onChanged: ctrl.setPerPage,
                      ),
                    ],
                  ),
                ),
              ),

              // ── Loading (initial) ───────────────────────────────────────────
              if (s.loading && s.rows.isEmpty)
                SliverPadding(
                  padding: const EdgeInsets.symmetric(
                      horizontal: 16, vertical: 8),
                  sliver: SliverList(
                    delegate: SliverChildBuilderDelegate(
                      (_, __) => const _BacktestCardSkeleton(),
                      childCount: 5,
                    ),
                  ),
                ),

              // ── Error ────────────────────────────────────────────────────────
              if (!s.loading && s.error != null && s.rows.isEmpty)
                SliverFillRemaining(
                  child: Padding(
                    padding:
                        const EdgeInsets.all(16),
                    child: ErrorBanner(
                        message: s.error!,
                        onRetry: ctrl.refresh),
                  ),
                ),

              // ── Empty ────────────────────────────────────────────────────────
              if (!s.loading && s.rows.isEmpty && s.error == null)
                SliverFillRemaining(
                  child: Padding(
                    padding:
                        const EdgeInsets.all(24),
                    child: EmptyState(
                      icon: symbol('analytics'),
                      title: 'No backtests found',
                      subtitle: 'Run a backtest to see results here.',
                    ),
                  ),
                ),

              // ── Cards ─────────────────────────────────────────────────────────
              if (s.rows.isNotEmpty)
                SliverPadding(
                  padding: const EdgeInsets.symmetric(
                      horizontal: 16, vertical: 8),
                  sliver: SliverList(
                    delegate: SliverChildBuilderDelegate(
                      (context, i) {
                        final bt = s.rows[i];
                        final live = s.statusMap[bt.id];
                        return _BacktestCard(
                          key: ValueKey(bt.id),
                          bt: bt,
                          liveStatus: live,
                          onAction: (action) => _doAction(
                              context, ref, bt, action),
                          onView: () => context
                              .push('/backtests/${bt.id}'),
                          onInstanceTap: bt.instanceId != null
                              ? () => context.push(
                                  '/instances/${bt.instanceId}')
                              : null,
                        );
                      },
                      childCount: s.rows.length,
                    ),
                  ),
                ),

              // ── Pagination ───────────────────────────────────────────────────
              if (s.rows.isNotEmpty)
                SliverToBoxAdapter(
                  child: _Pagination(
                    page: s.page,
                    totalPages: s.totalPages,
                    total: s.total,
                    loading: s.loading,
                    onPage: ctrl.goToPage,
                  ),
                ),
            ],
          ),
        ),
      ),
    );
  }

  Future<void> _doAction(
    BuildContext context,
    WidgetRef ref,
    BacktestRow bt,
    String action,
  ) async {
    final meta = _actionMeta(action);
    await showConfirmDialog(
      context,
      title: '${meta.$1} Backtest',
      body: '${meta.$2}\n\nBacktest ID: ${bt.id}',
      confirmLabel: meta.$1,
      confirmColor: meta.$3,
      icon: symbol(meta.$4),
      onConfirm: () async {
        final err = await ref
            .read(backtestsListControllerProvider.notifier)
            .performAction(bt.id, action);
        if (err != null) throw Exception(err);
      },
    );
  }

  static (String, String, Color, String) _actionMeta(String action) {
    switch (action) {
      case 'pause':
        return (
          'Pause',
          'The backtest will be paused and can be resumed later.',
          AppColors.primary,
          'pause_circle'
        );
      case 'resume':
        return (
          'Resume',
          'The backtest will continue from where it was paused.',
          AppColors.info,
          'play_circle'
        );
      case 'stop':
      default:
        return (
          'Stop',
          'This will permanently stop the backtest.',
          AppColors.danger,
          'stop_circle'
        );
    }
  }
}

// ── Backtest Card Skeleton ────────────────────────────────────────────────────

class _BacktestCardSkeleton extends StatelessWidget {
  const _BacktestCardSkeleton();

  @override
  Widget build(BuildContext context) {
    return Padding(
      padding: const EdgeInsets.only(bottom: 12),
      child: GlassCard(
        padding: const EdgeInsets.all(16),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            // ID line + status pill
            Row(
              children: [
                Expanded(child: Skeleton.line(height: 11)),
                const SizedBox(width: 12),
                Skeleton(width: 72, height: 20, radius: 999),
              ],
            ),
            const SizedBox(height: 10),
            // Stock chips row
            Row(
              children: [
                Skeleton(width: 44, height: 20, radius: 4),
                const SizedBox(width: 6),
                Skeleton(width: 44, height: 20, radius: 4),
                const SizedBox(width: 6),
                Skeleton(width: 44, height: 20, radius: 4),
                const SizedBox(width: 6),
                Skeleton(width: 30, height: 20, radius: 4),
              ],
            ),
            const SizedBox(height: 10),
            // Period line
            Skeleton.line(width: 180, height: 11),
            const SizedBox(height: 6),
            // Completed-at line
            Skeleton.line(width: 120, height: 10),
            const SizedBox(height: 12),
            // Elapsed + P&L row
            Row(
              children: [
                Skeleton(width: 80, height: 12, radius: 4),
                const Spacer(),
                Skeleton(width: 70, height: 14, radius: 4),
                const SizedBox(width: 8),
                Skeleton(width: 50, height: 12, radius: 4),
              ],
            ),
            const SizedBox(height: 12),
            // Action buttons row
            Row(
              mainAxisAlignment: MainAxisAlignment.end,
              children: [
                Skeleton(width: 70, height: 30, radius: 8),
              ],
            ),
          ],
        ),
      ),
    );
  }
}

// ── Backtest Card ─────────────────────────────────────────────────────────────

class _BacktestCard extends StatelessWidget {
  const _BacktestCard({
    super.key,
    required this.bt,
    this.liveStatus,
    required this.onAction,
    required this.onView,
    this.onInstanceTap,
  });

  final BacktestRow bt;
  final BacktestStatus? liveStatus;
  final void Function(String action) onAction;
  final VoidCallback onView;
  final VoidCallback? onInstanceTap;

  String get _status =>
      liveStatus?.status ?? bt.status ?? 'queued';

  num? get _progress => liveStatus?.progress;
  NexusLookback? get _lookback => liveStatus?.nexusLookback;
  num? get _elapsed =>
      liveStatus?.timeElapsedSeconds ?? bt.timeElapsedSeconds;

  bool get _isActive {
    final s = _status.toLowerCase();
    return s == 'running' || s == 'queued' || s == 'pending';
  }

  bool get _isPaused {
    final s = _status.toLowerCase();
    return s == 'paused' || s == 'paused_llm_critical';
  }

  bool get _canStop => _isActive || _isPaused;

  @override
  Widget build(BuildContext context) {
    return Padding(
      padding: const EdgeInsets.only(bottom: 12),
      child: GlassCard(
        padding: const EdgeInsets.all(16),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            // ── Top row: ID + status ────────────────────────────────────────
            Row(
              children: [
                Expanded(
                  child: Text(
                    bt.id,
                    style: AppTextStyles.mono(12,
                        color: AppColors.textMuted),
                    maxLines: 1,
                    overflow: TextOverflow.ellipsis,
                  ),
                ),
                StatusPill(
                  label: _status.toUpperCase(),
                  color: StatusPill.colorForStatus(_status),
                  pulsing: _isActive,
                ),
              ],
            ),
            const SizedBox(height: 8),

            // ── Instance link ───────────────────────────────────────────────
            if (bt.instanceId != null)
              GestureDetector(
                onTap: onInstanceTap,
                child: Text(
                  bt.instanceId!,
                  style: AppTextStyles.meta.copyWith(
                    color: AppColors.primary,
                    decoration: TextDecoration.underline,
                    decorationColor: AppColors.primary,
                  ),
                ),
              ),

            // ── Stock chips ─────────────────────────────────────────────────
            if (bt.stocks.isNotEmpty) ...[
              const SizedBox(height: 8),
              Wrap(
                spacing: 6,
                runSpacing: 4,
                children: [
                  ...bt.stocks.take(4).map((s) => Container(
                        padding: const EdgeInsets.symmetric(
                            horizontal: 6, vertical: 2),
                        decoration: BoxDecoration(
                          color: AppColors.surface,
                          borderRadius: BorderRadius.circular(4),
                          border: Border.all(color: AppColors.border),
                        ),
                        child: Text(
                          s,
                          style: AppTextStyles.mono(11,
                              color: AppColors.textMd),
                        ),
                      )),
                  if (bt.stocks.length > 4)
                    Text(
                      '+${bt.stocks.length - 4}',
                      style: AppTextStyles.meta
                          .copyWith(color: AppColors.textFaint),
                    ),
                ],
              ),
            ],
            const SizedBox(height: 8),

            // ── Period ──────────────────────────────────────────────────────
            Text(
              '${bt.startDate ?? '?'} → ${bt.endDate ?? '?'}',
              style: AppTextStyles.meta
                  .copyWith(color: AppColors.textDim),
            ),

            // ── Completed at ────────────────────────────────────────────────
            if (bt.completedAt != null)
              Padding(
                padding: const EdgeInsets.only(top: 2),
                child: Text(
                  fmtDateTime(bt.completedAt),
                  style: AppTextStyles.mono(11,
                      color: AppColors.textMuted),
                ),
              ),
            const SizedBox(height: 8),

            // ── Progress bar (running) ──────────────────────────────────────
            if (_isActive && _progress != null) ...[
              Row(
                children: [
                  Expanded(
                    child: ClipRRect(
                      borderRadius: BorderRadius.circular(4),
                      child: LinearProgressIndicator(
                        value:
                            (_progress!.toDouble() / 100).clamp(0, 1),
                        backgroundColor: AppColors.surface,
                        valueColor: const AlwaysStoppedAnimation(
                            AppColors.info),
                        minHeight: 6,
                      ),
                    ),
                  ),
                  const SizedBox(width: 8),
                  Text(
                    '${_progress!.round()}%',
                    style: AppTextStyles.mono(10,
                        color: AppColors.textDim),
                  ),
                ],
              ),
              const SizedBox(height: 6),
            ],

            // ── Nexus lookback bar ──────────────────────────────────────────
            if (_lookback != null) ...[
              Row(
                children: [
                  Icon(symbol('hub'),
                      color: AppColors.primary, size: 12),
                  const SizedBox(width: 4),
                  Text('Lookback',
                      style: AppTextStyles.nano.copyWith(
                          color: AppColors.primary,
                          fontWeight: FontWeight.w600)),
                  const Spacer(),
                  Text(
                    '${_lookback!.current}/${_lookback!.total}d',
                    style: AppTextStyles.mono(10,
                        color: AppColors.textDim),
                  ),
                ],
              ),
              const SizedBox(height: 4),
              ClipRRect(
                borderRadius: BorderRadius.circular(4),
                child: LinearProgressIndicator(
                  value: _lookback!.fraction.clamp(0, 1),
                  backgroundColor: AppColors.surface,
                  valueColor: AlwaysStoppedAnimation(
                      AppColors.primary.withValues(alpha: 0.7)),
                  minHeight: 6,
                ),
              ),
              const SizedBox(height: 6),
            ],

            // ── Elapsed + P&L row ───────────────────────────────────────────
            Row(
              children: [
                Icon(symbol('timer'),
                    color: AppColors.textFaint, size: 14),
                const SizedBox(width: 4),
                Text(
                  fmtElapsed(_elapsed),
                  style: AppTextStyles.mono(12,
                      color: AppColors.textMuted),
                ),
                const Spacer(),
                Text(
                  fmtPnl(bt.pnl),
                  style: AppTextStyles.mono(13,
                      color: pnlColor(bt.pnl),
                      weight: FontWeight.w700),
                ),
                const SizedBox(width: 8),
                Text(
                  bt.pnlPercent != null
                      ? fmtPct(bt.pnlPercent)
                      : '—',
                  style: AppTextStyles.mono(11,
                      color: pnlColor(bt.pnlPercent)),
                ),
              ],
            ),
            const SizedBox(height: 12),

            // ── Action buttons ──────────────────────────────────────────────
            Row(
              mainAxisAlignment: MainAxisAlignment.end,
              children: [
                if (_isActive)
                  _ActionBtn(
                    icon: symbol('pause_circle'),
                    color: AppColors.primary,
                    tooltip: 'Pause',
                    onTap: () => onAction('pause'),
                  ),
                if (_isPaused)
                  _ActionBtn(
                    icon: symbol('play_circle'),
                    color: AppColors.info,
                    tooltip: 'Resume',
                    onTap: () => onAction('resume'),
                  ),
                if (_canStop)
                  _ActionBtn(
                    icon: symbol('stop_circle'),
                    color: AppColors.danger,
                    tooltip: 'Stop',
                    onTap: () => onAction('stop'),
                  ),
                const SizedBox(width: 4),
                AppButton.primary(
                  label: 'View',
                  icon: symbol('open_in_new'),
                  onPressed: onView,
                ),
              ],
            ),
          ],
        ),
      ),
    );
  }
}

class _ActionBtn extends StatelessWidget {
  const _ActionBtn({
    required this.icon,
    required this.color,
    required this.tooltip,
    required this.onTap,
  });

  final IconData icon;
  final Color color;
  final String tooltip;
  final VoidCallback onTap;

  @override
  Widget build(BuildContext context) => Tooltip(
        message: tooltip,
        child: InkWell(
          onTap: onTap,
          borderRadius: BorderRadius.circular(8),
          child: Padding(
            padding: const EdgeInsets.all(6),
            child: Icon(icon, color: color, size: 22),
          ),
        ),
      );
}

// ── Per-page selector ──────────────────────────────────────────────────────────

class _PerPageSelector extends StatelessWidget {
  const _PerPageSelector(
      {required this.value, required this.onChanged});

  final int value;
  final void Function(int) onChanged;

  @override
  Widget build(BuildContext context) {
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 4),
      decoration: BoxDecoration(
        color: AppColors.surface,
        borderRadius: BorderRadius.circular(8),
        border: Border.all(color: AppColors.border),
      ),
      child: DropdownButtonHideUnderline(
        child: DropdownButton<int>(
          value: value,
          dropdownColor: AppColors.panel,
          style: AppTextStyles.meta.copyWith(color: AppColors.textMd),
          isDense: true,
          items: _perPageOptions
              .map((o) => DropdownMenuItem(
                    value: o,
                    child: Text('$o'),
                  ))
              .toList(),
          onChanged: (v) {
            if (v != null) onChanged(v);
          },
        ),
      ),
    );
  }
}

// ── Pagination ────────────────────────────────────────────────────────────────

class _Pagination extends StatelessWidget {
  const _Pagination({
    required this.page,
    required this.totalPages,
    required this.total,
    required this.loading,
    required this.onPage,
  });

  final int page;
  final int totalPages;
  final int total;
  final bool loading;
  final void Function(int) onPage;

  @override
  Widget build(BuildContext context) {
    if (totalPages <= 1) return const SizedBox(height: 16);
    final pages = _buildPages(page, totalPages);

    return Padding(
      padding: const EdgeInsets.fromLTRB(16, 4, 16, 24),
      child: Column(
        children: [
          Text(
            'Page $page of $totalPages  ($total total)',
            style: AppTextStyles.meta
                .copyWith(color: AppColors.textDim),
          ),
          const SizedBox(height: 8),
          Row(
            mainAxisAlignment: MainAxisAlignment.center,
            children: [
              _NavBtn(
                icon: Icons.chevron_left,
                enabled: page > 1 && !loading,
                onTap: () => onPage(page - 1),
              ),
              const SizedBox(width: 4),
              ...pages.map((p) {
                if (p == null) {
                  return Padding(
                    padding:
                        const EdgeInsets.symmetric(horizontal: 2),
                    child: Text('…',
                        style: AppTextStyles.meta
                            .copyWith(color: AppColors.textFaint)),
                  );
                }
                final active = p == page;
                return _PageBtn(
                  page: p,
                  active: active,
                  disabled: loading,
                  onTap: () => onPage(p),
                );
              }),
              const SizedBox(width: 4),
              _NavBtn(
                icon: Icons.chevron_right,
                enabled: page < totalPages && !loading,
                onTap: () => onPage(page + 1),
              ),
            ],
          ),
        ],
      ),
    );
  }

  /// Returns page numbers with nulls for ellipsis gaps.
  static List<int?> _buildPages(int current, int total) {
    if (total <= 7) {
      return List<int?>.generate(total, (i) => i + 1);
    }
    final result = <int?>{};
    result.add(1);
    result.add(total);
    for (int p = current - 1; p <= current + 1; p++) {
      if (p >= 1 && p <= total) result.add(p);
    }
    final sorted = result.toList()..sort((a, b) => a! - b!);
    final out = <int?>[];
    for (int i = 0; i < sorted.length; i++) {
      if (i > 0 && sorted[i]! - sorted[i - 1]! > 1) {
        out.add(null); // ellipsis
      }
      out.add(sorted[i]);
    }
    return out;
  }
}

class _NavBtn extends StatelessWidget {
  const _NavBtn(
      {required this.icon,
      required this.enabled,
      required this.onTap});

  final IconData icon;
  final bool enabled;
  final VoidCallback onTap;

  @override
  Widget build(BuildContext context) => InkWell(
        onTap: enabled ? onTap : null,
        borderRadius: BorderRadius.circular(8),
        child: Container(
          width: 32,
          height: 32,
          decoration: BoxDecoration(
            border: Border.all(color: AppColors.border),
            borderRadius: BorderRadius.circular(8),
          ),
          child: Icon(icon,
              color: enabled ? AppColors.textMuted : AppColors.textFaint,
              size: 18),
        ),
      );
}

class _PageBtn extends StatelessWidget {
  const _PageBtn({
    required this.page,
    required this.active,
    required this.disabled,
    required this.onTap,
  });

  final int page;
  final bool active;
  final bool disabled;
  final VoidCallback onTap;

  @override
  Widget build(BuildContext context) => Padding(
        padding: const EdgeInsets.symmetric(horizontal: 2),
        child: InkWell(
          onTap: disabled ? null : onTap,
          borderRadius: BorderRadius.circular(8),
          child: AnimatedContainer(
            duration: const Duration(milliseconds: 150),
            width: 32,
            height: 32,
            decoration: BoxDecoration(
              color: active
                  ? AppColors.fill(AppColors.primary)
                  : Colors.transparent,
              border: Border.all(
                  color: active
                      ? AppColors.stroke(AppColors.primary)
                      : Colors.transparent),
              borderRadius: BorderRadius.circular(8),
            ),
            alignment: Alignment.center,
            child: Text(
              '$page',
              style: AppTextStyles.meta.copyWith(
                color:
                    active ? AppColors.primary : AppColors.textMuted,
                fontWeight: active ? FontWeight.w600 : FontWeight.w400,
              ),
            ),
          ),
        ),
      );
}
