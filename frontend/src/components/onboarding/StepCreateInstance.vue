<script setup>
import { ref, computed } from 'vue'
import { authHeaders, ONBOARDING_API_BASE as API_BASE } from '../../utils/auth.js'

const props = defineProps({
  instances: { type: Array, default: () => [] },
})
const emit = defineEmits(['added'])

const showForm = ref(props.instances.length === 0)
const submitting = ref(false)
const submitMsg = ref('')
const submitOk = ref(false)

const form = ref({
  id: '',
  name: '',
  granularity: '60',
  run_command: false,
})

const GRANULARITIES = [
  { value: '60',   label: '1 min' },
  { value: '300',  label: '5 min' },
  { value: '900',  label: '15 min' },
  { value: '3600', label: '1 hr' },
]

const canSubmit = computed(() => /^[a-z0-9][a-z0-9_-]*$/i.test(form.value.id.trim()))

function _normalizeError(data, status) {
  const d = data?.detail
  if (Array.isArray(d)) return d.map(x => (x && (x.msg || x.message)) || JSON.stringify(x)).join('; ')
  if (typeof d === 'string') return d
  if (d && typeof d === 'object') return d.msg || d.message || JSON.stringify(d)
  return `HTTP ${status}`
}

async function createInstance() {
  if (!canSubmit.value) {
    submitOk.value = false
    submitMsg.value = 'Instance ID must contain only letters, digits, dashes, or underscores.'
    return
  }
  submitting.value = true
  submitOk.value = false
  submitMsg.value = 'Creating instance…'
  try {
    const f = form.value
    const res = await fetch(`${API_BASE}/instances`, {
      method: 'POST',
      headers: { ...authHeaders(), 'Content-Type': 'application/json' },
      body: JSON.stringify({
        id: f.id.trim(),
        name: f.name.trim() || undefined,
        granularity: f.granularity || '60',
        run_command: f.run_command,
      }),
    })
    const data = await res.json().catch(() => ({}))
    if (!res.ok) throw new Error(_normalizeError(data, res.status))
    submitOk.value = true
    submitMsg.value = `Instance "${data.id || f.id}" created.`
    showForm.value = false
    form.value = { id: '', name: '', granularity: '60', run_command: false }
    emit('added', data)
  } catch (e) {
    submitOk.value = false
    submitMsg.value = e.message || 'Failed to create instance'
  } finally {
    submitting.value = false
  }
}
</script>

<template>
  <div class="space-y-6">
    <div>
      <p class="text-primary text-xs font-bold uppercase tracking-widest mb-2 onboarding-letter" style="animation-delay: 0.05s">
        Step 3 · Create your first instance
      </p>
      <h2 class="onboarding-title text-2xl sm:text-3xl font-bold leading-tight onboarding-letter" style="animation-delay: 0.15s">
        Spin up an autonomous worker.
      </h2>
      <p class="mt-3 text-slate-400 text-sm onboarding-letter" style="animation-delay: 0.25s">
        An instance is a long-running execution context: it picks tickers, runs your strategy on a cadence,
        and writes orders through the linked brokerage. Every instance gets its own container and logs.
      </p>
    </div>

    <!-- Existing instances -->
    <div v-if="instances.length" class="space-y-2 onboarding-letter" style="animation-delay: 0.3s">
      <p class="text-[11px] font-semibold uppercase tracking-widest text-slate-500">Already running</p>
      <div class="grid grid-cols-1 sm:grid-cols-2 gap-2">
        <div
          v-for="inst in instances"
          :key="inst.id"
          class="rounded-lg border border-border-subtle bg-surface/40 px-3 py-2.5 flex items-center gap-3"
        >
          <span class="material-symbols-outlined text-primary text-[20px]">memory</span>
          <div class="min-w-0 flex-1">
            <p class="text-sm font-medium text-slate-100 truncate">{{ inst.name || inst.id }}</p>
            <p class="text-[11px] text-slate-500 truncate font-mono">{{ inst.id }}</p>
          </div>
          <span
            class="text-[10px]"
            :class="inst.runCommand || inst.running ? 'text-emerald-400' : 'text-slate-500'"
          >{{ inst.runCommand || inst.running ? 'running' : 'idle' }}</span>
        </div>
      </div>
      <button
        v-if="!showForm"
        @click="showForm = true"
        class="mt-1 inline-flex items-center gap-1.5 text-xs text-primary hover:brightness-125 transition-all"
      >
        <span class="material-symbols-outlined text-[14px]">add</span>
        Create another instance
      </button>
    </div>

    <!-- Add form -->
    <div v-if="showForm" class="rounded-xl border border-border-subtle bg-surface/40 p-4 sm:p-5 space-y-4 onboarding-letter" style="animation-delay: 0.35s">
      <div>
        <label class="block text-xs font-medium text-slate-400 mb-1.5">Instance ID <span class="text-red-400">*</span></label>
        <input
          v-model="form.id"
          type="text"
          placeholder="e.g. my-live-instance"
          class="w-full bg-surface border border-border-subtle rounded-lg px-3 py-2.5 text-sm text-slate-100 placeholder-slate-600 focus:outline-none focus:border-primary transition-colors font-mono"
        />
        <p class="text-[11px] text-slate-500 mt-1">Letters, digits, dashes, underscores. Becomes the container name.</p>
      </div>
      <div>
        <label class="block text-xs font-medium text-slate-400 mb-1.5">Display Name <span class="text-slate-600">(optional)</span></label>
        <input
          v-model="form.name"
          type="text"
          placeholder="e.g. Momentum live"
          class="w-full bg-surface border border-border-subtle rounded-lg px-3 py-2.5 text-sm text-slate-100 placeholder-slate-600 focus:outline-none focus:border-primary transition-colors"
        />
      </div>
      <div>
        <label class="block text-xs font-medium text-slate-400 mb-1.5">Cadence</label>
        <div class="flex gap-1.5 flex-wrap">
          <button
            v-for="g in GRANULARITIES"
            :key="g.value"
            @click="form.granularity = g.value"
            class="px-3 py-1.5 rounded-lg text-xs font-semibold border transition-all"
            :class="form.granularity === g.value
              ? 'bg-primary/15 text-primary border-primary/40'
              : 'border-border-subtle text-slate-400 hover:text-slate-200'"
          >{{ g.label }}</button>
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
        <button v-if="instances.length" @click="showForm = false" :disabled="submitting"
          class="px-3 py-2 text-sm text-slate-400 hover:text-slate-100 transition-colors disabled:opacity-40">Cancel</button>
        <button @click="createInstance" :disabled="submitting || !canSubmit"
          class="inline-flex items-center justify-center gap-2 px-4 py-2 rounded-lg bg-primary/90 text-background-dark text-sm font-bold hover:brightness-110 transition-all disabled:opacity-40">
          <span v-if="submitting" class="material-symbols-outlined text-base animate-spin">progress_activity</span>
          <span v-else class="material-symbols-outlined text-[16px]">rocket_launch</span>
          {{ submitting ? 'Working…' : 'Create instance' }}
        </button>
      </div>
    </div>
  </div>
</template>
