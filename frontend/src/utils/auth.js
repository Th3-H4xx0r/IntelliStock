const TOKEN_KEY = 'intellistock_token'
const USER_KEY  = 'intellistock_user'

// ── Storage ──────────────────────────────────────────────────────────────────

export function getToken() {
  return localStorage.getItem(TOKEN_KEY)
}

export function getUser() {
  try { return JSON.parse(localStorage.getItem(USER_KEY)) } catch { return null }
}

export function isAuthenticated() {
  return !!getToken()
}

export function setUser(user) {
  if (user) localStorage.setItem(USER_KEY, JSON.stringify(user))
}

function saveSession(token, user) {
  localStorage.setItem(TOKEN_KEY, token)
  localStorage.setItem(USER_KEY, JSON.stringify(user))
}

export function clearSession() {
  localStorage.removeItem(TOKEN_KEY)
  localStorage.removeItem(USER_KEY)
  // Forget per-user chatbot state so a follow-on login on the same tab
  // doesn't see the previous user's conversation list / model id.
  // Lazy import to avoid a load-order cycle (utils/chatbot.js imports auth.js).
  import('../composables/useChatbot.js').then(m => m.resetChatbot()).catch(() => { /* ignore */ })
}

/**
 * Install a one-time global fetch wrapper that adopts a slid token from the
 * backend's `X-Refreshed-Token` response header. The app has no central HTTP
 * client (each view calls fetch directly), so this is the single chokepoint
 * that keeps the web session sliding for ~30 days of activity. Idempotent.
 */
export function installRefreshedTokenCapture() {
  if (typeof window === 'undefined' || window.__refreshTokenCaptureInstalled) return
  window.__refreshTokenCaptureInstalled = true
  const orig = window.fetch.bind(window)
  window.fetch = async (...args) => {
    const res = await orig(...args)
    try {
      const t = res && res.headers && res.headers.get && res.headers.get('X-Refreshed-Token')
      // Only slide an existing session — never resurrect a token into a tab
      // that has logged out (cross-tab race with an in-flight request).
      if (t && localStorage.getItem(TOKEN_KEY)) localStorage.setItem(TOKEN_KEY, t)
    } catch { /* never let token capture break a request */ }
    return res
  }
}

// ── API ───────────────────────────────────────────────────────────────────────

// Dev:  /api → Vite proxy → VITE_API_URL (server-side, no CORS ever)
// Prod: VITE_API_URL baked into bundle at build time (direct call, needs backend CORS)
//       Falls back to /api if VITE_API_URL not set (nginx proxy handles it)
const API_BASE = import.meta.env.DEV
  ? '/api'
  : (import.meta.env.VITE_API_URL || '/api')

/**
 * POST /auth/login
 * @param {string} username
 * @param {string} password
 * @returns {{ token: string, user: object }}
 * @throws {Error} with a user-facing message
 */
export async function login(username, password) {
  const res = await fetch(`${API_BASE}/auth/login`, {
    method:  'POST',
    headers: { 'Content-Type': 'application/json' },
    body:    JSON.stringify({ username, password }),
  })

  if (!res.ok) {
    if (res.status === 401) throw new Error('Invalid username or password.')
    let msg = `Server error (${res.status})`
    try { msg = (await res.json()).detail ?? msg } catch { /* ignore */ }
    throw new Error(msg)
  }

  const data = await res.json()
  saveSession(data.access_token, data.user)
  return { token: data.access_token, user: data.user }
}

/**
 * Returns an Authorization header object for authenticated requests.
 */
export function authHeaders() {
  const token = getToken()
  return token ? { Authorization: `Bearer ${token}` } : {}
}

/**
 * GET /auth/me — fetches the freshest user document from the backend
 * and updates the cached copy. Used by the router guard to verify the
 * onboarding flag on first authenticated navigation.
 *
 * Returns:
 *   { ok: true,  user }                  — refreshed
 *   { ok: false, status: 401 | null }    — token rejected (clears session)
 *                                          or network error (caller decides)
 *
 * On a 401 the local session is cleared so the guard can route the user
 * to /login instead of trapping them in /onboarding with a dead token.
 */
export async function fetchCurrentUser() {
  if (!getToken()) return { ok: false, status: null }
  try {
    const res = await fetch(`${API_BASE}/auth/me`, { headers: authHeaders() })
    if (!res.ok) {
      if (res.status === 401) clearSession()
      return { ok: false, status: res.status }
    }
    const user = await res.json()
    setUser(user)
    return { ok: true, user }
  } catch {
    return { ok: false, status: null }
  }
}

/**
 * Convenience for the router: returns the cached value when present,
 * defaults to false when absent so a missing flag triggers onboarding
 * instead of letting users skip it.
 */
export function hasCompletedOnboarding() {
  const u = getUser()
  return !!(u && u.has_completed_onboarding)
}

export const ONBOARDING_API_BASE = API_BASE
