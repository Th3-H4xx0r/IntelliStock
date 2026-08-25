import 'dart:async';
import 'dart:math' as math;

import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';

import '../../../core/network/api_error.dart';
import '../../../core/theme/app_colors.dart';
import '../../../core/theme/app_text_styles.dart';
import '../../../core/widgets/app_background.dart';
import '../../../core/widgets/app_button.dart';
import '../../../core/widgets/glass_card.dart';
import '../../../core/widgets/material_symbols.dart';
import '../../../core/widgets/skeleton.dart';
import '../../live_trading/data/live_repository.dart';
import '../data/symbol_search_models.dart';
import '../data/symbol_search_repository.dart';

String searchUnavailableMessage(String message) {
  if (message == 'Not Found') {
    return 'Search is taking a moment to come online. Your dashboard is still up to date.';
  }
  return 'We could not reach market search. Check your connection and try again.';
}

class SymbolSearchScreen extends ConsumerStatefulWidget {
  const SymbolSearchScreen({super.key});

  @override
  ConsumerState<SymbolSearchScreen> createState() => _SymbolSearchScreenState();
}

class _SymbolSearchScreenState extends ConsumerState<SymbolSearchScreen> {
  final _controller = TextEditingController();
  final _focusNode = FocusNode();
  Timer? _debounce;
  List<SearchInstrument>? _results;
  String? _error;
  bool _loading = false;

  @override
  void initState() {
    super.initState();
    WidgetsBinding.instance.addPostFrameCallback((_) => _focusNode.requestFocus());
  }

  @override
  void dispose() {
    _debounce?.cancel();
    _controller.dispose();
    _focusNode.dispose();
    super.dispose();
  }

  void _onQueryChanged(String query) {
    _debounce?.cancel();
    final trimmed = query.trim();
    if (trimmed.isEmpty) {
      setState(() {
        _results = null;
        _error = null;
        _loading = false;
      });
      return;
    }
    setState(() {
      _results = null;
      _loading = true;
      _error = null;
    });
    _debounce = Timer(const Duration(milliseconds: 250), () async {
      try {
        final results = await ref.read(symbolSearchRepositoryProvider).search(trimmed);
        if (!mounted || _controller.text.trim() != trimmed) return;
        setState(() {
          _results = results;
          _loading = false;
        });
      } on ApiError catch (error) {
        if (!mounted || _controller.text.trim() != trimmed) return;
        setState(() {
          _error = error.message;
          _loading = false;
        });
      } catch (_) {
        if (!mounted || _controller.text.trim() != trimmed) return;
        setState(() {
          _error = 'Couldn\'t search symbols right now.';
          _loading = false;
        });
      }
    });
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      backgroundColor: AppColors.canvas,
      body: AppBackground(
        child: SafeArea(
          child: Column(
            children: [
              _header(context),
              Expanded(child: _body()),
            ],
          ),
        ),
      ),
    );
  }

  Widget _header(BuildContext context) {
    return Padding(
      padding: const EdgeInsets.fromLTRB(16, 10, 16, 12),
      child: Row(
        children: [
          IconButton(
            tooltip: 'Back',
            onPressed: () => context.pop(),
            icon: Icon(symbol('arrow_back'), color: AppColors.textHi),
          ),
          const SizedBox(width: 4),
          Expanded(
            child: DecoratedBox(
              decoration: BoxDecoration(
                color: Colors.white.withValues(alpha: 0.08),
                borderRadius: BorderRadius.circular(14),
                border: Border.all(color: Colors.white.withValues(alpha: 0.10)),
              ),
              child: TextField(
                controller: _controller,
                focusNode: _focusNode,
                onChanged: _onQueryChanged,
                textInputAction: TextInputAction.search,
                style: AppTextStyles.bodyHi,
                decoration: InputDecoration(
                  hintText: 'Search stocks, ETFs, crypto',
                  hintStyle: AppTextStyles.body.copyWith(color: AppColors.textDim),
                  prefixIcon: Icon(symbol('search'), color: AppColors.textMuted),
                  suffixIcon: _controller.text.isEmpty
                      ? null
                      : IconButton(
                          tooltip: 'Clear',
                          onPressed: () {
                            _controller.clear();
                            _onQueryChanged('');
                          },
                          icon: Icon(symbol('close'), color: AppColors.textMuted),
                        ),
                  border: InputBorder.none,
                  contentPadding: const EdgeInsets.symmetric(vertical: 15),
                ),
              ),
            ),
          ),
        ],
      ),
    );
  }

  Widget _body() {
    if (_loading && _results == null) return const _SearchSkeletonList();
    if (_error != null) {
      return _SearchErrorState(
        message: _error!,
        onRetry: () => _onQueryChanged(_controller.text),
      );
    }
    final results = _results;
    if (results == null) {
      return Center(
        child: Text(
          'Find an investment',
          style: AppTextStyles.body.copyWith(color: AppColors.textDim),
        ),
      );
    }
    if (results.isEmpty) {
      return Center(
        child: Text(
          'No matching symbols',
          style: AppTextStyles.body.copyWith(color: AppColors.textDim),
        ),
      );
    }
    final symbols = searchSymbolsForSparklines(results);
    final sparklines = ref.watch(searchSparklinesProvider(symbols.join(',')));
    return ListView.separated(
      padding: const EdgeInsets.fromLTRB(16, 4, 16, 28),
      itemCount: results.length,
      separatorBuilder: (_, _) => const SizedBox(height: 8),
      itemBuilder: (_, index) => _SearchResultRow(
        result: results[index],
        sparkline: sparklines.valueOrNull?[results[index].symbol],
        sparklineLoading: sparklines.isLoading,
      ),
    );
  }
}

