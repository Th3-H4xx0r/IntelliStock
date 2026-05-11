/**
 * Onboarding state + resource helpers.
 *
 * The backend stores has_completed_onboarding on the user doc in RethinkDB.
 * On a fresh install, /onboarding/state reports the flag plus counts of
 * existing models / brokerages / instances so the wizard can show the
 * already-configured items and offer "add another" rather than starting
 * from zero every time.
 */

import { authHeaders, setUser, ONBOARDING_API_BASE as API_BASE } from './auth.js'

async function _json(res) {
  if (!res.ok) {
    let detail = `HTTP ${res.status}`
    try {
      const data = await res.json()
      detail = data?.detail || detail
    } catch { /* ignore */ }
    throw new Error(detail)
  }
  return res.json()
}

/** GET /onboarding/state — returns { has_completed_onboarding, counts, user }. */
export async function fetchOnboardingState() {
  const res = await fetch(`${API_BASE}/onboarding/state`, { headers: authHeaders() })
  return _json(res)
}

/** POST /onboarding/complete — flips the flag to true and updates the cached user. */
export async function markOnboardingComplete() {
  const res = await fetch(`${API_BASE}/onboarding/complete`, {
    method: 'POST',
    headers: authHeaders(),
  })
  const data = await _json(res)
  if (data?.user) setUser(data.user)
  return data
}

/** POST /onboarding/reset — flips the flag back to false (re-onboard). */
export async function resetOnboarding() {
  const res = await fetch(`${API_BASE}/onboarding/reset`, {
    method: 'POST',
    headers: authHeaders(),
  })
  const data = await _json(res)
  if (data?.user) setUser(data.user)
  return data
}

/** GET /models — list all configured LLM models. */
export async function listModels() {
  const res = await fetch(`${API_BASE}/models`, { headers: authHeaders() })
  const data = await _json(res)
  return data.models || data || []
}

/** GET /brokerages — list all linked brokerage accounts. */
export async function listBrokerages() {
  const res = await fetch(`${API_BASE}/brokerages`, { headers: authHeaders() })
  const data = await _json(res)
  return data.accounts || []
}

/** GET /instances — list all created instances. */
export async function listInstances() {
  const res = await fetch(`${API_BASE}/instances`, { headers: authHeaders() })
  const data = await _json(res)
  return data.instances || data || []
}
