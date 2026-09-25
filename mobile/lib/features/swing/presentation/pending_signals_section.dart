import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import '../../../core/formatters/formatters.dart';
import '../../../core/theme/app_colors.dart';
import '../../../core/theme/app_text_styles.dart';
import '../../../core/widgets/app_button.dart';
import '../../../core/widgets/common_widgets.dart';
import '../../../core/widgets/confirm_dialog.dart';
import '../../../core/widgets/glass_card.dart';
import '../../../core/widgets/material_symbols.dart';
import '../application/swing_controller.dart';
import '../data/swing_repository.dart';

/// Reasoning longer than this starts collapsed behind "Show more".
const _reasoningCollapseChars = 240;

/// AI-scored swing and wheel candidates that wait for a human (spec
/// 2026-09-24 section 10). Shown on the instance detail screen above the
/// Stocks card when the strategy document has a swing or wheel lane.
class PendingSignalsSection extends ConsumerWidget {
  const PendingSignalsSection({super.key, required this.instanceId});

  final String instanceId;

  Future<void> _decide(BuildContext context, WidgetRef ref, SwingSignal signal,
      String decision) async {
    // No onConfirm callback: showConfirmDialog swallows errors thrown there,
    // and the operator has to see why a decision did not land.
    final confirmed = await showConfirmDialog(
      context,
      title: '${decisionLabel(decision)} ${signal.symbol}',
      body: decisionConfirmBody(signal, decision),
      confirmLabel: decisionLabel(decision),
      confirmColor: decision == 'reject' ? AppColors.danger : AppColors.success,
      icon: decision == 'reject' ? symbol('block') : symbol('check'),
    );
    if (!confirmed || !context.mounted) return;
    final result = await ref
        .read(pendingSignalsProvider(instanceId).notifier)
        .decide(signal, decision);
    // Follow-up 2: a 202's advice lives on its waiting card, not a snackbar.
    if (!context.mounted ||
        result.outcome == DecisionOutcome.ignored ||
        result.outcome == DecisionOutcome.uncertain) {
      return;
    }
    ScaffoldMessenger.of(context)
        .showSnackBar(SnackBar(content: Text(result.message)));
  }