class _SearchErrorState extends StatelessWidget {
  const _SearchErrorState({required this.message, required this.onRetry});

  final String message;
  final VoidCallback onRetry;

  @override
  Widget build(BuildContext context) => Center(
        child: ConstrainedBox(
          constraints: const BoxConstraints(maxWidth: 360),
          child: Padding(
            padding: const EdgeInsets.symmetric(horizontal: 24),
            child: GlassCard(
              padding: const EdgeInsets.fromLTRB(24, 28, 24, 24),
              child: Column(
                mainAxisSize: MainAxisSize.min,
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Container(
                    width: 48,
                    height: 48,
                    decoration: BoxDecoration(
                      color: AppColors.fill(AppColors.primary),
                      border: Border.all(color: AppColors.stroke(AppColors.primary)),
                      borderRadius: BorderRadius.circular(15),
                    ),
                    child: Icon(symbol('query_stats'), color: AppColors.primary),
                  ),
                  const SizedBox(height: 22),
                  Text('MARKET SEARCH', style: AppTextStyles.eyebrow),
                  const SizedBox(height: 6),
                  Text('We’re reconnecting', style: AppTextStyles.h2),
                  const SizedBox(height: 8),
                  Text(
                    searchUnavailableMessage(message),
                    style: AppTextStyles.body.copyWith(color: AppColors.textMuted, height: 1.45),
                  ),
                  const SizedBox(height: 24),
                  SizedBox(
                    width: double.infinity,
                    child: AppButton.primary(
                      label: 'Retry search',
                      icon: symbol('refresh'),
                      onPressed: onRetry,
                    ),
                  ),
                ],
              ),
            ),
          ),
        ),
      );
}

class _SearchResultRow extends StatelessWidget {
  const _SearchResultRow({
    required this.result,
    required this.sparkline,
    required this.sparklineLoading,
  });

  final SearchInstrument result;
  final List<double>? sparkline;
  final bool sparklineLoading;

