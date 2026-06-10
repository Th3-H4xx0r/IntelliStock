import 'package:flutter/material.dart';
import 'package:flutter_markdown/flutter_markdown.dart';
import 'package:syncfusion_flutter_charts/charts.dart';
import '../../../core/theme/app_colors.dart';
import '../../../core/theme/app_text_styles.dart';

/// Renders a single rich block emitted by the assistant.
///
/// Block types handled:
///  • `markdown` — rendered via flutter_markdown
///  • `table`    — vertical scroll inside a GlassCard-style container
///  • `chart`    — bar/line with Syncfusion; minimal but functional
///  • `stat`     — single stat with optional trend
///  • `navigate` — non-intrusive navigation pill (the dock also catches these)
///  • everything else → italic fallback
class ChatRichBlock extends StatelessWidget {
  const ChatRichBlock({super.key, required this.block});

  final Map<String, dynamic> block;

  @override
  Widget build(BuildContext context) {
    final type = (block['type'] ?? '').toString();
    return switch (type) {
      'markdown' => _MarkdownBlock(block: block),
      'table' => _TableBlock(block: block),
      'chart' || 'portfolio' || 'price' => _ChartBlock(block: block),
      'stat' => _StatBlock(block: block),
      'navigate' => _NavigateBlock(block: block),
      _ => Text(
          '(unsupported block: $type)',
          style: AppTextStyles.micro.copyWith(
            color: AppColors.textDim,
            fontStyle: FontStyle.italic,
          ),
        ),
    };
  }
}

// ── Markdown ──────────────────────────────────────────────────────────────────

class _MarkdownBlock extends StatelessWidget {
  const _MarkdownBlock({required this.block});
  final Map<String, dynamic> block;

  @override
  Widget build(BuildContext context) {
    final content = (block['content'] ?? '').toString();
    return MarkdownBody(
      data: content,
      selectable: true,
      styleSheet: MarkdownStyleSheet(
        p: AppTextStyles.body.copyWith(color: AppColors.textMd),
        code: AppTextStyles.mono(12, color: const Color(0xFFD8B4FE)),
        codeblockDecoration: BoxDecoration(
          color: AppColors.surface,
          borderRadius: BorderRadius.circular(8),
          border: Border.all(color: AppColors.border),
        ),
        blockquoteDecoration: BoxDecoration(
          border: Border(
            left: BorderSide(color: AppColors.border, width: 2),
          ),
        ),
        blockquote: AppTextStyles.body.copyWith(color: AppColors.textDim),
        h1: AppTextStyles.h2.copyWith(color: AppColors.textHi),
        h2: AppTextStyles.h3.copyWith(color: AppColors.textHi),
        h3: AppTextStyles.cardTitle.copyWith(color: AppColors.textHi),
        a: AppTextStyles.body.copyWith(
          color: AppColors.primary,
          decoration: TextDecoration.underline,
        ),
        listBullet: AppTextStyles.body.copyWith(color: AppColors.textMd),
        tableHead: AppTextStyles.micro.copyWith(
          color: AppColors.textDim,
          fontWeight: FontWeight.w600,
        ),
        tableBody: AppTextStyles.body.copyWith(color: AppColors.textMd),
        tableBorder: TableBorder.all(
          color: AppColors.border,
          width: 1,
        ),
      ),
    );
  }
}

// ── Table ─────────────────────────────────────────────────────────────────────

class _TableBlock extends StatelessWidget {
  const _TableBlock({required this.block});
  final Map<String, dynamic> block;

  @override
  Widget build(BuildContext context) {
    final title = block['title']?.toString();
    final headers = (block['headers'] as List? ?? const [])
        .whereType<Map<String, dynamic>>()
        .toList();
    final rows = (block['rows'] as List? ?? const [])
        .whereType<Map<String, dynamic>>()
        .toList();

    return Container(
      decoration: BoxDecoration(
        color: AppColors.surface.withValues(alpha: 0.5),
        borderRadius: BorderRadius.circular(12),
        border: Border.all(color: AppColors.border),
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          if (title != null)
            Padding(
              padding: const EdgeInsets.fromLTRB(12, 10, 12, 4),
              child: Text(
                title.toUpperCase(),
                style: AppTextStyles.eyebrow.copyWith(color: AppColors.textDim),
              ),
            ),
          SingleChildScrollView(
            scrollDirection: Axis.horizontal,
            child: DataTable(
              headingRowColor: WidgetStateProperty.all(
                  AppColors.surface.withValues(alpha: 0.8)),
              dataRowColor: WidgetStateProperty.all(Colors.transparent),
              dividerThickness: 0.5,
              columnSpacing: 16,
              headingTextStyle: AppTextStyles.nano.copyWith(
                color: AppColors.textDim,
                fontWeight: FontWeight.w600,
              ),
              dataTextStyle:
                  AppTextStyles.body.copyWith(color: AppColors.textMd),
              columns: [
                for (final h in headers)
                  DataColumn(
                    label: Text(
                      (h['label'] ?? h['key'] ?? '').toString(),
                    ),
                  ),
              ],
              rows: [
                for (final row in rows)
                  DataRow(
                    cells: [
                      for (final h in headers)
                        DataCell(
                          Text(
                            _fmtCell(row[(h['key'] ?? h['label'])]),
                          ),
                        ),
                    ],
                  ),
              ],
            ),
          ),
        ],
      ),
    );
  }

  String _fmtCell(dynamic v) {
    if (v == null) return '';
    if (v is num) return v.toStringAsFixed(v.truncateToDouble() == v ? 0 : 2);
    return v.toString();
  }
}

