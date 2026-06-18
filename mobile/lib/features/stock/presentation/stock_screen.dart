import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import '../../../core/charts/scrubbable_area_chart.dart';
import '../../../core/formatters/formatters.dart';
import '../../../core/theme/app_colors.dart';
import '../../../core/theme/app_text_styles.dart';
import '../../../core/widgets/glass_card.dart';
import '../../dashboard/data/dashboard_repository.dart';
import '../../live_trading/data/models/live_state.dart';
import '../application/stock_controller.dart';

const _ranges = ['1D', '1W', '1M', '3M', 'YTD', '1Y', 'ALL'];

/// Passed as go_router `extra` when navigating from a holding so the screen has
/// the position + brokerage immediately (without re-fetching).
class StockScreenArgs {
  const StockScreenArgs({this.position, this.brokerageId});
  final AccountPosition? position;
  final String? brokerageId;
}

/// Full-screen view for one stock: price + range chart (1D…ALL, since 12 AM for
/// 1D), range stats, the user's position, and recent orders for the symbol.
class StockScreen extends ConsumerStatefulWidget {
  const StockScreen({
    super.key,
    required this.symbol,
    this.position,
    this.brokerageId,
  });

  final String symbol;
  final AccountPosition? position;
  final String? brokerageId;

  @override
  ConsumerState<StockScreen> createState() => _StockScreenState();
}

class _StockScreenState extends ConsumerState<StockScreen> {
  String _range = '1D';
  final ValueNotifier<int?> _scrub = ValueNotifier<int?>(null);

  @override
  void dispose() {
    _scrub.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    final histAsync = ref.watch(
      stockHistoryProvider((symbol: widget.symbol, range: _range)),
    );
    return Scaffold(
      backgroundColor: AppColors.canvas,
      appBar: AppBar(
        backgroundColor: AppColors.canvas,
        elevation: 0,
        title: Text(widget.symbol, style: AppTextStyles.h2),
      ),
      body: ListView(
        padding: const EdgeInsets.fromLTRB(16, 4, 16, 32),
        children: [
          GlassCard(
            child: histAsync.when(
              loading: () => const SizedBox(
                height: 300,
                child:
                    Center(child: CircularProgressIndicator(strokeWidth: 2)),
              ),
              error: (e, _) => SizedBox(
                height: 120,
                child: Center(
                  child: Text("Couldn't load $_range prices",
                      style: AppTextStyles.micro
                          .copyWith(color: AppColors.danger)),
                ),
              ),
              data: _chartCardBody,
            ),
          ),
          if (widget.position != null) ...[
            const SizedBox(height: 16),
            _positionCard(widget.position!),
          ],
          const SizedBox(height: 16),
          _ordersCard(),
        ],
      ),
    );
  }

