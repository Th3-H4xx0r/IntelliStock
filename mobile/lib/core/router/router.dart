import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';
import '../network/api_base_url.dart';
import '../network/session.dart';
import 'app_shell.dart';

import '../../features/connect/presentation/connect_screen.dart';
import '../../features/auth/presentation/login_screen.dart';
import '../../features/onboarding/presentation/onboarding_screen.dart';
import '../../features/dashboard/presentation/dashboard_screen.dart';
import '../../features/instances/presentation/instances_screen.dart';
import '../../features/instances/presentation/instance_detail_screen.dart';
import '../../features/live_trading/presentation/live_trading_screen.dart';
import '../../features/backtests/presentation/backtests_screen.dart';
import '../../features/backtests/presentation/backtest_detail_screen.dart';
import '../../features/backtests/presentation/backtest_playback_screen.dart';
import '../../features/strategies/presentation/strategies_screen.dart';
import '../../features/strategies/presentation/strategy_detail_screen.dart';
import '../../features/brokerages/presentation/brokerages_screen.dart';
import '../../features/agent_runs/presentation/agent_runs_screen.dart';
import '../../features/nexus/presentation/nexus_screen.dart';
import '../../features/models/presentation/models_screen.dart';
import '../../features/token_usage/presentation/token_usage_screen.dart';
import '../../features/settings/presentation/settings_screen.dart';
import '../../features/settings/presentation/notification_settings_screen.dart';
import '../../features/stock/presentation/stock_screen.dart';

final _rootKey = GlobalKey<NavigatorState>();

/// Central app router. Branches drive the bottom-nav shell; detail and "More"
/// destinations push over the shell. The redirect handles auth + onboarding
/// gates. The biometric lock gate is layered on in `app.dart` via the
/// `MaterialApp.router` builder.
final goRouterProvider = Provider<GoRouter>((ref) {
  // Use ref.read (not ref.watch) so the GoRouter object is created once and
  // NOT recreated (losing the back stack) on every session change.
  // refreshListenable: session handles redirect re-evaluation on session changes.
  final session = ref.read(sessionProvider);
  final urlStore = ref.read(apiBaseUrlProvider);

  return GoRouter(
    navigatorKey: _rootKey,
    initialLocation: '/dashboard',
    refreshListenable: Listenable.merge([session, urlStore]),
    redirect: (context, state) {
      final loc = state.matchedLocation;
      final onConnect = loc == '/connect';
      // Gate everything behind a configured backend URL (before the auth gate).
      if (!urlStore.isConfigured) {
        return onConnect ? null : '/connect';
      }
      final loggedIn = session.isAuthenticated;
      final onLogin = loc == '/login';
      final onOnboarding = loc == '/onboarding';

      if (!loggedIn) {
        return onLogin ? null : '/login?redirect=${Uri.encodeComponent(loc)}';
      }
      if (onLogin) {
        if (!session.hasCompletedOnboarding) return '/onboarding';
        // Honor ?redirect= only if it's a safe relative in-app path.
        final redirectParam = state.uri.queryParameters['redirect'];
        if (redirectParam != null &&
            redirectParam.startsWith('/') &&
            !redirectParam.startsWith('//') &&
            !redirectParam.contains(':') &&
            !redirectParam.contains('@')) {
          return redirectParam;
        }
        return '/dashboard';
      }
      if (!session.hasCompletedOnboarding && !onOnboarding) {
        return '/onboarding';
      }
      return null;
    },
    routes: [
      GoRoute(
        path: '/connect',
        builder: (_, _) => const ConnectScreen(),
      ),
      GoRoute(
        path: '/login',
        builder: (_, state) =>
            LoginScreen(redirectPath: state.uri.queryParameters['redirect']),
      ),
      GoRoute(
        path: '/onboarding',
        builder: (_, _) => const OnboardingScreen(),
      ),
      // Bottom-nav shell.
      StatefulShellRoute.indexedStack(
        builder: (_, _, shell) => AppShell(navigationShell: shell),
        branches: [
          StatefulShellBranch(routes: [
            GoRoute(path: '/dashboard', builder: (_, _) => const DashboardScreen()),
          ]),
          StatefulShellBranch(routes: [
            GoRoute(path: '/instances', builder: (_, _) => const InstancesScreen()),
          ]),
          StatefulShellBranch(routes: [
            GoRoute(path: '/backtests', builder: (_, _) => const BacktestsScreen()),
          ]),
          StatefulShellBranch(routes: [
            GoRoute(path: '/strategies', builder: (_, _) => const StrategiesScreen()),
          ]),
        ],
      ),
      // Detail / fullscreen routes pushed over the shell.
      GoRoute(
        path: '/instances/:id',
        parentNavigatorKey: _rootKey,
        builder: (_, s) =>
            InstanceDetailScreen(instanceId: s.pathParameters['id']!),
      ),
      GoRoute(
        path: '/instances/:id/live',
        parentNavigatorKey: _rootKey,
        builder: (_, s) =>
            LiveTradingScreen(instanceId: s.pathParameters['id']!),
      ),
      GoRoute(
        path: '/backtests/:id',
        parentNavigatorKey: _rootKey,
        builder: (_, s) => BacktestDetailScreen(id: s.pathParameters['id']!),
      ),
      GoRoute(
        path: '/backtests/:id/playback',
        parentNavigatorKey: _rootKey,
        builder: (_, s) => BacktestPlaybackScreen(id: s.pathParameters['id']!),
      ),
      GoRoute(
        path: '/strategies/:id',
        parentNavigatorKey: _rootKey,
        builder: (_, s) =>
            StrategyDetailScreen(strategyId: s.pathParameters['id']!),
      ),
      GoRoute(
        path: '/brokerages',
        parentNavigatorKey: _rootKey,
        builder: (_, _) => const BrokeragesScreen(),
      ),
      GoRoute(
        path: '/stock/:symbol',
        parentNavigatorKey: _rootKey,
        builder: (_, s) {
          final args = s.extra is StockScreenArgs
              ? s.extra as StockScreenArgs
              : null;
          return StockScreen(
            symbol: s.pathParameters['symbol']!,
            position: args?.position,
            brokerageId: args?.brokerageId,
            portfolioTotal: args?.portfolioTotal,
          );
        },
      ),
      GoRoute(
        path: '/agent-runs',
        parentNavigatorKey: _rootKey,
        builder: (_, _) => const AgentRunsScreen(),
      ),
      GoRoute(
        path: '/nexus',
        parentNavigatorKey: _rootKey,
        builder: (_, _) => const NexusScreen(),
      ),
      GoRoute(
        path: '/models',
        parentNavigatorKey: _rootKey,
        builder: (_, _) => const ModelsScreen(),
      ),
      GoRoute(
        path: '/token-usage',
        parentNavigatorKey: _rootKey,
        builder: (_, _) => const TokenUsageScreen(),
      ),
      GoRoute(
        path: '/settings',
        parentNavigatorKey: _rootKey,
        builder: (_, _) => const SettingsScreen(),
      ),
      GoRoute(
        path: '/settings/notifications',
        parentNavigatorKey: _rootKey,
        builder: (_, _) => const NotificationSettingsScreen(),
      ),
    ],
  );
});