// ── Chart ─────────────────────────────────────────────────────────────────────

/// Minimal chart block — renders a bar or line chart using Syncfusion.
/// The frontend uses ApexCharts; here we map the same block shapes to
/// Syncfusion equivalents.  Portfolio/price blocks use a line series;
/// generic chart blocks use the `chart_type` hint (defaults to 'bar').
class _ChartBlock extends StatelessWidget {
  const _ChartBlock({required this.block});
  final Map<String, dynamic> block;

  @override
  Widget build(BuildContext context) {
    final type = (block['type'] ?? 'chart').toString();
    final title = block['title']?.toString();
    final chartType = (block['chart_type'] ?? 'bar').toString();

    // Build series data from the block structure.
    // Generic chart: `series: [{name, data: [[x, y], ...] | [y, ...]}]`
    // Portfolio/price: `series: [{symbol, points: [{ts, value}]}]` or
    //                  `timestamps + values` (portfolio flat arrays).
    final List<CartesianSeries> seriesList = _buildSeries(type, chartType);

    if (seriesList.isEmpty) {
      return Text(
        '(no chart data)',
        style: AppTextStyles.micro.copyWith(
          color: AppColors.textDim,
          fontStyle: FontStyle.italic,
        ),
      );
    }

    return Container(
      decoration: BoxDecoration(
        color: AppColors.surface.withValues(alpha: 0.5),
        borderRadius: BorderRadius.circular(12),
        border: Border.all(color: AppColors.border),
      ),
      padding: const EdgeInsets.all(12),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          if (title != null)
            Padding(
              padding: const EdgeInsets.only(bottom: 8),
              child: Text(
                title.toUpperCase(),
                style:
                    AppTextStyles.eyebrow.copyWith(color: AppColors.textDim),
              ),
            ),
          SizedBox(
            height: 200,
            child: SfCartesianChart(
              backgroundColor: Colors.transparent,
              plotAreaBorderWidth: 0,
              primaryXAxis: CategoryAxis(
                majorGridLines: const MajorGridLines(width: 0),
                axisLine: const AxisLine(width: 0),
                labelStyle: AppTextStyles.nano.copyWith(
                  color: AppColors.chartAxis,
                ),
              ),
              primaryYAxis: NumericAxis(
                majorGridLines: MajorGridLines(
                  color: AppColors.chartGrid,
                  dashArray: const [4, 4],
                ),
                axisLine: const AxisLine(width: 0),
                labelStyle: AppTextStyles.nano.copyWith(
                  color: AppColors.chartAxis,
                ),
              ),
              series: seriesList,
            ),
          ),
        ],
      ),
    );
  }

  List<CartesianSeries> _buildSeries(String type, String chartType) {
    final seriesRaw =
        (block['series'] as List? ?? const []).whereType<Map>().toList();
    final colors = [
      AppColors.primary,
      AppColors.success,
      AppColors.info,
      AppColors.warning,
      AppColors.danger,
    ];

    // Portfolio flat arrays
    if (type == 'portfolio') {
      final tsArr = block['timestamps'] as List? ?? const [];
      final vals = block['values'] as List? ?? const [];
      final pts = <_XY>[];
      final n = tsArr.length < vals.length ? tsArr.length : vals.length;
      for (var i = 0; i < n; i++) {
        final v = num.tryParse(vals[i].toString());
        if (v != null) pts.add(_XY(i.toString(), v.toDouble()));
      }
      if (pts.isEmpty) return const [];
      return [
        LineSeries<_XY, String>(
          dataSource: pts,
          xValueMapper: (d, _) => d.x,
          yValueMapper: (d, _) => d.y,
          color: AppColors.chartLine,
          width: 2,
        ),
      ];
    }

    // Price / generic series
    if (seriesRaw.isEmpty) return const [];

    return seriesRaw.asMap().entries.map((entry) {
      final i = entry.key;
      final s = entry.value;
      final color = colors[i % colors.length];
      final name = (s['symbol'] ?? s['name'] ?? 'Series $i').toString();

      // Points-based (price blocks)
      final pointsRaw = s['points'] as List?;
      if (pointsRaw != null) {
        final pts = <_XY>[];
        for (final p in pointsRaw.whereType<Map>()) {
          final v = num.tryParse(p['value'].toString());
          if (v != null) pts.add(_XY(pts.length.toString(), v.toDouble()));
        }
        return LineSeries<_XY, String>(
          name: name,
          dataSource: pts,
          xValueMapper: (d, _) => d.x,
          yValueMapper: (d, _) => d.y,
          color: color,
          width: 2,
        );
      }

      // Generic data array  [ [x, y], ... ]  or  [y, ...]
      final dataRaw = s['data'] as List? ?? const [];
      final pts = <_XY>[];
      for (final item in dataRaw) {
        if (item is List && item.length >= 2) {
          final y = num.tryParse(item[1].toString());
          if (y != null) pts.add(_XY(item[0].toString(), y.toDouble()));
        } else {
          final y = num.tryParse(item.toString());
          if (y != null) pts.add(_XY(pts.length.toString(), y.toDouble()));
        }
      }

      if (chartType == 'bar') {
        return ColumnSeries<_XY, String>(
          name: name,
          dataSource: pts,
          xValueMapper: (d, _) => d.x,
          yValueMapper: (d, _) => d.y,
          color: color,
        );
      }
      return LineSeries<_XY, String>(
        name: name,
        dataSource: pts,
        xValueMapper: (d, _) => d.x,
        yValueMapper: (d, _) => d.y,
        color: color,
        width: 2,
      );
    }).toList();
  }
}