  Future<void> _resend(
      BuildContext context, WidgetRef ref, SwingSignal signal) async {
    final confirmed = await showConfirmDialog(
      context,
      title: 'Re-send ${signal.symbol}',
      body: resendConfirmBody(signal),
      confirmLabel: 'Re-send',
      confirmColor: AppColors.warning,
      icon: symbol('send'),
    );
    if (!confirmed || !context.mounted) return;
    final result =
        await ref.read(pendingSignalsProvider(instanceId).notifier).resend(signal);
    if (!context.mounted ||
        result.outcome == DecisionOutcome.ignored ||
        result.outcome == DecisionOutcome.uncertain) {
      return;
    }
    ScaffoldMessenger.of(context)
        .showSnackBar(SnackBar(content: Text(result.message)));
  }

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final async = ref.watch(pendingSignalsProvider(instanceId));
    final count = async.valueOrNull?.signals.length;
    return GlassCard(
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Text(
            count == null ? 'Pending AI signals' : 'Pending AI signals ($count)',
            style: AppTextStyles.cardTitle,
          ),
          const SizedBox(height: 10),
          async.when(
            loading: () => Text('Loading…', style: AppTextStyles.meta),
            error: (err, _) => ErrorBanner(
              message: err.toString(),
              onRetry: () => ref.invalidate(pendingSignalsProvider(instanceId)),
            ),
            data: (state) => Column(
              crossAxisAlignment: CrossAxisAlignment.stretch,
              children: [
                if (state.refreshError != null) ...[
                  Text(
                    'Last refresh failed: ${state.refreshError}',
                    style: AppTextStyles.nano.copyWith(color: AppColors.warning),
                  ),
                  const SizedBox(height: 8),
                ],
                if (state.signals.isEmpty)
                  Text(
                    'Nothing waiting for review.',
                    style: AppTextStyles.meta
                        .copyWith(fontStyle: FontStyle.italic),
                  )
                else
                  for (final s in state.signals)
                    Padding(
                      padding: const EdgeInsets.only(bottom: 8),
                      child: _SignalCard(
                        key: ValueKey(s.id),
                        signal: s,
                        busy: state.isDeciding(s.id),
                        onDecide: (d) => _decide(context, ref, s, d),
                      ),
                    ),
                // 202'd approvals and re-sends (follow-up 2): the card and
                // its badge stay until a poll settles them.
                if (state.uncertain.isNotEmpty) ...[
                  const SizedBox(height: 8),
                  Text(
                    'Waiting for the broker (${state.uncertain.length})',
                    style: AppTextStyles.nano.copyWith(
                        color: AppColors.warning, letterSpacing: 0.6),
                  ),
                  const SizedBox(height: 6),
                  for (final card in state.uncertain)
                    Padding(
                      padding: const EdgeInsets.only(bottom: 8),
                      child: _UncertainCardView(
                        key: ValueKey('uncertain-${card.signal.id}'),
                        card: card,
                        onDismiss: () => ref
                            .read(pendingSignalsProvider(instanceId).notifier)
                            .dismissUncertain(card.signal.id),
                      ),
                    ),
                ],
                // Approvals no broker command has claimed for 2+ minutes
                // (fix wave item 3). Absent for an account with none.
                if (state.stuck.isNotEmpty) ...[
                  const SizedBox(height: 8),
                  Text(
                    'Approved, not yet sent (${state.stuck.length})',
                    style: AppTextStyles.nano.copyWith(
                        color: AppColors.warning, letterSpacing: 0.6),
                  ),
                  const SizedBox(height: 6),
                  for (final s in state.stuck)
                    Padding(
                      padding: const EdgeInsets.only(bottom: 8),
                      child: _StuckCard(
                        key: ValueKey('stuck-${s.id}'),
                        signal: s,
                        label: stuckLabel(s, state.asOf ?? DateTime.now()),
                        blockedReason: resendBlockedReason(
                            s, nyDate(state.asOf ?? DateTime.now())),
                        busy: state.isResending(s.id),
                        onResend: () => _resend(context, ref, s),
                      ),
                    ),
                ],
                const SizedBox(height: 4),
                Text(
                  'Approval rebuilds the order at the live price.',
                  style: AppTextStyles.nano.copyWith(color: AppColors.textFaint),
                ),
              ],
            ),
          ),
        ],
      ),
    );
  }
}

Color _scoreColor(int? score) {
  if (score == null) return AppColors.textMuted;
  if (score >= 75) return AppColors.success;
  if (score >= 50) return AppColors.warning;
  return AppColors.danger;
}

/// (label, value) pairs for the proposal grid.
List<(String, String)> proposalFields(SwingSignal s) {
  if (s.isWheel) {
    final credit = s.creditEst;
    return [
      ('CONTRACT', s.contract.isEmpty ? '—' : s.contract),
      ('STRIKE', fmtMoney(s.strike)),
      ('EXPIRY', s.expiry.isEmpty ? '—' : s.expiry),
      ('QTY', s.qty?.toString() ?? '—'),
      ('LIMIT', fmtMoney(s.limitPrice)),
      (
        'PREMIUM',
        credit == null
            ? fmtMoney(s.premiumEst)
            : '${fmtMoney(s.premiumEst)} (${fmtMoney(credit)})'
      ),
      ('COLLATERAL', fmtMoney(s.collateral)),
    ];
  }
  return [
    ('ENTRY', fmtMoney(s.entry)),
    ('STOP', fmtMoney(s.stop)),
    ('TARGET', fmtMoney(s.target)),
    ('SHARES', s.shares?.toString() ?? '—'),
  ];
}

class _SignalCard extends StatefulWidget {
  const _SignalCard({
    super.key,
    required this.signal,
    required this.busy,
    required this.onDecide,
  });

  final SwingSignal signal;
  final bool busy;
  final void Function(String decision) onDecide;

  @override
  State<_SignalCard> createState() => _SignalCardState();
}

class _SignalCardState extends State<_SignalCard> {
  bool _expanded = false;

