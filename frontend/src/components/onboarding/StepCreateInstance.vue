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
      <p class="text-sm text-slate-500">Step 3 · Create your first instance</p>
      <h2 class="mt-2 text-xl sm:text-2xl font-medium leading-tight text-slate-100">
        Spin up an autonomous worker
      </h2>
      <p class="mt-3 text-sm leading-6 text-slate-400">
        An instance is a long-running execution context: it picks tickers, runs your strategy on a cadence,
        and writes orders through the linked brokerage. Every instance gets its own container and logs.
      </p>
    </div>

    <!-- Existing instances -->
    <div v-if="instances.length">
      <p class="ui-label mb-2">Already running</p>
      <div class="rounded-xl border border-white/[0.07] ui-rows">
        <div v-for="inst in instances" :key="inst.id" class="min-h-14 px-4 py-2 flex items-center gap-3">
          <div class="min-w-0 flex-1">
            <p class="text-sm text-slate-100 truncate">{{ inst.name || inst.id }}</p>
            <p class="mt-0.5 text-xs text-slate-500 truncate font-mono">{{ inst.id }}</p>
          </div>
          <span
            class="inline-flex items-center gap-2 text-xs capitalize shrink-0"
            :class="inst.runCommand || inst.running ? 'text-slate-200' : 'text-slate-500'"
          >
            <span class="ui-dot" :class="inst.runCommand || inst.running ? 'ui-dot-live' : 'ui-dot-off'"></span>{{ inst.runCommand || inst.running ? 'running' : 'idle' }}
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
        Create another instance
      </button>
    </div>

    <!-- Add form -->
    <div v-if="showForm" class="rounded-xl border border-white/[0.07] p-4 sm:p-5 space-y-5">
      <div>
        <label class="ui-field-label">Instance ID <span class="text-slate-500">*</span></label>
        <input
          v-model="form.id"
          type="text"
          placeholder="e.g. my-live-instance"
          class="ui-input font-mono"
        />
        <p class="ui-help">Letters, digits, dashes, underscores. Becomes the container name.</p>
      </div>
      <div>
        <label class="ui-field-label">Display name <span class="text-slate-500">(optional)</span></label>
        <input
          v-model="form.name"
          type="text"
          placeholder="e.g. Momentum live"
          class="ui-input"
        />
      </div>
      <div>
        <label class="ui-field-label">Cadence</label>
        <div class="ui-seg flex-wrap">
          <button
            v-for="g in GRANULARITIES"
            :key="g.value"
            type="button"
            @click="form.granularity = g.value"
            class="ui-seg-btn tabular-nums"
            :class="form.granularity === g.value ? 'ui-seg-btn-active' : ''"
          >{{ g.label }}</button>
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
        <button v-if="instances.length" type="button" @click="showForm = false" :disabled="submitting"
          class="ui-btn-ghost h-9">Cancel</button>
        <button type="button" @click="createInstance" :disabled="submitting || !canSubmit"
          class="ui-btn-primary">
          <span v-if="submitting" class="material-symbols-outlined text-[16px] animate-spin">progress_activity</span>
          {{ submitting ? 'Working…' : 'Create instance' }}
        </button>
      </div>
    </div>
  </div>
</template>