class _XY {
  _XY(this.x, this.y);
  final String x;
  final double y;
}

// ── Stat ──────────────────────────────────────────────────────────────────────

class _StatBlock extends StatelessWidget {
  const _StatBlock({required this.block});
  final Map<String, dynamic> block;

  @override
  Widget build(BuildContext context) {
    final label = (block['label'] ?? '').toString();
    final value = (block['value'] ?? '').toString();
    final detail = block['detail']?.toString();
    final trend = block['trend']?.toString();

    IconData? trendIcon;
    Color? trendColor;
    if (trend == 'up') {
      trendIcon = Icons.trending_up;
      trendColor = AppColors.success;
    } else if (trend == 'down') {
      trendIcon = Icons.trending_down;
      trendColor = AppColors.danger;
    }

    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 12),
      decoration: BoxDecoration(
        color: AppColors.surface.withValues(alpha: 0.5),
        borderRadius: BorderRadius.circular(12),
        border: Border.all(color: AppColors.border),
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Text(
            label.toUpperCase(),
            style: AppTextStyles.eyebrow.copyWith(color: AppColors.textDim),
          ),
          const SizedBox(height: 4),
          Row(
            crossAxisAlignment: CrossAxisAlignment.center,
            children: [
              Text(
                value,
                style: AppTextStyles.valueLg.copyWith(color: AppColors.textHi),
              ),
              if (trendIcon != null) ...[
                const SizedBox(width: 6),
                Icon(trendIcon, color: trendColor, size: 18),
              ],
            ],
          ),
          if (detail != null) ...[
            const SizedBox(height: 4),
            Text(
              detail,
              style: AppTextStyles.micro.copyWith(color: AppColors.textDim),
            ),
          ],
        ],
      ),
    );
  }
}

// ── Navigate ──────────────────────────────────────────────────────────────────

class _NavigateBlock extends StatelessWidget {
  const _NavigateBlock({required this.block});
  final Map<String, dynamic> block;

  @override
  Widget build(BuildContext context) {
    final route = (block['route'] ?? '').toString();
    return Row(
      mainAxisSize: MainAxisSize.min,
      children: [
        Icon(Icons.north_east, color: AppColors.primary, size: 14),
        const SizedBox(width: 4),
        Text(
          'Navigated to ',
          style: AppTextStyles.micro.copyWith(color: AppColors.primary),
        ),
        Text(
          route,
          style: AppTextStyles.mono(11, color: AppColors.primary),
        ),
      ],
    );
  }
}
