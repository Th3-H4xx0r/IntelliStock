import 'dart:convert';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:home_widget/home_widget.dart';
import '../core/models/portfolio_history.dart';
import '../core/network/api_client.dart';
import 'widget_payload.dart';

/// App Group identifier shared between the Flutter app and the WidgetKit
/// extension (PortfolioWidget).
const _kAppGroup = 'group.dev.pkrishna.intellistock';

// ── Key constants (must match SwiftUI side) ───────────────────────────────────

/// JSON-encoded [WidgetPortfolio].
const kWidgetKeyPortfolio = 'portfolio_data';

/// JSON-encoded list of [WidgetPosition].
const kWidgetKeyPositions = 'positions_data';

/// JSON-encoded list of [WidgetInstance].
const kWidgetKeyInstances = 'instances_data';

/// JSON-encoded list of [WidgetAccount] — the selectable portfolios for the
/// configurable widget.
const kWidgetKeyAccounts = 'accounts_data';

// ── Service ───────────────────────────────────────────────────────────────────

/// Writes a [WidgetPayload] to the App Group shared container and triggers a
/// WidgetKit timeline reload for both the Portfolio and Instance widgets.
///
/// Called by the dashboard / live-data feature after every data refresh.
/// The sync is fire-and-forget; errors are silently swallowed so a widget
/// failure never crashes the main app.
class WidgetSyncService {
  const WidgetSyncService();

  /// Persists all sections of [payload] and reloads both widget kinds.
  Future<void> sync(WidgetPayload payload) async {
    try {
      await HomeWidget.setAppGroupId(_kAppGroup);

      await Future.wait([
        HomeWidget.saveWidgetData<String>(
          kWidgetKeyPortfolio,
          jsonEncode(payload.portfolio.toJson()),
        ),
        HomeWidget.saveWidgetData<String>(
          kWidgetKeyPositions,
          jsonEncode(payload.positions.map((p) => p.toJson()).toList()),
        ),
        HomeWidget.saveWidgetData<String>(
          kWidgetKeyInstances,
          jsonEncode(payload.instances.map((i) => i.toJson()).toList()),
        ),
      ]);

      // Trigger timeline reload for portfolio overview widget.
      await HomeWidget.updateWidget(
        iOSName: 'PortfolioWidget',
        androidName: 'PortfolioWidgetProvider',
      );

      // Trigger timeline reload for instance status widget.
      await HomeWidget.updateWidget(
        iOSName: 'InstanceWidget',
        androidName: 'InstanceWidgetProvider',
      );
    } catch (_) {
      // Widget sync must never surface errors to the user.
    }
  }

  /// Writes the full list of selectable portfolios (for the configurable
  /// widget) plus the first as the primary fallback, then reloads the widget.
  Future<void> syncAccounts(List<WidgetAccount> accounts) async {
    try {
      await HomeWidget.setAppGroupId(_kAppGroup);
      await HomeWidget.saveWidgetData<String>(
        kWidgetKeyAccounts,
        jsonEncode(accounts.map((a) => a.toJson()).toList()),
      );
      if (accounts.isNotEmpty) {
        await HomeWidget.saveWidgetData<String>(
          kWidgetKeyPortfolio,
          jsonEncode(accounts.first.toPortfolioJson()),
        );
      }
      await HomeWidget.updateWidget(
        iOSName: 'PortfolioWidget',
        androidName: 'PortfolioWidgetProvider',
      );
    } catch (_) {
      // Widget sync must never surface errors to the user.
    }
  }
}

final widgetSyncServiceProvider = Provider<WidgetSyncService>(
  (_) => const WidgetSyncService(),
);

/// Builds the home-screen widget's portfolio list from the user's INSTANCES —
/// the same live equity the Live Trading terminal shows (NOT the coarser
/// brokerage balance). Each instance with intraday history becomes one
/// selectable portfolio in the configurable widget.
class WidgetDataSyncer {
  WidgetDataSyncer(this._ref);

  final Ref _ref;

  Future<void> run() async {
    try {
      final client = _ref.read(apiClientProvider);
      final data = await client.get<Map<String, dynamic>>('/instances');
      final instances = (data['instances'] as List? ?? const [])
          .whereType<Map<String, dynamic>>()
          .toList();

      final accounts = <WidgetAccount>[];
      for (final inst in instances) {
        final id = (inst['id'] ?? '').toString();
        if (id.isEmpty) continue;
        final name = (inst['name'] ?? '').toString();
        try {
          final raw = await client.get<Map<String, dynamic>>(
            '/instances/$id/portfolio-history',
            query: {'range': '1D'},
          );
          final h = PortfolioHistory.fromJson(raw);
          if (h.values.isEmpty && h.currentValue == null) continue;
          final n = h.timestamps.length < h.values.length
              ? h.timestamps.length
              : h.values.length;
          accounts.add(WidgetAccount(
            id: id,
            label: name.isNotEmpty ? name : id,
            accountValue:
                h.currentValue ?? (h.values.isNotEmpty ? h.values.last : 0),
            dayPnlAbs: h.changeAbs ?? 0,
            dayPnlPct: h.changePct ?? 0,
            intradayPoints: [
              for (var i = 0; i < n; i++)
                IntradayPoint(
                  t: h.timestamps[i].millisecondsSinceEpoch ~/ 1000,
                  v: h.values[i],
                ),
            ],
          ));
        } catch (_) {
          // Instance not running / no history → skip it.
        }
      }

      if (accounts.isNotEmpty) {
        await _ref.read(widgetSyncServiceProvider).syncAccounts(accounts);
      }
    } catch (_) {
      // Never surface widget-sync errors to the user.
    }
  }
}

final widgetDataSyncerProvider = Provider<WidgetDataSyncer>(
  (ref) => WidgetDataSyncer(ref),
);

/// Watch this from the dashboard to refresh the home-screen widget on entry.
final widgetSyncProvider = FutureProvider.autoDispose<void>(
  (ref) => ref.read(widgetDataSyncerProvider).run(),
);