  @override
  Widget build(BuildContext context) {
    return GlassCard(
      onTap: () => context.push('/stock/${Uri.encodeComponent(result.symbol)}'),
      padding: const EdgeInsets.symmetric(horizontal: 14, vertical: 12),
      child: Row(
        children: [
          _TypeBadge(type: result.type),
          const SizedBox(width: 12),
          Expanded(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Text(result.symbol, style: AppTextStyles.bodyHi.copyWith(fontWeight: FontWeight.w800)),
                const SizedBox(height: 3),
                Text(
                  result.name,
                  maxLines: 1,
                  overflow: TextOverflow.ellipsis,
                  style: AppTextStyles.micro.copyWith(color: AppColors.textMuted),
                ),
              ],
            ),
          ),
          const SizedBox(width: 12),
          SizedBox(
            width: 72,
            height: 30,
            child: sparklineLoading
                ? const Skeleton(height: 30, radius: 7)
                : (sparkline == null || sparkline!.length < 2)
                    ? const SizedBox.shrink()
                    : CustomPaint(painter: _SparklinePainter(sparkline!)),
          ),
          Icon(symbol('arrow_forward'), size: 18, color: AppColors.textDim),
        ],
      ),
    );
  }
}

class _TypeBadge extends StatelessWidget {
  const _TypeBadge({required this.type});

  final String type;

  @override
  Widget build(BuildContext context) {
    final icon = switch (type) {
      'ETF' => 'stacked_line_chart',
      'Crypto' => 'currency_bitcoin',
      _ => 'business',
    };
    return Container(
      width: 38,
      height: 38,
      decoration: BoxDecoration(
        color: AppColors.fill(AppColors.primary),
        borderRadius: BorderRadius.circular(11),
      ),
      child: Icon(symbol(icon), size: 20, color: AppColors.primary),
    );
  }
}

class _SearchSkeletonList extends StatelessWidget {
  const _SearchSkeletonList();

  @override
  Widget build(BuildContext context) => ListView.separated(
        padding: const EdgeInsets.fromLTRB(16, 4, 16, 28),
        itemCount: 6,
        separatorBuilder: (_, _) => const SizedBox(height: 8),
        itemBuilder: (_, _) => const GlassCard(
          padding: EdgeInsets.all(14),
          child: Row(
            children: [
              Skeleton(width: 38, height: 38, radius: 11),
              SizedBox(width: 12),
              Expanded(
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Skeleton(width: 72, height: 15, radius: 4),
                    SizedBox(height: 7),
                    Skeleton(width: 160, height: 12, radius: 4),
                  ],
                ),
              ),
              SizedBox(width: 12),
              Skeleton(width: 72, height: 30, radius: 7),
            ],
          ),
        ),
      );
}

final searchSparklinesProvider = FutureProvider.autoDispose
    .family<Map<String, List<double>>, String>((ref, symbolsCsv) async {
  final symbols = symbolsCsv.split(',').where((symbol) => symbol.isNotEmpty).toList();
  final values = await ref
      .read(liveRepositoryProvider)
      .symbolHistoricals(symbols, '1D');
  return values.map(
    (symbol, points) => MapEntry(
      symbol,
      points.map((point) => point.value).toList(),
    ),
  );
});

class _SparklinePainter extends CustomPainter {
  _SparklinePainter(this.values);

  final List<double> values;

  @override
  void paint(Canvas canvas, Size size) {
    final minValue = values.reduce(math.min);
    final maxValue = values.reduce(math.max);
    final delta = maxValue - minValue;
    final up = values.last >= values.first;
    final paint = Paint()
      ..color = up ? AppColors.success : AppColors.danger
      ..style = PaintingStyle.stroke
      ..strokeWidth = 1.8
      ..strokeCap = StrokeCap.round
      ..strokeJoin = StrokeJoin.round;
    final path = Path();
    for (var index = 0; index < values.length; index++) {
      final x = size.width * index / (values.length - 1);
      final y = delta == 0
          ? size.height / 2
          : size.height - ((values[index] - minValue) / delta * size.height);
      if (index == 0) {
        path.moveTo(x, y);
      } else {
        path.lineTo(x, y);
      }
    }
    canvas.drawPath(path, paint);
  }

  @override
  bool shouldRepaint(_SparklinePainter oldDelegate) => oldDelegate.values != values;
}
