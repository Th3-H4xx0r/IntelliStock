<script setup>
import { ref, computed, watch } from 'vue'
import { authHeaders, ONBOARDING_API_BASE as API_BASE } from '../../utils/auth.js'

const props = defineProps({
  brokerages: { type: Array, default: () => [] },
  instances:  { type: Array, default: () => [] },
})
const emit = defineEmits(['linked'])

const selectedInstance = ref('')
const selectedBrokerage = ref('')
const submitting = ref(false)
const submitMsg = ref('')
const submitOk = ref(false)

watch(() => props.instances, (next) => {
  if (next.length === 1 && !selectedInstance.value) selectedInstance.value = next[0].id
}, { immediate: true })
watch(() => props.brokerages, (next) => {
  if (next.length === 1 && !selectedBrokerage.value) selectedBrokerage.value = next[0].id
}, { immediate: true })

function _normalizeError(data, status) {
  const d = data?.detail
  if (Array.isArray(d)) return d.map(x => (x && (x.msg || x.message)) || JSON.stringify(x)).join('; ')
  if (typeof d === 'string') return d
  if (d && typeof d === 'object') return d.msg || d.message || JSON.stringify(d)
  return `HTTP ${status}`
}

async function linkBrokerage() {
  if (!selectedInstance.value || !selectedBrokerage.value) {
    submitOk.value = false
    submitMsg.value = 'Pick both an instance and a brokerage to link.'
    return
  }
  submitting.value = true
  submitOk.value = false
  submitMsg.value = 'Linking brokerage…'
  try {
    const res = await fetch(`${API_BASE}/instances/${encodeURIComponent(selectedInstance.value)}/link-brokerage`, {
      method: 'POST',
      headers: { ...authHeaders(), 'Content-Type': 'application/json' },
      body: JSON.stringify({ brokerage_id: selectedBrokerage.value }),
    })
    const data = await res.json().catch(() => ({}))
    if (!res.ok) throw new Error(_normalizeError(data, res.status))
    submitOk.value = true
    submitMsg.value = 'Linked! Your instance can now place orders through this brokerage.'
    emit('linked', { instance_id: selectedInstance.value, brokerage_id: selectedBrokerage.value })
  } catch (e) {
    submitOk.value = false
    submitMsg.value = e.message || 'Failed to link'
  } finally {
    submitting.value = false
  }
}

const canLink = computed(() => props.instances.length > 0 && props.brokerages.length > 0)
</script>

<template>
  <div class="space-y-7">
    <div>
      <p class="text-primary text-xs font-bold uppercase tracking-widest mb-2 onboarding-letter" style="animation-delay: 0.05s">
        Step 4 · Connect the pieces
      </p>
      <h2 class="onboarding-title text-2xl sm:text-3xl font-bold leading-tight onboarding-letter" style="animation-delay: 0.15s">
        How a trade actually flows.
      </h2>
      <p class="mt-3 text-slate-400 text-sm onboarding-letter" style="animation-delay: 0.25s">
        Your instance runs a strategy that asks a model. The model returns a buy/sell call. The instance
        sends that order through the linked brokerage. Each link is independent — swap any piece without
        rebuilding the rest.
      </p>
    </div>

    <!-- Visual flow -->
    <div class="rounded-xl border border-border-subtle bg-surface/40 p-4 sm:p-5 onboarding-letter" style="animation-delay: 0.3s">
      <div class="flex items-stretch justify-between gap-2 sm:gap-3 overflow-x-auto">
        <div
          v-for="(node, idx) in [
            { icon: 'memory',          label: 'Model',     desc: 'Reasons over signals' },
            { icon: 'tune',            label: 'Strategy',  desc: 'Picks tickers + capital' },
            { icon: 'rocket_launch',   label: 'Instance',  desc: 'Runs on a cadence' },
            { icon: 'account_balance', label: 'Brokerage', desc: 'Executes orders' },
          ]"
          :key="node.label"
          class="flex items-center gap-1 sm:gap-2 min-w-0"
        >
          <div class="flex flex-col items-center text-center min-w-[88px]">
            <div class="size-11 rounded-xl bg-primary/10 border border-primary/30 flex items-center justify-center mb-1.5">
              <span class="material-symbols-outlined text-primary text-[22px]">{{ node.icon }}</span>
            </div>
            <p class="text-[11px] font-semibold text-slate-200">{{ node.label }}</p>
            <p class="text-[10px] text-slate-500 leading-tight">{{ node.desc }}</p>
          </div>
          <span v-if="idx < 3" class="material-symbols-outlined text-primary/60 text-[18px] mt-3">arrow_forward</span>
        </div>
      </div>
    </div>

    <!-- Live linker -->
    <div class="rounded-xl border border-border-subtle bg-surface/40 p-4 sm:p-5 space-y-4 onboarding-letter" style="animation-delay: 0.35s">
      <p class="text-[11px] font-semibold uppercase tracking-widest text-slate-500">Link an instance to a brokerage now</p>

      <div v-if="!canLink" class="text-sm text-slate-400 leading-relaxed">
        You'll need at least one instance and one brokerage to link them here. You can always do this
        later from the
        <span class="text-primary font-semibold">Instances</span> page — pick a row, choose a brokerage,
        set a max-usage cap, and you're live.
      </div>

      <div v-else class="grid grid-cols-1 sm:grid-cols-2 gap-3">
        <div>
          <label class="block text-xs font-medium text-slate-400 mb-1.5">Instance</label>
          <select v-model="selectedInstance"
            class="w-full bg-surface border border-border-subtle rounded-lg px-3 py-2.5 text-sm text-slate-100 focus:outline-none focus:border-primary transition-colors">
            <option value="">Pick one…</option>
            <option v-for="i in instances" :key="i.id" :value="i.id">{{ i.name || i.id }}</option>
          </select>
        </div>
        <div>
          <label class="block text-xs font-medium text-slate-400 mb-1.5">Brokerage</label>
          <select v-model="selectedBrokerage"
            class="w-full bg-surface border border-border-subtle rounded-lg px-3 py-2.5 text-sm text-slate-100 focus:outline-none focus:border-primary transition-colors">
            <option value="">Pick one…</option>
            <option v-for="b in brokerages" :key="b.id" :value="b.id">
              {{ b.account_name }} ({{ b.brokerage_type }})
            </option>
          </select>
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

      <div v-if="canLink" class="flex justify-end">
        <button @click="linkBrokerage" :disabled="submitting || !selectedInstance || !selectedBrokerage"
          class="inline-flex items-center justify-center gap-2 px-4 py-2 rounded-lg bg-primary/90 text-background-dark text-sm font-bold hover:brightness-110 transition-all disabled:opacity-40">
          <span v-if="submitting" class="material-symbols-outlined text-base animate-spin">progress_activity</span>
          <span v-else class="material-symbols-outlined text-[16px]">link</span>
          {{ submitting ? 'Linking…' : 'Link brokerage to instance' }}
        </button>
      </div>
    </div>

    <p class="text-[11px] text-slate-500 leading-relaxed onboarding-letter" style="animation-delay: 0.45s">
      Tip: instances can also be linked to a strategy and a separate data-only Alpaca brokerage from the Instances page.
      Strategies are the "brain" — pick from the catalog or build your own.
    </p>
  </div>
</template>
