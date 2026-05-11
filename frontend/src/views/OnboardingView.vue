<script setup>
import { ref, computed, onMounted } from 'vue'
import { useRouter, useRoute } from 'vue-router'
import OnboardingShell from '../components/onboarding/OnboardingShell.vue'
import StepWelcome from '../components/onboarding/StepWelcome.vue'
import StepAbout from '../components/onboarding/StepAbout.vue'
import StepAddModel from '../components/onboarding/StepAddModel.vue'
import StepLinkBrokerage from '../components/onboarding/StepLinkBrokerage.vue'
import StepCreateInstance from '../components/onboarding/StepCreateInstance.vue'
import StepConnect from '../components/onboarding/StepConnect.vue'
import StepComplete from '../components/onboarding/StepComplete.vue'
import {
  fetchOnboardingState,
  markOnboardingComplete,
  listModels,
  listBrokerages,
  listInstances,
} from '../utils/onboarding.js'
import { getUser, fetchCurrentUser } from '../utils/auth.js'

const router = useRouter()
const route = useRoute()

const STEPS = [
  { id: 'welcome',    label: 'Welcome'    },
  { id: 'about',      label: 'About'      },
  { id: 'model',      label: 'Model'      },
  { id: 'brokerage',  label: 'Brokerage'  },
  { id: 'instance',   label: 'Instance'   },
  { id: 'connect',    label: 'Connect'    },
  { id: 'complete',   label: 'Done'       },
]

const current = ref(0)
const direction = ref('forward')
const busy = ref(false)
const finishing = ref(false)

const models = ref([])
const brokerages = ref([])
const instances = ref([])

const username = computed(() => (getUser()?.username) || '')

async function refreshAll() {
  const [m, b, i] = await Promise.all([
    listModels().catch(() => []),
    listBrokerages().catch(() => []),
    listInstances().catch(() => []),
  ])
  models.value = m
  brokerages.value = b
  instances.value = i
}

onMounted(async () => {
  // /onboarding always plays the wizard from step 1 — no auto-redirect for
  // already-onboarded users. They can re-run the flow anytime; the completion
  // flag is just re-affirmed at the end (idempotent).
  try {
    await fetchOnboardingState()
  } catch { /* ignore */ }
  await refreshAll()
})

function go(delta) {
  const target = Math.min(STEPS.length - 1, Math.max(0, current.value + delta))
  if (target === current.value) return
  direction.value = delta > 0 ? 'forward' : 'back'
  current.value = target
}

function onModelAdded(model) {
  if (model && model.id) {
    const idx = models.value.findIndex(m => m.id === model.id)
    if (idx === -1) models.value = [...models.value, model]
    else models.value.splice(idx, 1, model)
  } else {
    refreshAll()
  }
}
function onBrokerageAdded(acct) {
  if (acct && acct.id) {
    const idx = brokerages.value.findIndex(b => b.id === acct.id)
    if (idx === -1) brokerages.value = [...brokerages.value, acct]
    else brokerages.value.splice(idx, 1, acct)
  } else {
    refreshAll()
  }
}
function onInstanceAdded(inst) {
  if (inst && inst.id) {
    const idx = instances.value.findIndex(x => x.id === inst.id)
    if (idx === -1) instances.value = [...instances.value, inst]
    else instances.value.splice(idx, 1, inst)
  } else {
    refreshAll()
  }
}
function onLinked() {
  // No structural list change — just acknowledge.
}

const stepId = computed(() => STEPS[current.value].id)

const nextLabel = computed(() => {
  if (stepId.value === 'welcome') return 'Get started'
  if (stepId.value === 'complete') return 'Open dashboard'
  return 'Next'
})

const showSkip = computed(() => ['model', 'brokerage', 'instance', 'connect'].includes(stepId.value))

const skipLabel = computed(() => 'Skip for now')

async function handleNext() {
  if (stepId.value === 'complete') {
    return finishOnboarding()
  }
  go(1)
}

// Reject anything that isn't a strictly-internal path. Blocks open-redirect
// attacks via ?redirect=//evil.com/x or ?redirect=https://evil.com.
function _isSafeRedirect(target) {
  if (typeof target !== 'string' || !target) return false
  if (!target.startsWith('/')) return false
  if (target.startsWith('//') || target.startsWith('/\\')) return false
  if (target === '/onboarding' || target.startsWith('/onboarding?') || target.startsWith('/onboarding/')) return false
  return true
}

async function finishOnboarding() {
  finishing.value = true
  busy.value = true
  try {
    await markOnboardingComplete()
    await fetchCurrentUser()
  } catch {
    // best-effort: still navigate; the flag stays false and the next visit
    // will retry the redirect → onboarding loop.
  } finally {
    busy.value = false
    finishing.value = false
  }
  const redirect = typeof route.query.redirect === 'string' ? route.query.redirect : null
  if (_isSafeRedirect(redirect)) router.replace(redirect)
  else router.replace({ name: 'dashboard' })
}

function handleBack() { go(-1) }
function handleSkip() { go(1) }

function handleExit() {
  if (confirm('Exit onboarding? Your saved models, brokerages, and instances stay configured. You can re-run this flow later from /onboarding?force=1.')) {
    router.replace({ name: 'dashboard' })
  }
}
</script>

<template>
  <OnboardingShell
    :steps="STEPS"
    :current="current"
    :direction="direction"
    :can-go-back="current > 0 && current < STEPS.length - 1"
    :can-go-next="true"
    :next-label="nextLabel"
    :show-skip="showSkip"
    :skip-label="skipLabel"
    :busy="busy"
    @next="handleNext"
    @back="handleBack"
    @skip="handleSkip"
    @exit="handleExit"
  >
    <StepWelcome        v-if="stepId === 'welcome'"   :username="username" />
    <StepAbout          v-else-if="stepId === 'about'" />
    <StepAddModel       v-else-if="stepId === 'model'"     :models="models"           @added="onModelAdded" />
    <StepLinkBrokerage  v-else-if="stepId === 'brokerage'" :brokerages="brokerages"   @added="onBrokerageAdded" />
    <StepCreateInstance v-else-if="stepId === 'instance'"  :instances="instances"     @added="onInstanceAdded" />
    <StepConnect        v-else-if="stepId === 'connect'"   :brokerages="brokerages" :instances="instances" @linked="onLinked" />
    <StepComplete       v-else-if="stepId === 'complete'"
      :model-count="models.length"
      :brokerage-count="brokerages.length"
      :instance-count="instances.length"
    />
  </OnboardingShell>
</template>
