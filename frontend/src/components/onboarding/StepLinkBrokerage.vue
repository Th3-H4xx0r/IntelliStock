<script setup>
import { ref, computed } from 'vue'
import { authHeaders, ONBOARDING_API_BASE as API_BASE } from '../../utils/auth.js'

const props = defineProps({
  brokerages: { type: Array, default: () => [] },
})
const emit = defineEmits(['added'])

const showForm = ref(props.brokerages.length === 0)
const tab = ref('alpaca') // 'alpaca' | 'robinhood'
const submitting = ref(false)
const submitMsg = ref('')
const submitOk = ref(false)

const alpaca = ref({ account_name: '', key: '', secret: '', paper: true, data_feed: 'iex' })
const rh = ref({ account_name: '', access_token: '', refresh_token: '', device_token: '' })
const rhStep = ref(1)
const rhAccounts = ref([])
const rhSelected = ref('')
const rhAcknowledged = ref(false)

function _normalizeError(data, status) {
  const d = data?.detail
  if (Array.isArray(d)) return d.map(x => (x && (x.msg || x.message)) || JSON.stringify(x)).join('; ')
  if (typeof d === 'string') return d
  if (d && typeof d === 'object') return d.msg || d.message || JSON.stringify(d)
  return `HTTP ${status}`
}

function setTab(t) {
  tab.value = t
  submitMsg.value = ''
  submitOk.value = false
  rhStep.value = 1
  rhAccounts.value = []
  rhSelected.value = ''
}

const alpacaValid = computed(() =>
  alpaca.value.account_name.trim() && alpaca.value.key.trim() && alpaca.value.secret.trim()
)

async function saveAlpaca() {
  if (!alpacaValid.value) {
    submitOk.value = false
    submitMsg.value = 'Account name, API key, and secret are all required.'
    return
  }
  submitting.value = true
  submitOk.value = false
  submitMsg.value = 'Validating credentials…'
  try {
    const f = alpaca.value
    const tres = await fetch(`${API_BASE}/brokerages/test-alpaca`, {
      method: 'POST',
      headers: { ...authHeaders(), 'Content-Type': 'application/json' },
      body: JSON.stringify({
        key: f.key.trim(),
        secret: f.secret.trim(),
        paper: f.paper,
        alpaca_data_feed: f.data_feed || 'iex',
      }),
    })
    const td = await tres.json().catch(() => ({}))
    if (!tres.ok || !td?.ok) {
      const hint = (td?.hints && td.hints[0]) || _normalizeError(td, tres.status)
      throw new Error(`Test failed — ${hint}`)
    }

    submitMsg.value = 'Linking account…'
    const res = await fetch(`${API_BASE}/brokerages`, {
      method: 'POST',
      headers: { ...authHeaders(), 'Content-Type': 'application/json' },
      body: JSON.stringify({
        brokerage_type: 'alpaca',
        account_name: f.account_name.trim(),
        key: f.key.trim(),
        secret: f.secret.trim(),
        paper: f.paper,
        alpaca_data_feed: f.data_feed || 'iex',
      }),
    })
    const data = await res.json().catch(() => ({}))
    if (!res.ok) throw new Error(_normalizeError(data, res.status))
    submitOk.value = true
    submitMsg.value = `Linked "${data.account?.account_name || f.account_name}".`
    showForm.value = false
    alpaca.value = { account_name: '', key: '', secret: '', paper: true, data_feed: 'iex' }
    emit('added', data.account)
  } catch (e) {
    submitOk.value = false
    submitMsg.value = e.message || 'Something went wrong'
  } finally {
    submitting.value = false
  }
}

async function rhFetchAccounts() {
  const f = rh.value
  if (!rhAcknowledged.value) {
    submitOk.value = false
    submitMsg.value = 'Acknowledge the Robinhood warning to continue.'
    return
  }
  if (!f.account_name.trim() || !f.access_token.trim() || !f.refresh_token.trim()) {
    submitOk.value = false
    submitMsg.value = 'Account name, access token, and refresh token are all required.'
    return
  }
  submitting.value = true
  submitOk.value = false
  submitMsg.value = 'Fetching Robinhood accounts…'
  try {
    const res = await fetch(`${API_BASE}/brokerages/robinhood/accounts`, {
      method: 'POST',
      headers: { ...authHeaders(), 'Content-Type': 'application/json' },
      body: JSON.stringify({
        access_token: f.access_token.trim(),
        refresh_token: f.refresh_token.trim(),
        device_token: f.device_token.trim() || undefined,
      }),
    })
    const data = await res.json().catch(() => ({}))
    if (!res.ok) throw new Error(_normalizeError(data, res.status))
    rhAccounts.value = data.accounts || []
    const selectable = rhAccounts.value.filter(a => a.management_type !== 'managed')
    rhSelected.value = selectable.length === 1 ? selectable[0].account_number : ''
    rhStep.value = 2
    submitMsg.value = ''
  } catch (e) {
    submitOk.value = false
    submitMsg.value = e.message || 'Failed to fetch accounts'
  } finally {
    submitting.value = false
  }
}