  @override
  Widget build(BuildContext context) {
    final s = widget.signal;
    final longReasoning = s.reasoning.length > _reasoningCollapseChars;
    // Buttons go inert while this card's request is in flight.
    VoidCallback? tap(String decision) =>
        widget.busy ? null : () => widget.onDecide(decision);

    return Container(
      padding: const EdgeInsets.all(12),
      decoration: BoxDecoration(
        color: AppColors.surface.withValues(alpha: 0.5),
        borderRadius: BorderRadius.circular(10),
        border: Border.all(color: AppColors.border),
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Row(
            children: [
              Text(
                s.symbol,
                style: AppTextStyles.cardTitle.copyWith(
                    color: AppColors.textHi, fontWeight: FontWeight.w800),
              ),
              const SizedBox(width: 8),
              AppBadge(
                label: s.lane,
                color: s.isWheel ? AppColors.primary : AppColors.info,
              ),
              const SizedBox(width: 6),
              AppBadge(
                  label: s.score?.toString() ?? '—',
                  color: _scoreColor(s.score)),
              const SizedBox(width: 8),
              Expanded(
                child: Text(
                  'session ${s.session.isEmpty ? '—' : s.session}',
                  textAlign: TextAlign.end,
                  overflow: TextOverflow.ellipsis,
                  style: AppTextStyles.nano.copyWith(color: AppColors.textFaint),
                ),
              ),
            ],
          ),
          const SizedBox(height: 10),
          Wrap(
            spacing: 16,
            runSpacing: 8,
            children: [
              for (final (label, value) in proposalFields(s))
                Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  mainAxisSize: MainAxisSize.min,
                  children: [
                    Text(label,
                        style: AppTextStyles.nano.copyWith(
                            color: AppColors.textDim, letterSpacing: 0.4)),
                    Text(value,
                        style: AppTextStyles.micro.copyWith(
                            color: AppColors.textMd,
                            fontWeight: FontWeight.w700)),
                  ],
                ),
            ],
          ),
          if (s.reasoning.trim().isNotEmpty) ...[
            const SizedBox(height: 10),
            Text(
              s.reasoning.trim(),
              style: AppTextStyles.micro.copyWith(color: AppColors.textMd),
              maxLines: _expanded || !longReasoning ? null : 4,
              overflow: _expanded || !longReasoning
                  ? TextOverflow.visible
                  : TextOverflow.ellipsis,
            ),
            if (longReasoning)
              TextButton(
                onPressed: () => setState(() => _expanded = !_expanded),
                style: TextButton.styleFrom(
                  padding: EdgeInsets.zero,
                  minimumSize: const Size(0, 32),
                  tapTargetSize: MaterialTapTargetSize.shrinkWrap,
                ),
                child: Text(_expanded ? 'Show less' : 'Show more'),
              ),
          ],
          if (s.keyRisksText.isNotEmpty) ...[
            const SizedBox(height: 6),
            Text(
              'Risks: ${s.keyRisksText}',
              style: AppTextStyles.nano.copyWith(color: AppColors.warning),
            ),
          ],
          const SizedBox(height: 10),
          // A Wrap rather than a Row of Expanded: three labelled buttons do
          // not fit a 320pt-wide phone on one line.
          Wrap(
            spacing: 8,
            runSpacing: 8,
            children: [
              AppButton.semantic(
                label: decisionLabel('approve'),
                color: AppColors.success,
                dense: true,
                onPressed: tap('approve'),
              ),
              if (s.allowsHalf)
                AppButton.semantic(
                  label: decisionLabel('approve_half'),
                  color: AppColors.success,
                  dense: true,
                  onPressed: tap('approve_half'),
                ),
              AppButton.ghost(
                label: decisionLabel('reject'),
                dense: true,
                onPressed: tap('reject'),
              ),
            ],
          ),
          if (widget.busy) ...[
            const SizedBox(height: 6),
            Text('Working…',
                style: AppTextStyles.nano.copyWith(color: AppColors.textDim)),
          ],
        ],
      ),
    );
  }
}

/// An approval no broker command has claimed for 2+ minutes: the instance
/// stopped, or the broker's handler returned early. Re-send queues the same
/// command again; the broker ignores a copy it already claimed.
class _StuckCard extends StatelessWidget {
  const _StuckCard({
    super.key,
    required this.signal,
    required this.label,
    required this.blockedReason,
    required this.busy,
    required this.onResend,
  });