  Widget _chartCardBody(StockSeries series) {
    final vals = series.vals;
    if (vals.length < 2) {
      return SizedBox(
        height: 120,
        child: Center(
          child: Text('No price data for $_range',
              style: AppTextStyles.micro.copyWith(color: AppColors.textDim)),
        ),
      );
    }
    final first = vals.first;
    final last = vals.last;
    final high = vals.reduce((a, b) => a > b ? a : b);
    final low = vals.reduce((a, b) => a < b ? a : b);
    final color = last >= first ? AppColors.success : AppColors.danger;

    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        // Scrub-aware price + change for the selected range.
        ValueListenableBuilder<int?>(
          valueListenable: _scrub,
          builder: (_, idx, _) {
            final shown = (idx != null && idx >= 0 && idx < vals.length)
                ? vals[idx]
                : last;
            final dAbs = shown - first;
            final dPct = first != 0 ? (dAbs / first) * 100 : 0.0;
            final c = dAbs >= 0 ? AppColors.success : AppColors.danger;
            return Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Text(fmtMoney(shown), style: AppTextStyles.valueXl),
                const SizedBox(height: 2),
                Text('${fmtPnl(dAbs)} · ${fmtPct(dPct)}   $_range',
                    style: AppTextStyles.micro
                        .copyWith(color: c, fontWeight: FontWeight.w700)),
              ],
            );
          },
        ),
        const SizedBox(height: 12),
        ScrubbableAreaChart(
          timestamps: series.ts,
          values: vals,
          lineColor: color,
          height: 200,
          onScrub: (i) => _scrub.value = i,
        ),
        const SizedBox(height: 12),
        _rangeTabs(),
        const SizedBox(height: 14),
        Row(children: [
          _stat('Open', fmtMoney(first)),
          _stat('High', fmtMoney(high)),
          _stat('Low', fmtMoney(low)),
        ]),
      ],
    );
  }

  Widget _rangeTabs() {
    return Container(
      padding: const EdgeInsets.all(3),
      decoration: BoxDecoration(
        color: const Color(0x0FFFFFFF),
        borderRadius: BorderRadius.circular(10),
        border: Border.all(color: const Color(0x0DFFFFFF)),
      ),
      child: Row(
        children: _ranges.map((r) {
          final isActive = r == _range;
          return Expanded(
            child: GestureDetector(
              behavior: HitTestBehavior.opaque,
              onTap: () {
                if (r != _range) {
                  _scrub.value = null;
                  setState(() => _range = r);
                }
              },
              child: AnimatedContainer(
                duration: const Duration(milliseconds: 160),
                curve: Curves.easeOut,
                padding: const EdgeInsets.symmetric(vertical: 7),
                decoration: BoxDecoration(
                  color:
                      isActive ? const Color(0x24FFFFFF) : Colors.transparent,
                  borderRadius: BorderRadius.circular(7),
                ),
                child: Text(r,
                    textAlign: TextAlign.center,
                    style: AppTextStyles.micro.copyWith(
                      color: isActive ? AppColors.textHi : AppColors.textDim,
                      fontWeight:
                          isActive ? FontWeight.w700 : FontWeight.w500,
                    )),
              ),
            ),
          );
        }).toList(),
      ),
    );
  }

  Widget _stat(String label, String value) {
    return Expanded(
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Text(label.toUpperCase(),
              style: AppTextStyles.nano.copyWith(color: AppColors.textFaint)),
          const SizedBox(height: 3),
          Text(value, style: AppTextStyles.value),
        ],
      ),
    );
  }

  Widget _positionCard(AccountPosition p) {
    final color = p.unrealizedPnl >= 0 ? AppColors.success : AppColors.danger;
    final qty = p.qty;
    final qtyStr = qty == qty.roundToDouble()
        ? qty.toInt().toString()
        : qty.toStringAsFixed(2);
    return GlassCard(
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Text('Your position', style: AppTextStyles.h3),
          const SizedBox(height: 12),
          Row(children: [
            _stat('Shares', qtyStr),
            _stat('Market value', fmtMoney(p.marketValue)),
            _stat('Avg entry', fmtMoney(p.avgEntryPrice)),
          ]),
          const SizedBox(height: 14),
          Text('TOTAL P&L',
              style: AppTextStyles.nano.copyWith(color: AppColors.textFaint)),
          const SizedBox(height: 3),
          Text(
              '${fmtPnl(p.unrealizedPnl)} · ${fmtPct(p.unrealizedPnlPct)}',
              style: AppTextStyles.valueLg.copyWith(color: color)),
        ],
      ),
    );
  }

  Widget _ordersCard() {
    final bid = widget.brokerageId;
    return GlassCard(
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Text('Order history', style: AppTextStyles.h3),
          const SizedBox(height: 4),
          if (bid == null)
            _ordersEmpty('No linked brokerage')
          else
            Consumer(builder: (context, ref, _) {
              final ordersAsync = ref.watch(
                stockOrdersProvider(
                    (brokerageId: bid, symbol: widget.symbol)),
              );
              return ordersAsync.when(
                loading: () => const Padding(
                  padding: EdgeInsets.symmetric(vertical: 16),
                  child: Center(
                      child: SizedBox(
                          width: 18,
                          height: 18,
                          child:
                              CircularProgressIndicator(strokeWidth: 2))),
                ),
                error: (_, _) => _ordersEmpty("Couldn't load orders"),
                data: (orders) => orders.isEmpty
                    ? _ordersEmpty('No recent orders for ${widget.symbol}')
                    : Column(
                        children: [for (final t in orders) _OrderRow(trade: t)],
                      ),
              );
            }),
        ],
      ),
    );
  }

  Widget _ordersEmpty(String msg) => Padding(
        padding: const EdgeInsets.symmetric(vertical: 12),
        child: Text(msg,
            style: AppTextStyles.micro.copyWith(color: AppColors.textDim)),
      );
}

class _OrderRow extends StatelessWidget {
  const _OrderRow({required this.trade});
  final Trade trade;

  @override
  Widget build(BuildContext context) {
    final isBuy = trade.side.toLowerCase() == 'buy';
    final color = isBuy ? AppColors.success : AppColors.danger;
    final qty = trade.qty;
    final qtyStr = qty == qty.roundToDouble()
        ? qty.toInt().toString()
        : qty.toStringAsFixed(2);
    return Padding(
      padding: const EdgeInsets.symmetric(vertical: 8),
      child: Row(
        children: [
          Container(
            padding: const EdgeInsets.symmetric(horizontal: 7, vertical: 3),
            decoration: BoxDecoration(
              color: AppColors.fill(color),
              borderRadius: BorderRadius.circular(5),
            ),
            child: Text(trade.side.toUpperCase(),
                style: AppTextStyles.nano
                    .copyWith(color: color, fontWeight: FontWeight.w800)),
          ),
          const SizedBox(width: 10),
          Expanded(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Text('$qtyStr @ ${fmtMoney(trade.price)}',
                    style: AppTextStyles.bodyHi
                        .copyWith(fontWeight: FontWeight.w600)),
                const SizedBox(height: 2),
                Text(fmtDateTime(trade.ts),
                    style:
                        AppTextStyles.nano.copyWith(color: AppColors.textDim)),
              ],
            ),
          ),
          Text(fmtMoney(trade.price * trade.qty), style: AppTextStyles.value),
        ],
      ),
    );
  }
}