async function saveRobinhood() {
  if (!rhSelected.value) {
    submitOk.value = false
    submitMsg.value = 'Select an account first.'
    return
  }
  submitting.value = true
  submitOk.value = false
  submitMsg.value = 'Linking account…'
  try {
    const f = rh.value
    const res = await fetch(`${API_BASE}/brokerages`, {
      method: 'POST',
      headers: { ...authHeaders(), 'Content-Type': 'application/json' },
      body: JSON.stringify({
        brokerage_type: 'robinhood',
        account_name: f.account_name.trim(),
        access_token: f.access_token.trim(),
        refresh_token: f.refresh_token.trim(),
        device_token: f.device_token.trim() || undefined,
        account_number: rhSelected.value,
      }),
    })
    const data = await res.json().catch(() => ({}))
    if (!res.ok) throw new Error(_normalizeError(data, res.status))
    submitOk.value = true
    submitMsg.value = `Linked Robinhood account "${data.account?.account_name || f.account_name}".`
    showForm.value = false
    rh.value = { account_name: '', access_token: '', refresh_token: '', device_token: '' }
    rhStep.value = 1
    rhAccounts.value = []
    rhSelected.value = ''
    rhAcknowledged.value = false
    emit('added', data.account)
  } catch (e) {
    submitOk.value = false
    submitMsg.value = e.message || 'Failed to link account'
  } finally {
    submitting.value = false
  }
}

function fmtMoney(v) {
  return v != null ? new Intl.NumberFormat('en-US', { style: 'currency', currency: 'USD' }).format(v) : '—'
}
</script>

