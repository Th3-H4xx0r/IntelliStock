import { createRouter, createWebHistory } from 'vue-router'
import { isAuthenticated, getUser, fetchCurrentUser, hasCompletedOnboarding } from '../utils/auth.js'
import { isPreviewMode } from '../utils/preview.js'
import LandingView from '../views/LandingView.vue'

const routes = [
  {
    path: '/',
    name: 'landing',
    component: LandingView,
  },
  {
    path: '/login',
    name: 'login',
    component: () => import('../views/LoginView.vue'),
    meta: { guestOnly: true },
  },
  {
    path: '/dashboard',
    name: 'dashboard',
    component: () => import('../views/DashboardView.vue'),
    meta: { requiresAuth: true },
  },
  {
    path: '/brokerages',
    name: 'brokerages',
    component: () => import('../views/BrokeragesView.vue'),
    meta: { requiresAuth: true },
  },
  {
    path: '/instances',
    name: 'instances',
    component: () => import('../views/InstancesView.vue'),
    meta: { requiresAuth: true },
  },
  {
    path: '/instances/:id',
    name: 'instance-detail',
    component: () => import('../views/InstanceDetailView.vue'),
    meta: { requiresAuth: true },
  },
  {
    path: '/instances/:id/live',
    name: 'live-trading',
    component: () => import('../views/LiveTradingView.vue'),
    meta: { requiresAuth: true },
  },
  {
    path: '/backtests',
    name: 'backtests',
    component: () => import('../views/BacktestsView.vue'),
    meta: { requiresAuth: true },
  },
  {
    path: '/backtests/:id',
    name: 'backtest-detail',
    component: () => import('../views/BacktestDetailView.vue'),
    meta: { requiresAuth: true },
  },
  {
    path: '/backtests/:id/playback',
    name: 'backtest-playback',
    component: () => import('../views/BacktestPlaybackView.vue'),
    meta: { requiresAuth: true },
  },
  {
    path: '/agent-runs',
    name: 'agent-runs',
    component: () => import('../views/AgentRunsView.vue'),
    meta: { requiresAuth: true },
  },
  {
    path: '/nexus',
    name: 'nexus',
    component: () => import('../views/NexusView.vue'),
    meta: { requiresAuth: true },
  },
  {
    path: '/learning',
    name: 'learning',
    component: () => import('../views/LearningView.vue'),
    meta: { requiresAuth: true },
  },
  {
    path: '/kalshi',
    name: 'kalshi',
    component: () => import('../views/KalshiView.vue'),
    meta: { requiresAuth: true },
  },
  {
    path: '/crypto',
    name: 'crypto',
    component: () => import('../views/CryptoView.vue'),
    meta: { requiresAuth: true },
  },
  {
    path: '/crypto/instances/:id',
    name: 'crypto-instance-detail',
    component: () => import('../views/CryptoInstanceDetailView.vue'),
    meta: { requiresAuth: true },
  },
  {
    path: '/kalshi/instances/:id',
    name: 'kalshi-instance-detail',
    component: () => import('../views/KalshiInstanceDetailView.vue'),
    meta: { requiresAuth: true },
  },
  {
    path: '/kalshi/instances/:id/backtest',
    name: 'kalshi-backtest',
    component: () => import('../views/KalshiBacktestView.vue'),
    meta: { requiresAuth: true },
  },
  {
    path: '/kalshi/backtests/:id',
    name: 'kalshi-backtest-result',
    component: () => import('../views/KalshiBacktestResultView.vue'),
    meta: { requiresAuth: true },
  },
  {
    path: '/strategies',
    name: 'strategies',
    component: () => import('../views/StrategiesView.vue'),
    meta: { requiresAuth: true },
  },
  {
    path: '/strategies/:id',
    name: 'strategy-detail',
    component: () => import('../views/StrategyDetailView.vue'),
    meta: { requiresAuth: true },
  },
  {
    path: '/models',
    name: 'models',
    component: () => import('../views/ModelsView.vue'),
    meta: { requiresAuth: true },
  },
  {
    path: '/token-usage',
    name: 'token-usage',
    component: () => import('../views/TokenUsageView.vue'),
    meta: { requiresAuth: true },
  },
  {
    path: '/notification-settings',
    name: 'notification-settings',
    component: () => import('../views/NotificationSettingsView.vue'),
    meta: { requiresAuth: true },
  },
  {
    path: '/animation',
    name: 'animation',
    component: () => import('../views/AnimationView.vue'),
  },
  {
    path: '/onboarding',
    name: 'onboarding',
    component: () => import('../views/OnboardingView.vue'),
    meta: { requiresAuth: true, skipOnboardingGuard: true },
  },
]

const router = createRouter({
  history: createWebHistory(import.meta.env.BASE_URL),
  routes,
})

router.beforeEach(async (to) => {
  // Preview mode: only the landing page is reachable. Any direct nav
  // to /login, /dashboard, /onboarding, etc. lands back on /. We don't
  // strip the routes themselves at module load because doing so would
  // make code-split chunks unreachable but still in the bundle —
  // a runtime guard is simpler and just as effective for a SPA.
  if (isPreviewMode() && to.name !== 'landing') {
    return { name: 'landing' }
  }

  const authed = isAuthenticated()

  // Redirect unauthenticated users away from protected pages first.
  if (to.meta.requiresAuth && !authed) {
    return { name: 'login', query: { redirect: to.fullPath } }
  }

  // Redirect authenticated users away from guest-only pages.
  if (to.meta.guestOnly && authed) {
    return hasCompletedOnboarding() ? { name: 'dashboard' } : { name: 'onboarding' }
  }

  // Onboarding gate: when authenticated user hits any protected route that
  // is not /onboarding itself, send them to /onboarding until the flag flips.
  // The cached user may pre-date the field — refresh from /auth/me once and
  // reuse the cached value on subsequent navigations.
  if (authed && to.meta.requiresAuth && !to.meta.skipOnboardingGuard) {
    const cached = getUser()
    let completed
    if (cached && Object.prototype.hasOwnProperty.call(cached, 'has_completed_onboarding')) {
      completed = !!cached.has_completed_onboarding
    } else {
      const result = await fetchCurrentUser()
      if (result?.ok) {
        completed = !!(result.user && result.user.has_completed_onboarding)
      } else if (result?.status === 401) {
        // Token rejected — fetchCurrentUser already cleared the session.
        return { name: 'login', query: { redirect: to.fullPath } }
      } else {
        // Network error: fail OPEN so a flaky connection doesn't trap users
        // in /onboarding. The OnboardingView will re-check on its own when
        // it loads, and a healthy /auth/login will repopulate the field.
        return true
      }
    }
    if (!completed) {
      return { name: 'onboarding', query: { redirect: to.fullPath } }
    }
  }

  return true
})

export default router
