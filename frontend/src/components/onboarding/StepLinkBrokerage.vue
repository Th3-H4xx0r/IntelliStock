<script setup>
import { ref, computed } from 'vue'
import { authHeaders, ONBOARDING_API_BASE as API_BASE } from '../../utils/auth.js'

const props = defineProps({
  brokerages: { type: Array, default: () => [] },
})
const emit = defineEmits(['added'])

const showForm = ref(props.brokerages.length === 0)
const submitting = ref(false)
const submitMsg = ref('')
const submitOk = ref(false)

const alpaca = ref({ account_name: '', key: '', secret: '', paper: true, data_feed: 'iex' })

function _normalizeError(data, status) {
  const d = data?.detail
  if (Array.isArray(d)) return d.map(x => (x && (x.msg || x.message)) || JSON.stringify(x)).join('; ')
  if (typeof d === 'string') return d
  if (d && typeof d === 'object') return d.msg || d.message || JSON.stringify(d)
  return `HTTP ${status}`
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


</script>

<template>
  <div class="space-y-6">
    <div>
      <p class="text-sm text-slate-500">Step 2 · Link a brokerage</p>
      <h2 class="mt-2 text-xl sm:text-2xl font-medium leading-tight text-slate-100">
        Connect the account your orders go to
      </h2>
      <p class="mt-3 text-sm leading-6 text-slate-400">
        Alpaca is the supported stock brokerage for paper and live trading.
      </p>
    </div>

    <!-- Existing brokerages -->
    <div v-if="brokerages.length">
      <p class="ui-label mb-2">Already linked</p>
      <div class="rounded-xl border border-white/[0.07] ui-rows">
        <div v-for="b in brokerages" :key="b.id" class="min-h-14 px-4 py-2 flex items-center gap-3">
          <div class="min-w-0 flex-1">
            <p class="text-sm text-slate-100 truncate">{{ b.account_name }}</p>
            <p class="mt-0.5 text-xs text-slate-500 truncate">
              <span class="capitalize">{{ b.brokerage_type }}</span><span v-if="b.brokerage_type === 'alpaca'"> · {{ b.alpaca_paper ? 'Paper' : 'Live' }}</span>
            </p>
          </div>
          <span class="inline-flex items-center gap-2 text-xs shrink-0" :class="b.status === 'active' ? 'text-slate-500' : 'text-slate-300'">
            <span v-if="b.status !== 'active'" class="ui-dot ui-dot-warn"></span>{{ b.status }}
          </span>
        </div>
      </div>
      <button
        v-if="!showForm"
        type="button"
        @click="showForm = true"
        class="ui-btn-ghost mt-2 -ml-3"
      >
        <span class="material-symbols-outlined text-[16px]">add</span>
        Link another brokerage
      </button>
    </div>

    <!-- Add form -->
    <div v-if="showForm" class="rounded-xl border border-white/[0.07] p-4 sm:p-5 space-y-5">
      <!-- Alpaca form -->
      <div class="space-y-5">
        <div>
          <label class="ui-field-label">Account name <span class="text-slate-500">*</span></label>
          <input v-model="alpaca.account_name" type="text" placeholder="e.g. Alpaca paper main"
            class="ui-input" />
        </div>
        <div class="grid grid-cols-1 sm:grid-cols-2 gap-4">
          <div>
            <label class="ui-field-label">API key ID <span class="text-slate-500">*</span></label>
            <input v-model="alpaca.key" type="text" placeholder="PKxxxxxxxxxx"
              class="ui-input font-mono" />
          </div>
          <div>
            <label class="ui-field-label">Secret key <span class="text-slate-500">*</span></label>
            <input v-model="alpaca.secret" type="password" placeholder="••••••••••"
              class="ui-input font-mono" />
          </div>
        </div>
        <div class="flex flex-wrap items-center gap-x-6 gap-y-3">
          <label class="flex items-center gap-2.5 cursor-pointer">
            <button type="button" @click="alpaca.paper = !alpaca.paper"
              :class="alpaca.paper ? 'bg-slate-200' : 'bg-raised'"
              class="relative inline-flex h-5 w-9 shrink-0 items-center rounded-full border border-white/[0.08] transition-colors duration-150 ease-out"
              role="switch" :aria-checked="alpaca.paper">
              <span :class="alpaca.paper ? 'translate-x-[18px] bg-background-dark' : 'translate-x-0.5 bg-slate-500'"
                class="inline-block size-3.5 rounded-full transition-transform duration-150 ease-out"></span>
            </button>
            <span class="text-sm text-slate-300">Paper trading</span>
          </label>
          <div class="flex items-center gap-2">
            <span class="text-sm text-slate-400">Data feed</span>
            <div class="ui-seg">
              <button type="button" @click="alpaca.data_feed = 'iex'"
                :class="alpaca.data_feed === 'iex' ? 'ui-seg-btn-active' : ''"
                class="ui-seg-btn">IEX</button>
              <button type="button" @click="alpaca.data_feed = 'sip'"
                :class="alpaca.data_feed === 'sip' ? 'ui-seg-btn-active' : ''"
                class="ui-seg-btn">SIP</button>
            </div>
          </div>
        </div>

        <Transition name="step">
          <div v-if="submitMsg" class="rounded-xl border px-4 py-3 text-sm"
            :class="submitOk ? 'border-gain/20 bg-gain/5 text-gain' : 'border-loss/20 bg-loss/5 text-loss'"
            role="status">
            <span v-if="submitting" class="inline-flex items-center gap-2 text-slate-300">
              <span class="material-symbols-outlined text-[16px] animate-spin">progress_activity</span>
              {{ submitMsg }}
            </span>
            <span v-else>{{ submitMsg }}</span>
          </div>
        </Transition>

        <div class="flex flex-col-reverse sm:flex-row items-stretch sm:items-center gap-2 sm:justify-end">
          <button v-if="brokerages.length" type="button" @click="showForm = false" :disabled="submitting"
            class="ui-btn-ghost h-9">Cancel</button>
          <button type="button" @click="saveAlpaca" :disabled="submitting || !alpacaValid"
            class="ui-btn-primary">
            <span v-if="submitting" class="material-symbols-outlined text-[16px] animate-spin">progress_activity</span>
            {{ submitting ? 'Working…' : 'Test &amp; link Alpaca' }}
          </button>
        </div>
      </div>


    </div>
  </div>
</template>