  final SwingSignal signal;
  final String label;

  /// Why Re-send is not offered (an approval from an older session), or null.
  final String? blockedReason;
  final bool busy;
  final VoidCallback onResend;

  @override
  Widget build(BuildContext context) {
    final s = signal;
    return Container(
      padding: const EdgeInsets.all(12),
      decoration: BoxDecoration(
        color: AppColors.warning.withValues(alpha: 0.06),
        borderRadius: BorderRadius.circular(10),
        border: Border.all(color: AppColors.warning.withValues(alpha: 0.3)),
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Row(
            children: [
              Text(
                s.symbol,
                style: AppTextStyles.cardTitle.copyWith(
                    color: AppColors.textHi, fontWeight: FontWeight.w800),
              ),
              const SizedBox(width: 8),
              AppBadge(
                label: s.lane,
                color: s.isWheel ? AppColors.primary : AppColors.info,
              ),
              const SizedBox(width: 6),
              AppBadge(
                label: s.status == 'approved_half' ? 'approved ½' : 'approved',
                color: AppColors.warning,
              ),
            ],
          ),
          const SizedBox(height: 4),
          Text('session ${s.session.isEmpty ? '—' : s.session}',
              style: AppTextStyles.nano.copyWith(color: AppColors.textFaint)),
          const SizedBox(height: 6),
          Text(label,
              style: AppTextStyles.micro.copyWith(color: AppColors.textMd)),
          const SizedBox(height: 10),
          if (blockedReason != null)
            Text(blockedReason!,
                style: AppTextStyles.nano.copyWith(color: AppColors.textDim))
          else
            AppButton.semantic(
              label: 'Re-send',
              color: AppColors.warning,
              dense: true,
              onPressed: busy ? null : onResend,
            ),
          if (busy) ...[
            const SizedBox(height: 6),
            Text('Working…',
                style: AppTextStyles.nano.copyWith(color: AppColors.textDim)),
          ],
        ],
      ),
    );
  }
}

/// A 202'd approval or re-send (follow-up 2): "uncertain — waiting for the
/// broker" with the server's advice until a poll finds the signal pending
/// (the card goes), submitted or failed (the badge says so until Dismiss).
class _UncertainCardView extends StatelessWidget {
  const _UncertainCardView({
    super.key,
    required this.card,
    required this.onDismiss,
  });

  final UncertainCard card;
  final VoidCallback onDismiss;

  Color get _tone => switch (card.resolved) {
        'submitted' => AppColors.success,
        'failed' => AppColors.danger,
        _ => AppColors.warning,
      };

  @override
  Widget build(BuildContext context) {
    final s = card.signal;
    return Container(
      padding: const EdgeInsets.all(12),
      decoration: BoxDecoration(
        color: _tone.withValues(alpha: 0.06),
        borderRadius: BorderRadius.circular(10),
        border: Border.all(color: _tone.withValues(alpha: 0.3)),
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Row(
            children: [
              Text(
                s.symbol,
                style: AppTextStyles.cardTitle.copyWith(
                    color: AppColors.textHi, fontWeight: FontWeight.w800),
              ),
              const SizedBox(width: 8),
              AppBadge(
                label: s.lane,
                color: s.isWheel ? AppColors.primary : AppColors.info,
              ),
            ],
          ),
          const SizedBox(height: 6),
          AppBadge(label: card.badge, color: _tone),
          const SizedBox(height: 4),
          Text('session ${s.session.isEmpty ? '—' : s.session}',
              style: AppTextStyles.nano.copyWith(color: AppColors.textFaint)),
          const SizedBox(height: 6),
          Text(
            switch (card.resolved) {
              'submitted' =>
                'The broker submitted it. Check open orders for the fill.',
              'failed' => 'It failed at the broker; the live log says why.',
              _ => card.message,
            },
            style: AppTextStyles.micro.copyWith(color: AppColors.textMd),
          ),
          if (card.resolved != null) ...[
            const SizedBox(height: 10),
            AppButton.ghost(label: 'Dismiss', dense: true, onPressed: onDismiss),
          ],
        ],
      ),
    );
  }
}
