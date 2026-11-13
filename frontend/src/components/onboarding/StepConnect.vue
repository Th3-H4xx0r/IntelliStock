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
    submitMsg.value = 'Linked. Your instance can now place orders through this brokerage.'
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
      <p class="text-sm text-slate-500">Step 4 · Connect the pieces</p>
      <h2 class="mt-2 text-xl sm:text-2xl font-medium leading-tight text-slate-100">
        How a trade actually flows
      </h2>
      <p class="mt-3 text-sm leading-6 text-slate-400">
        Your instance runs a strategy that asks a model. The model returns a buy/sell call. The instance
        sends that order through the linked brokerage. Each link is independent, so you can swap any piece
        without rebuilding the rest.
      </p>
    </div>

    <!-- Visual flow -->
    <div class="rounded-xl border border-white/[0.07] px-4 py-4 sm:px-5 overflow-x-auto">
      <div class="flex items-start justify-between gap-2 sm:gap-3 min-w-[480px]">
        <template
          v-for="(node, idx) in [
            { icon: 'memory',          label: 'Model',     desc: 'Reasons over signals' },
            { icon: 'tune',            label: 'Strategy',  desc: 'Picks tickers + capital' },
            { icon: 'rocket_launch',   label: 'Instance',  desc: 'Runs on a cadence' },
            { icon: 'account_balance', label: 'Brokerage', desc: 'Executes orders' },
          ]"
          :key="node.label"
        >
          <div class="flex-1 min-w-0">
            <span class="material-symbols-outlined text-[18px] text-slate-500">{{ node.icon }}</span>
            <p class="mt-2 text-sm font-medium text-slate-100">{{ node.label }}</p>
            <p class="mt-0.5 text-xs text-slate-500">{{ node.desc }}</p>
          </div>
          <span v-if="idx < 3" class="material-symbols-outlined text-[16px] text-slate-600 mt-0.5 shrink-0">arrow_forward</span>
        </template>
      </div>
    </div>

    <!-- Live linker -->
    <div class="rounded-xl border border-white/[0.07] p-4 sm:p-5 space-y-4">
      <p class="text-sm font-medium text-slate-200">Link an instance to a brokerage now</p>

      <div v-if="!canLink" class="text-sm leading-6 text-slate-400">
        You'll need at least one instance and one brokerage to link them here. You can always do this
        later from the
        <span class="text-slate-200">Instances</span> page: pick a row, choose a brokerage,
        set a max-usage cap, and you're live.
      </div>

      <div v-else class="grid grid-cols-1 sm:grid-cols-2 gap-4">
        <div>
          <label class="ui-field-label">Instance</label>
          <select v-model="selectedInstance" class="ui-input">
            <option value="">Pick one…</option>
            <option v-for="i in instances" :key="i.id" :value="i.id">{{ i.name || i.id }}</option>
          </select>
        </div>
        <div>
          <label class="ui-field-label">Brokerage</label>
          <select v-model="selectedBrokerage" class="ui-input">
            <option value="">Pick one…</option>
            <option v-for="b in brokerages" :key="b.id" :value="b.id">
              {{ b.account_name }} ({{ b.brokerage_type }})
            </option>
          </select>
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

      <div v-if="canLink" class="flex justify-end">
        <button type="button" @click="linkBrokerage" :disabled="submitting || !selectedInstance || !selectedBrokerage"
          class="ui-btn-primary">
          <span v-if="submitting" class="material-symbols-outlined text-[16px] animate-spin">progress_activity</span>
          {{ submitting ? 'Linking…' : 'Link brokerage to instance' }}
        </button>
      </div>
    </div>

    <p class="text-xs leading-5 text-slate-500">
      Tip: instances can also be linked to a strategy and a separate data-only Alpaca brokerage from the Instances page.
      Strategies are the "brain": pick from the catalog or build your own.
    </p>
  </div>
</template>