<template>
  <div class="space-y-6">
    <div>
      <p class="text-primary text-xs font-bold uppercase tracking-widest mb-2 onboarding-letter" style="animation-delay: 0.05s">
        Step 2 · Link a brokerage
      </p>
      <h2 class="onboarding-title text-2xl sm:text-3xl font-bold leading-tight onboarding-letter" style="animation-delay: 0.15s">
        Plug in where the orders go.
      </h2>
      <p class="mt-3 text-slate-400 text-sm onboarding-letter" style="animation-delay: 0.25s">
        Alpaca is the production-grade option (paper or live, by API key). Robinhood works through an unofficial
        reverse-engineered API — fine for personal use, but with real ToS / account-ban risk.
      </p>
    </div>

    <!-- Existing brokerages -->
    <div v-if="brokerages.length" class="space-y-2 onboarding-letter" style="animation-delay: 0.3s">
      <p class="text-[11px] font-semibold uppercase tracking-widest text-slate-500">Already linked</p>
      <div class="grid grid-cols-1 sm:grid-cols-2 gap-2">
        <div
          v-for="b in brokerages"
          :key="b.id"
          class="rounded-lg border border-border-subtle bg-surface/40 px-3 py-2.5 flex items-center gap-3"
        >
          <span class="material-symbols-outlined text-primary text-[20px]">
            {{ b.brokerage_type === 'alpaca' ? 'show_chart' : 'savings' }}
          </span>
          <div class="min-w-0 flex-1">
            <p class="text-sm font-medium text-slate-100 truncate">{{ b.account_name }}</p>
            <p class="text-[11px] text-slate-500 truncate uppercase tracking-widest">
              {{ b.brokerage_type }}<span v-if="b.brokerage_type === 'alpaca'"> · {{ b.alpaca_paper ? 'Paper' : 'Live' }}</span>
            </p>
          </div>
          <span class="text-[10px]" :class="b.status === 'active' ? 'text-emerald-400' : 'text-yellow-400'">{{ b.status }}</span>
        </div>
      </div>
      <button
        v-if="!showForm"
        @click="showForm = true"
        class="mt-1 inline-flex items-center gap-1.5 text-xs text-primary hover:brightness-125 transition-all"
      >
        <span class="material-symbols-outlined text-[14px]">add</span>
        Link another brokerage
      </button>
    </div>

    <!-- Add form -->
    <div v-if="showForm" class="rounded-xl border border-border-subtle bg-surface/40 p-4 sm:p-5 space-y-4 onboarding-letter" style="animation-delay: 0.35s">
      <div class="flex gap-1.5 p-1 rounded-lg bg-surface/60 border border-border-subtle w-fit">
        <button
          @click="setTab('alpaca')"
          :class="tab === 'alpaca' ? 'bg-primary/15 text-primary' : 'text-slate-400 hover:text-slate-100'"
          class="px-3 py-1.5 rounded-md text-xs font-semibold transition-colors"
        >Alpaca</button>
        <button
          @click="setTab('robinhood')"
          :class="tab === 'robinhood' ? 'bg-primary/15 text-primary' : 'text-slate-400 hover:text-slate-100'"
          class="px-3 py-1.5 rounded-md text-xs font-semibold transition-colors"
        >Robinhood</button>
      </div>

      <!-- Alpaca form -->
      <div v-if="tab === 'alpaca'" class="space-y-4">
        <div>
          <label class="block text-xs font-medium text-slate-400 mb-1.5">Account Name <span class="text-red-400">*</span></label>
          <input v-model="alpaca.account_name" type="text" placeholder="e.g. Alpaca paper main"
            class="w-full bg-surface border border-border-subtle rounded-lg px-3 py-2.5 text-sm text-slate-100 placeholder-slate-600 focus:outline-none focus:border-primary transition-colors" />
        </div>
        <div class="grid grid-cols-1 sm:grid-cols-2 gap-3">
          <div>
            <label class="block text-xs font-medium text-slate-400 mb-1.5">API Key ID <span class="text-red-400">*</span></label>
            <input v-model="alpaca.key" type="text" placeholder="PKxxxxxxxxxx"
              class="w-full bg-surface border border-border-subtle rounded-lg px-3 py-2.5 text-sm text-slate-100 placeholder-slate-600 focus:outline-none focus:border-primary transition-colors font-mono" />
          </div>
          <div>
            <label class="block text-xs font-medium text-slate-400 mb-1.5">Secret Key <span class="text-red-400">*</span></label>
            <input v-model="alpaca.secret" type="password" placeholder="••••••••••"
              class="w-full bg-surface border border-border-subtle rounded-lg px-3 py-2.5 text-sm text-slate-100 placeholder-slate-600 focus:outline-none focus:border-primary transition-colors font-mono" />
          </div>
        </div>
        <div class="flex flex-wrap items-center gap-4">
          <label class="flex items-center gap-2 cursor-pointer">
            <button type="button" @click="alpaca.paper = !alpaca.paper"
              :class="alpaca.paper ? 'bg-primary border-primary' : 'bg-surface border-border-subtle'"
              class="relative inline-flex h-5 w-9 shrink-0 rounded-full border-2 transition-colors">
              <span :class="alpaca.paper ? 'translate-x-4' : 'translate-x-0.5'"
                class="inline-block size-3.5 rounded-full bg-white shadow transition-transform"></span>
            </button>
            <span class="text-sm text-slate-300">Paper trading</span>
          </label>
          <div class="flex items-center gap-2">
            <span class="text-xs text-slate-400">Data feed:</span>
            <button @click="alpaca.data_feed = 'iex'"
              :class="alpaca.data_feed === 'iex' ? 'bg-primary/15 text-primary border-primary/40' : 'border-border-subtle text-slate-400'"
              class="px-2.5 py-1 rounded-md text-xs font-semibold border transition-all">IEX</button>
            <button @click="alpaca.data_feed = 'sip'"
              :class="alpaca.data_feed === 'sip' ? 'bg-primary/15 text-primary border-primary/40' : 'border-border-subtle text-slate-400'"
              class="px-2.5 py-1 rounded-md text-xs font-semibold border transition-all">SIP</button>
          </div>
        </div>

        <Transition name="step">
          <div v-if="submitMsg" class="rounded-lg px-3 py-2.5 text-sm font-medium"
            :class="submitOk
              ? 'bg-emerald-500/10 border border-emerald-500/20 text-emerald-400'
              : 'bg-red-500/10 border border-red-500/20 text-red-400'">
            <span v-if="submitting" class="inline-flex items-center gap-2">
              <span class="material-symbols-outlined text-base animate-spin">progress_activity</span>
              {{ submitMsg }}
            </span>
            <span v-else>{{ submitMsg }}</span>
          </div>
        </Transition>

        <div class="flex flex-col-reverse sm:flex-row items-stretch sm:items-center gap-2 sm:justify-end">
          <button v-if="brokerages.length" @click="showForm = false" :disabled="submitting"
            class="px-3 py-2 text-sm text-slate-400 hover:text-slate-100 transition-colors disabled:opacity-40">Cancel</button>
          <button @click="saveAlpaca" :disabled="submitting || !alpacaValid"
            class="inline-flex items-center justify-center gap-2 px-4 py-2 rounded-lg bg-primary/90 text-background-dark text-sm font-bold hover:brightness-110 transition-all disabled:opacity-40">
            <span v-if="submitting" class="material-symbols-outlined text-base animate-spin">progress_activity</span>
            <span v-else class="material-symbols-outlined text-[16px]">link</span>
            {{ submitting ? 'Working…' : 'Test &amp; link Alpaca' }}
          </button>
        </div>
      </div>

      <!-- Robinhood form -->
      <div v-else class="space-y-4">
        <!-- Mandatory warning panel -->
        <div class="rounded-xl border border-amber-500/40 bg-amber-500/10 px-4 py-3.5 text-amber-200 text-sm leading-relaxed">
          <div class="flex items-start gap-2.5">
            <span class="material-symbols-outlined text-amber-300 text-[20px] mt-0.5">warning</span>
            <div class="space-y-2 min-w-0">
              <p class="font-semibold">Robinhood does not provide an official API.</p>
              <p class="text-amber-100/90 text-[13px]">
                Linking your Robinhood account uses a reverse-engineered private API. Automated trading
                violates Robinhood's terms of service — your account could be flagged, restricted, or
                <span class="font-semibold">permanently banned</span>. We recommend Alpaca for production
                use. The adapter ships with <code class="font-mono px-1 rounded bg-black/30">RH_DRY_RUN=true</code>
                so the first runs simulate orders only.
              </p>
              <label class="flex items-start gap-2 cursor-pointer mt-1">
                <input v-model="rhAcknowledged" type="checkbox"
                  class="mt-0.5 size-4 rounded border-amber-400/50 bg-transparent text-amber-400 focus:ring-amber-400" />
                <span class="text-[12px] text-amber-100">
                  I understand the risk and accept that linking Robinhood may result in account suspension or banning.
                </span>
              </label>
            </div>
          </div>
        </div>

        <template v-if="rhStep === 1">
          <div>
            <label class="block text-xs font-medium text-slate-400 mb-1.5">Account Name <span class="text-red-400">*</span></label>
            <input v-model="rh.account_name" type="text" placeholder="e.g. My Robinhood Account"
              class="w-full bg-surface border border-border-subtle rounded-lg px-3 py-2.5 text-sm text-slate-100 placeholder-slate-600 focus:outline-none focus:border-primary transition-colors" />
          </div>
          <div>
            <label class="block text-xs font-medium text-slate-400 mb-1.5">Access Token <span class="text-red-400">*</span></label>
            <textarea v-model="rh.access_token" rows="2" placeholder="Paste access token"
              class="w-full bg-surface border border-border-subtle rounded-lg px-3 py-2.5 text-xs text-slate-100 placeholder-slate-600 focus:outline-none focus:border-primary transition-colors font-mono"></textarea>
          </div>
          <div>
            <label class="block text-xs font-medium text-slate-400 mb-1.5">Refresh Token <span class="text-red-400">*</span></label>
            <textarea v-model="rh.refresh_token" rows="2" placeholder="Paste refresh token"
              class="w-full bg-surface border border-border-subtle rounded-lg px-3 py-2.5 text-xs text-slate-100 placeholder-slate-600 focus:outline-none focus:border-primary transition-colors font-mono"></textarea>
          </div>
          <div>
            <label class="block text-xs font-medium text-slate-400 mb-1.5">Device Token <span class="text-slate-600">(optional)</span></label>
            <input v-model="rh.device_token" type="text" placeholder="Auto-generated if blank"
              class="w-full bg-surface border border-border-subtle rounded-lg px-3 py-2.5 text-sm text-slate-100 placeholder-slate-600 focus:outline-none focus:border-primary transition-colors font-mono" />
          </div>
          <p class="text-[11px] text-slate-500">
            Obtain tokens by running <code class="font-mono bg-black/30 px-1 rounded">python robinhood_cli.py --paste-tokens</code>.
          </p>
        </template>

        <template v-else>
          <p class="text-xs text-slate-400">Select the Robinhood account to link as <span class="text-primary font-semibold">{{ rh.account_name }}</span>:</p>
          <div class="space-y-2 max-h-72 overflow-y-auto">
            <label
              v-for="acct in rhAccounts"
              :key="acct.account_number"
              class="flex items-center gap-3 rounded-lg border px-3 py-2.5 cursor-pointer transition-all"
              :class="[
                acct.management_type === 'managed' ? 'opacity-50 cursor-not-allowed' : 'hover:border-primary/40',
                rhSelected === acct.account_number ? 'border-primary/60 bg-primary/5' : 'border-border-subtle bg-surface/40',
              ]"
            >
              <input
                type="radio"
                :value="acct.account_number"
                v-model="rhSelected"
                :disabled="acct.management_type === 'managed'"
                class="size-4 text-primary"
              />
              <div class="flex-1 min-w-0">
                <p class="text-sm font-medium text-slate-100 truncate">
                  {{ acct.account_number }} <span class="text-slate-500 text-xs">· {{ acct.account_type || 'cash' }}</span>
                  <span v-if="acct.management_type === 'managed'" class="text-amber-400 text-[10px] ml-1">(managed)</span>
                </p>
                <p class="text-[11px] text-slate-500">
                  Equity {{ fmtMoney(acct.equity) }} · Buying power {{ fmtMoney(acct.buying_power) }}
                </p>
              </div>
            </label>
          </div>
        </template>

        <Transition name="step">
          <div v-if="submitMsg" class="rounded-lg px-3 py-2.5 text-sm font-medium"
            :class="submitOk
              ? 'bg-emerald-500/10 border border-emerald-500/20 text-emerald-400'
              : 'bg-red-500/10 border border-red-500/20 text-red-400'">
            <span v-if="submitting" class="inline-flex items-center gap-2">
              <span class="material-symbols-outlined text-base animate-spin">progress_activity</span>
              {{ submitMsg }}
            </span>
            <span v-else>{{ submitMsg }}</span>
          </div>
        </Transition>

        <div class="flex flex-col-reverse sm:flex-row items-stretch sm:items-center gap-2 sm:justify-end">
          <button v-if="brokerages.length && rhStep === 1" @click="showForm = false" :disabled="submitting"
            class="px-3 py-2 text-sm text-slate-400 hover:text-slate-100 transition-colors disabled:opacity-40">Cancel</button>
          <button v-if="rhStep === 2" @click="rhStep = 1" :disabled="submitting"
            class="px-3 py-2 text-sm text-slate-400 hover:text-slate-100 transition-colors disabled:opacity-40">Back</button>
          <button v-if="rhStep === 1" @click="rhFetchAccounts"
            :disabled="submitting || !rhAcknowledged"
            class="inline-flex items-center justify-center gap-2 px-4 py-2 rounded-lg bg-primary/90 text-background-dark text-sm font-bold hover:brightness-110 transition-all disabled:opacity-40">
            <span v-if="submitting" class="material-symbols-outlined text-base animate-spin">progress_activity</span>
            <span v-else class="material-symbols-outlined text-[16px]">arrow_forward</span>
            {{ submitting ? 'Working…' : 'Fetch accounts' }}
          </button>
          <button v-else @click="saveRobinhood"
            :disabled="submitting || !rhSelected"
            class="inline-flex items-center justify-center gap-2 px-4 py-2 rounded-lg bg-primary/90 text-background-dark text-sm font-bold hover:brightness-110 transition-all disabled:opacity-40">
            <span v-if="submitting" class="material-symbols-outlined text-base animate-spin">progress_activity</span>
            <span v-else class="material-symbols-outlined text-[16px]">link</span>
            {{ submitting ? 'Working…' : 'Link Robinhood' }}
          </button>
        </div>
      </div>
    </div>
  </div>
</template>
