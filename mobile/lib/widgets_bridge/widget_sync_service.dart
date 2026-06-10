import 'dart:convert';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:home_widget/home_widget.dart';
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
}

final widgetSyncServiceProvider = Provider<WidgetSyncService>(
  (_) => const WidgetSyncService(),
);
