<script setup>
import { ref, computed, onMounted, onUnmounted } from 'vue'
import AppShell from '../layouts/AppShell.vue'
import NexusLiveLogs from '../components/NexusLiveLogs.vue'
import { getToken } from '../utils/auth.js'

const API_BASE = import.meta.env.DEV
  ? '/api'
  : (import.meta.env.VITE_API_URL || '/api')

function authHeaders() {
  const token = getToken()
  return token
    ? { Authorization: `Bearer ${token}`, 'Content-Type': 'application/json' }
    : { 'Content-Type': 'application/json' }
}

// ── State ─────────────────────────────────────────────────────────────────────
const status             = ref(null)
const loading            = ref(true)
const svcBusy            = ref(false)
const selectedPhases     = ref([])
const showStartModal = ref(false)
const showDeleteModal = ref(false)
const showAutoUpdateModal = ref(false)
const autoUpdateEnabled = ref(false)
const autoUpdateIntervalHours = ref(168)
const autoUpdateStartPhase = ref(3)
const autoUpdateEndPhase = ref(14)
const phase7HistoryQuarters = ref(1)
const phase7HistoryQuartersInitialized = ref(false)
const historicalModeEnabled = ref(false)
const historicalStartDate = ref('')
const forceBootstrapRebuild = ref(false)
const showRebuildConfirm = ref(false)
const rebuildConfirmText = ref('')
// Enable-bootstrap modal
const showEnableBootstrapModal = ref(false)
const enableBootstrapDate      = ref('')
const showDisableBootstrapModal = ref(false)
const rebuildMode = ref('non_destructive')
const rebuildForceBootstrap = ref(false)
const rebuildResult      = ref(null)   // {success, message, neo4j_cleared, rethinkdb_cleared}
const rebuildRequestPending = ref(false)
const deleteRequestPending = ref(false)
const deleteProgressVisible = ref(false)
const deleteActionError = ref('')
const rebuildCacheEntries = ref([])
const rebuildSelectedCachePaths = ref([])
const rebuildCacheLoading = ref(false)
const rebuildCacheAvailable = ref(false)
const rebuildCacheError = ref('')
const rebuildCacheRoot = ref('/app/.cache')
let pollTimer = null

const fallbackNexusPhaseOptions = [
  { value: 1, label: 'Phase 1: Company universe' },
  { value: 2, label: 'Phase 2: Easy relationships' },
  { value: 3, label: 'Phase 2b: SEC sector/industry' },
  { value: 4, label: 'Phase 3: Supply chain' },
  { value: 5, label: 'Phase 4: Competitive' },
  { value: 6, label: 'Phase 5: Macro/BEA' },
  { value: 7, label: 'Phase 6: GLEIF hierarchy' },
  { value: 8, label: 'Phase 6B: SEC EX-21 hierarchy' },
  { value: 9, label: 'Phase 7: 13F ownership' },
  { value: 10, label: 'Phase 8: USASpending' },
  { value: 11, label: 'Phase 9: Wikidata' },
  { value: 12, label: 'Phase 10: PatentsView' },
  { value: 13, label: 'Phase 11: 8-K agreements' },
  { value: 14, label: 'Phase 12: ETF universe' },
]
const fallbackDeletePhaseValues = new Set([3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14])
const fallbackDeletePhaseOptions = fallbackNexusPhaseOptions.filter(option =>
  fallbackDeletePhaseValues.has(Number(option.value))
)

// ── Computed ──────────────────────────────────────────────────────────────────
const control      = computed(() => status.value?.control || {})
const graphBuild   = computed(() => status.value?.graph_build || null)
const graphSummary = computed(() => status.value?.graph_summary || { relationship_counts: [], node_counts: {} })
const scraper      = computed(() => status.value?.scraper || null)
const bootstrap    = computed(() => status.value?.bootstrap || { enabled: false, phases: [], status: 'disabled' })
const graphBuilt   = computed(() => !!status.value?.graph_built)
const nexusServiceRunning = computed(() => !!control.value.running)
const nexusPhaseOptions = computed(() => {
  const options = status.value?.control?.phase_options
  return Array.isArray(options) && options.length ? options : fallbackNexusPhaseOptions
})
const deletePhaseOptions = computed(() => {
  const options = status.value?.control?.delete_phase_options
  return Array.isArray(options) && options.length ? options : fallbackDeletePhaseOptions
})
const allSelectedPhases = computed(() => nexusPhaseOptions.value.map(option => Number(option.value)))
const allDeletePhases = computed(() => deletePhaseOptions.value.map(option => Number(option.value)))
const nexusRunning = computed(() =>
  nexusServiceRunning.value && String(graphBuild.value?.status || '').toLowerCase() === 'running'
)
const stages       = computed(() => graphBuild.value?.stages || [])
const overallPct   = computed(() => graphBuild.value?.progress_pct ?? 0)
const etaFormatted = computed(() => graphBuild.value?.eta_formatted || null)
const nextAutoUpdateAt = computed(() => control.value?.next_auto_update_at || null)
const rebuildIsDestructive = computed(() => rebuildMode.value === 'destructive')
const bootstrapStartDate = computed(() =>
  control.value?.historical_start_date || bootstrap.value?.start_date || null
)
const bootstrapRebuildAvailable = computed(() =>
  !!bootstrapStartDate.value
)
const rebuildForceBootstrapEffective = computed(() =>
  rebuildIsDestructive.value
    ? bootstrapRebuildAvailable.value
    : (bootstrapRebuildAvailable.value && rebuildForceBootstrap.value)
)
const allRebuildCachePaths = computed(() => rebuildCacheEntries.value.map(entry => entry.path))
const allRebuildCacheSelected = computed(() =>
  allRebuildCachePaths.value.length > 0
  && allRebuildCachePaths.value.every(path => rebuildSelectedCachePaths.value.includes(path))
)
const autoUpdateSummary = computed(() => {
  if (!control.value?.auto_update_enabled) return 'Disabled'
  const hours = Number(control.value?.auto_update_interval_hours || 168)
  if (hours % 24 === 0) {
    const days = hours / 24
    return `Every ${days} day${days === 1 ? '' : 's'}`
  }
  return `Every ${hours} hour${hours === 1 ? '' : 's'}`
})
const historicalConfigInvalid = computed(() =>
  !!historicalModeEnabled.value && !String(historicalStartDate.value || '').trim()
)
const historicalCoverageSummary = computed(() => {
  if (!control.value?.historical_mode_enabled || !control.value?.historical_start_date) {
    return 'Disabled'
  }
  const start = control.value.historical_start_date
  const end = control.value.historical_coverage_end || 'building'
  return `${start} -> ${end}`
})
const autoUpdateStartLabel = computed(() =>
  control.value?.auto_update_start_phase_label
  || nexusPhaseOptions.value.find(option => Number(option.value) === Number(control.value?.auto_update_start_phase ?? 3))?.label
  || 'Phase 2b: SEC sector/industry'
)
const autoUpdateEndLabel = computed(() =>
  control.value?.auto_update_end_phase_label
  || nexusPhaseOptions.value.find(option => Number(option.value) === Number(control.value?.auto_update_end_phase ?? 14))?.label
  || 'Phase 12: ETF universe'
)
const bootstrapVisible = computed(() => true)
const bootstrapAllPhasesComplete = computed(() => {
  const phases = bootstrap.value?.phases || []
  return phases.length > 0 && phases.every(phase => phase?.status === 'completed' || phase?.bootstrap_complete)
})
// "Enabled but never actually run yet" — historical mode is on in control but bootstrap job hasn't started
const bootstrapEnabledNeverRun = computed(() =>
  !!control.value?.historical_mode_enabled &&
  (!bootstrap.value?.status || bootstrap.value?.status === 'disabled' || bootstrap.value?.status === 'never_built')
)
const bootstrapEffectiveStatus = computed(() => {
  if (bootstrapEnabledNeverRun.value) return 'enabled_never_run'
  const rawStatus = bootstrap.value?.status || 'disabled'
  if (rawStatus === 'running') return 'running'
  if (rawStatus === 'pending') return 'pending'
  if (rawStatus === 'partial') return 'partial'
  if (bootstrap.value?.complete || rawStatus === 'completed' || bootstrap.value?.last_status === 'completed' || bootstrapAllPhasesComplete.value) {
    return 'completed'
  }
  return rawStatus
})
const bootstrapFinished = computed(() => bootstrapEffectiveStatus.value === 'completed')
const bootstrapDisplayMessage = computed(() => {
  if (bootstrapEnabledNeverRun.value) {
    const start = control.value?.historical_start_date || 'unknown'
    return `Historical bootstrap is enabled from ${start} but has not run yet.`
  }
  if (bootstrapEffectiveStatus.value === 'completed') {
    const start = bootstrap.value?.start_date || control.value?.historical_start_date || 'unknown'
    const end = bootstrap.value?.coverage_end || control.value?.historical_coverage_end || 'today'
    return `Historical bootstrap complete for ${start} -> ${end}.`
  }
  return bootstrap.value?.message || 'Historical bootstrap status unavailable.'
})
const bootstrapCoverageSummary = computed(() => {
  if (bootstrapEnabledNeverRun.value) {
    const start = control.value?.historical_start_date || 'unknown'
    return `From ${start} (not yet run)`
  }
  if (bootstrapEffectiveStatus.value === 'never_built') return 'Never built'
  if (!bootstrap.value?.enabled) return 'Disabled'
  const start = bootstrap.value?.start_date || control.value?.historical_start_date || 'unknown'
  const end = bootstrap.value?.coverage_end || 'building'
  return `${start} -> ${end}`
})
const bootstrapPhaseRows = computed(() =>
  (bootstrap.value?.phases || []).map(phase => ({
    ...phase,
    liveStage: stages.value.find(stage => stage.key === phase.key) || null,
  }))
)

const completedCount = computed(() =>
  stages.value.filter(s => s.status === 'completed' || s.status === 'skipped').length
)
const totalBuildRuntimeSec = computed(() => {
  const total = stages.value.reduce((sum, stage) => {
    const sec = Number(stage?.duration_sec)
    return Number.isFinite(sec) ? sum + sec : sum
  }, 0)
  return total > 0 ? total : null
})
const relationshipCounts = computed(() => graphSummary.value?.relationship_counts || [])
const nodeCounts = computed(() => graphSummary.value?.node_counts || {})

const showBuilt = computed(() => graphBuilt.value && !nexusRunning.value)
const primaryStartLabel = computed(() => (showBuilt.value && nexusServiceRunning.value ? 'Re-run' : 'Start'))
const manualStartTitle = computed(() => (showBuilt.value && nexusServiceRunning.value ? 'Re-run Nexus' : 'Start Nexus'))
const rebuildOperation = computed(() => ({
  active: !!control.value?.rebuild_operation_active,
  destructive: !!control.value?.rebuild_operation_destructive,
  step: control.value?.rebuild_operation_step || null,
  message: control.value?.rebuild_operation_message || null,
  current: control.value?.rebuild_operation_current,
  total: control.value?.rebuild_operation_total,
  unit: control.value?.rebuild_operation_unit || null,
}))
const showRebuildProgressModal = computed(() =>
  rebuildRequestPending.value || rebuildOperation.value.active
)
const rebuildProgressTitle = computed(() => {
  if (rebuildOperation.value.destructive || rebuildIsDestructive.value) return 'Destructive Rebuild In Progress'
  return 'Rebuild In Progress'
})
const rebuildProgressMessage = computed(() => {
  if (rebuildOperation.value.message) return rebuildOperation.value.message
  if (rebuildRequestPending.value) {
    return rebuildIsDestructive.value
      ? 'Preparing destructive rebuild and clearing Neo4j graph data...'
      : 'Preparing Nexus rebuild request...'
  }
  return 'Processing Nexus rebuild request...'
})
const rebuildProgressCount = computed(() => {
  const current = rebuildOperation.value.current
  const total = rebuildOperation.value.total
  const unit = rebuildOperation.value.unit || 'items'
  if (current == null && total == null) return null
  if (total != null) return `${Number(current || 0).toLocaleString()} / ${Number(total).toLocaleString()} ${unit}`
  return `${Number(current || 0).toLocaleString()} ${unit}`
})
const manualPhaseSelectionInvalid = computed(() => selectedPhases.value.length === 0)
const deleteSelectedPhases = ref([])
const deletePhaseSelectionInvalid = computed(() => deleteSelectedPhases.value.length === 0)
const deleteOperation = computed(() => ({
  active: !!control.value?.delete_operation_active,
  step: control.value?.delete_operation_step || null,
  message: control.value?.delete_operation_message || null,
  current: control.value?.delete_operation_current,
  total: control.value?.delete_operation_total,
  unit: control.value?.delete_operation_unit || null,
  startedAt: control.value?.delete_operation_started_at || null,
  updatedAt: control.value?.delete_operation_updated_at || null,
  error: control.value?.delete_operation_error || null,
  selectedPhases: Array.isArray(control.value?.delete_operation_selected_phases)
    ? control.value.delete_operation_selected_phases.map(value => Number(value)).filter(Number.isFinite)
    : [],
  phaseRows: Array.isArray(control.value?.delete_operation_phase_rows)
    ? control.value.delete_operation_phase_rows
    : [],
}))
const showDeleteProgressState = computed(() =>
  deleteProgressVisible.value
  || deleteRequestPending.value
  || deleteOperation.value.active
)
const deleteProgressTitle = computed(() =>
  deleteOperation.value.active || deleteRequestPending.value
    ? 'Deleting Nexus Edges'
    : 'Delete Complete'
)
const deleteProgressMessage = computed(() => {
  if (deleteActionError.value) return deleteActionError.value
  if (deleteOperation.value.message) return deleteOperation.value.message
  if (deleteRequestPending.value) return 'Starting selected phase cleanup...'
  return 'Selected phase cleanup finished.'
})

// ── Fetch ─────────────────────────────────────────────────────────────────────
async function fetchStatus() {
  try {
    const res = await fetch(`${API_BASE}/nexus/status`, { headers: authHeaders() })
    if (!res.ok) return
    status.value = await res.json()
    if (!showAutoUpdateModal.value) {
      autoUpdateEnabled.value = !!status.value?.control?.auto_update_enabled
      autoUpdateIntervalHours.value = Number(status.value?.control?.auto_update_interval_hours || 168)
      autoUpdateStartPhase.value = Number(status.value?.control?.auto_update_start_phase || 3)
      autoUpdateEndPhase.value = Number(status.value?.control?.auto_update_end_phase || 14)
    }
    if (!showAutoUpdateModal.value && !showStartModal.value) {
      historicalModeEnabled.value = !!status.value?.control?.historical_mode_enabled
      historicalStartDate.value = status.value?.control?.historical_start_date || ''
    }
    if (!phase7HistoryQuartersInitialized.value) {
      phase7HistoryQuarters.value = Number(status.value?.control?.phase7_history_quarters || 1)
      phase7HistoryQuartersInitialized.value = true
    }
  } catch { /* non-critical */ } finally {
    loading.value = false
  }
}

// ── Controls ──────────────────────────────────────────────────────────────────
async function nexusPost(payload) {
  svcBusy.value = true
  try {
    await fetch(`${API_BASE}/nexus/control`, {
      method: 'POST',
      headers: authHeaders(),
      body: JSON.stringify(payload),
    })
    await fetchStatus()
  } catch { /* non-critical */ } finally {
    svcBusy.value = false
  }
}

function startNexus() {
  const historyQuarters = Math.max(1, Number(phase7HistoryQuarters.value || 1))
  if (historicalConfigInvalid.value) return
  const effectiveHistoricalStartDate = historicalModeEnabled.value
    ? historicalStartDate.value
    : bootstrapStartDate.value
  const effectiveHistoricalModeEnabled = !!(
    historicalModeEnabled.value
    || (forceBootstrapRebuild.value && effectiveHistoricalStartDate)
  )
  showStartModal.value = false
  nexusPost({
    running: true,
    phase7_history_quarters: historyQuarters,
    historical_mode_enabled: effectiveHistoricalModeEnabled,
    ...(forceBootstrapRebuild.value ? { force_bootstrap_rebuild: true } : {}),
    ...(effectiveHistoricalModeEnabled && effectiveHistoricalStartDate
      ? { historical_start_date: effectiveHistoricalStartDate }
      : {}),
    selected_phases: [...selectedPhases.value].sort((a, b) => a - b),
  })
}

function stopNexus() { nexusPost({ running: false }) }

function openEnableBootstrapModal() {
  enableBootstrapDate.value = control.value?.historical_start_date || ''
  showEnableBootstrapModal.value = true
}
function closeEnableBootstrapModal() {
  showEnableBootstrapModal.value = false
}
function confirmEnableBootstrap() {
  if (!enableBootstrapDate.value) return
  nexusPost({ historical_mode_enabled: true, historical_start_date: enableBootstrapDate.value })
  showEnableBootstrapModal.value = false
}
function disableBootstrap() {
  nexusPost({ historical_mode_enabled: false })
}

function openDisableBootstrapModal() {
  showDisableBootstrapModal.value = true
}

function closeDisableBootstrapModal() {
  showDisableBootstrapModal.value = false
}

function confirmDisableBootstrap() {
  showDisableBootstrapModal.value = false
  disableBootstrap()
}

function openStartModal() {
  const controlSelected = Array.isArray(control.value?.selected_phases)
    ? control.value.selected_phases.map(value => Number(value)).filter(Number.isFinite)
    : []
  selectedPhases.value = (controlSelected.length ? controlSelected : allSelectedPhases.value).slice()
  phase7HistoryQuarters.value = Number(control.value?.phase7_history_quarters || 1)
  historicalModeEnabled.value = !!control.value?.historical_mode_enabled
  historicalStartDate.value = control.value?.historical_start_date || ''
  forceBootstrapRebuild.value = false
  showStartModal.value = true
}

function closeStartModal() {
  showStartModal.value = false
}

function selectAllSelectedPhases() {
  selectedPhases.value = allSelectedPhases.value.slice()
}

function clearSelectedPhases() {
  selectedPhases.value = []
}

function openDeleteModal() {
  const activeSelected = deleteOperation.value.selectedPhases
  deleteSelectedPhases.value = (activeSelected.length ? activeSelected : allDeletePhases.value).slice()
  deleteActionError.value = ''
  deleteProgressVisible.value = deleteOperation.value.active
  showDeleteModal.value = true
}

function closeDeleteModal() {
  if (deleteOperation.value.active || deleteRequestPending.value) return
  showDeleteModal.value = false
  deleteProgressVisible.value = false
  deleteActionError.value = ''
}

function selectAllDeletePhases() {
  deleteSelectedPhases.value = allDeletePhases.value.slice()
}

function clearDeletePhases() {
  deleteSelectedPhases.value = []
}

async function deleteEdges() {
  if (deletePhaseSelectionInvalid.value) return
  deleteRequestPending.value = true
  deleteProgressVisible.value = true
  deleteActionError.value = ''
  showDeleteModal.value = true
  try {
    const res = await fetch(`${API_BASE}/nexus/delete-edges`, {
      method: 'POST',
      headers: authHeaders(),
      body: JSON.stringify({
        selected_phases: [...deleteSelectedPhases.value].sort((a, b) => a - b),
      }),
    })
    const data = await res.json().catch(() => ({}))
    if (!res.ok) {
      deleteProgressVisible.value = false
      deleteActionError.value = data?.detail || 'Unable to start the delete operation.'
      return
    }
    await fetchStatus()
  } catch {
    deleteProgressVisible.value = false
    deleteActionError.value = 'Unable to start the delete operation.'
  } finally {
    deleteRequestPending.value = false
  }
}

function openAutoUpdateModal() {
  autoUpdateEnabled.value = !!control.value?.auto_update_enabled
  autoUpdateIntervalHours.value = Number(control.value?.auto_update_interval_hours || 168)
  autoUpdateStartPhase.value = Number(control.value?.auto_update_start_phase || 3)
  autoUpdateEndPhase.value = Number(control.value?.auto_update_end_phase || 14)
  phase7HistoryQuarters.value = Number(control.value?.phase7_history_quarters || 1)
  historicalModeEnabled.value = !!control.value?.historical_mode_enabled
  historicalStartDate.value = control.value?.historical_start_date || ''
  showAutoUpdateModal.value = true
}

function saveAutoUpdate() {
  const historyQuarters = Math.max(1, Number(phase7HistoryQuarters.value || 1))
  nexusPost({
    auto_update_enabled: autoUpdateEnabled.value,
    auto_update_interval_hours: Number(autoUpdateIntervalHours.value || 168),
    auto_update_start_phase: Number(autoUpdateStartPhase.value || 3),
    auto_update_end_phase: Number(autoUpdateEndPhase.value || 14),
    phase7_history_quarters: historyQuarters,
    ...(autoUpdateEnabled.value ? { running: true } : {}),
  })
  showAutoUpdateModal.value = false
}

function closeRebuildModal() {
  showRebuildConfirm.value = false
  rebuildConfirmText.value = ''
}

async function fetchRebuildCacheEntries() {
  rebuildCacheLoading.value = true
  rebuildCacheError.value = ''
  rebuildCacheEntries.value = []
  rebuildSelectedCachePaths.value = []
  rebuildCacheAvailable.value = false
  rebuildCacheRoot.value = '/app/.cache'
  try {
    const res = await fetch(`${API_BASE}/nexus/cache`, { headers: authHeaders() })
    const data = await res.json().catch(() => ({}))
    if (!res.ok) {
      rebuildCacheError.value = data?.detail || 'Unable to load Nexus cache entries.'
      return
    }
    rebuildCacheEntries.value = Array.isArray(data?.entries) ? data.entries : []
    rebuildCacheAvailable.value = !!data?.available
    rebuildCacheRoot.value = data?.cache_root || '/app/.cache'
    rebuildCacheError.value = data?.error || ''
  } catch {
    rebuildCacheError.value = 'Unable to load Nexus cache entries.'
  } finally {
    rebuildCacheLoading.value = false
  }
}

function openRebuildModal() {
  rebuildConfirmText.value = ''
  rebuildMode.value = 'non_destructive'
  rebuildForceBootstrap.value = false
  rebuildResult.value = null
  showRebuildConfirm.value = true
  fetchRebuildCacheEntries()
}

function toggleAllRebuildCacheSelections() {
  rebuildSelectedCachePaths.value = allRebuildCacheSelected.value
    ? []
    : [...allRebuildCachePaths.value]
}

async function rebuildNexus() {
  if (rebuildConfirmText.value.toLowerCase().trim() !== 'confirm') return
  closeRebuildModal()
  rebuildResult.value = null
  rebuildRequestPending.value = true
  svcBusy.value = true
  try {
    const res = await fetch(`${API_BASE}/nexus/rebuild`, {
      method: 'POST',
      headers: authHeaders(),
      body: JSON.stringify({
        confirm: true,
        destructive: rebuildIsDestructive.value,
        force_bootstrap_rebuild: rebuildForceBootstrapEffective.value,
        delete_cache_paths: rebuildSelectedCachePaths.value,
      }),
    })
    const data = await res.json().catch(() => ({}))
    rebuildResult.value = res.ok
      ? data
      : {
          success: false,
          message: data?.detail || data?.message || 'Unable to start the Nexus rebuild.',
          error: data?.error || null,
        }
    await fetchStatus()
    // Auto-dismiss after 8s
    setTimeout(() => { rebuildResult.value = null }, 8000)
  } catch { /* non-critical */ } finally {
    rebuildRequestPending.value = false
    svcBusy.value = false
  }
}

// ── Stage helpers ─────────────────────────────────────────────────────────────
const STAGE_META = {
  pending:   { icon: 'radio_button_unchecked', spin: false, color: 'text-slate-600',   bg: 'bg-slate-800 border-slate-700' },
  running:   { icon: null,                     spin: true,  color: 'text-sky-400',     bg: 'bg-sky-500/10 border-sky-500/30' },
  stopped:   { icon: 'pause_circle',           spin: false, color: 'text-amber-400',   bg: 'bg-amber-500/10 border-amber-500/30' },
  completed: { icon: 'check_circle',           spin: false, color: 'text-emerald-400', bg: 'bg-emerald-500/10 border-emerald-500/30' },
  skipped:   { icon: 'remove_circle',          spin: false, color: 'text-slate-500',   bg: 'bg-slate-500/10 border-slate-600' },
  failed:    { icon: 'cancel',                 spin: false, color: 'text-red-400',     bg: 'bg-red-500/10 border-red-500/30' },
}

const BOOTSTRAP_META = {
  never_built:      { icon: 'history_toggle_off', color: 'text-slate-500',   badge: 'text-slate-400 bg-slate-500/10 border-slate-700' },
  disabled:         { icon: 'history_toggle_off', color: 'text-slate-500',   badge: 'text-slate-500 bg-slate-500/10 border-slate-700' },
  enabled_never_run:{ icon: 'schedule',           color: 'text-sky-400',     badge: 'text-sky-400 bg-sky-500/10 border-sky-500/20' },
  pending:          { icon: 'schedule',           color: 'text-slate-300',   badge: 'text-slate-300 bg-slate-500/10 border-slate-700' },
  running:          { icon: 'progress_activity',  color: 'text-sky-400',     badge: 'text-sky-400 bg-sky-500/10 border-sky-500/20' },
  partial:          { icon: 'history',            color: 'text-amber-400',   badge: 'text-amber-400 bg-amber-500/10 border-amber-500/20' },
  completed:        { icon: 'check_circle',       color: 'text-emerald-400', badge: 'text-emerald-400 bg-emerald-500/10 border-emerald-500/20' },
}

function stageMeta(st)  { return STAGE_META[st] || STAGE_META.pending }
function bootstrapMeta(st) { return BOOTSTRAP_META[st] || BOOTSTRAP_META.pending }

function bootstrapStatusText(st) {
  const raw = String(st || '').trim()
  if (!raw) return 'Unknown'
  if (raw === 'never_built') return 'Never built'
  return raw.replace(/_/g, ' ')
}

function bootstrapPillText(st) {
  if (st === 'completed')         return 'Bootstrap Ready'
  if (st === 'running')           return 'Bootstrap Running'
  if (st === 'never_built')       return 'Bootstrap Not Built'
  if (st === 'disabled')          return 'Disabled'
  if (st === 'enabled_never_run') return 'Enabled — Never Run'
  if (st === 'partial')           return 'Bootstrap Partial'
  return 'Bootstrap Pending'
}

function stageLineColor(stage) {
  if (stage.status === 'completed' || stage.status === 'skipped') return 'bg-emerald-500/30'
  if (stage.status === 'running') return 'bg-sky-500/40'
  if (stage.status === 'stopped') return 'bg-amber-500/40'
  return 'bg-slate-700'
}

function substepPct(stage) {
  if (!stage.total_substeps) return 0
  return Math.round(((stage.substeps_completed ?? 0) / stage.total_substeps) * 100)
}

function fmtDuration(sec) {
  if (sec == null) return null
  if (sec < 60) return sec.toFixed(1) + 's'
  if (sec >= 3600) {
    const h = Math.floor(sec / 3600)
    const m = Math.round((sec % 3600) / 60)
    return m ? `${h}h ${m}m` : `${h}h`
  }
  const m = Math.floor(sec / 60)
  const s = Math.round(sec % 60)
  return s ? `${m}m ${s}s` : `${m}m`
}

function fmtDateTime(v) {
  if (!v) return '—'
  try { return new Date(v).toLocaleString(undefined, { dateStyle: 'medium', timeStyle: 'short' }) }
  catch { return String(v) }
}

// ── Polling ───────────────────────────────────────────────────────────────────
function fmtCoverage(start, end) {
  if (start && end) return `${start} -> ${end}`
  if (end) return `Through ${end}`
  if (start) return `From ${start}`
  return 'Waiting for coverage'
}

function pollDelayMs() {
  return nexusRunning.value ? 2000 : 5000
}

async function pollOnce() {
  pollTimer = null
  await fetchStatus()
  startPoll()
}

function startPoll() {
  if (!pollTimer) pollTimer = setTimeout(pollOnce, pollDelayMs())
}

function stopPoll()  { if (pollTimer) { clearTimeout(pollTimer); pollTimer = null } }

onMounted(async () => { await fetchStatus(); startPoll() })
onUnmounted(stopPoll)
</script>

<template>
  <AppShell>
    <main class="flex-1 px-4 py-6 sm:px-6 sm:py-8 lg:px-8 lg:py-10">

      <!-- Header -->
      <div class="flex items-start justify-between gap-4 mb-8 flex-wrap">
        <div>
          <h1 class="text-2xl font-bold text-slate-100">Nexus Graph</h1>
          <p class="text-slate-500 text-sm mt-1">Knowledge graph builder — S&P 500 company relationships across 10 data sources.</p>
        </div>
        <div class="flex items-center gap-2 flex-wrap justify-end shrink-0">
          <!-- Status pill -->
          <span
            class="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full text-[11px] font-semibold border"
            :class="nexusRunning
              ? 'text-sky-400 bg-sky-500/10 border-sky-500/20'
              : showBuilt
                ? 'text-emerald-400 bg-emerald-500/10 border-emerald-500/20'
                : 'text-slate-500 bg-slate-500/10 border-slate-700'"
          >
            <span
              class="size-1.5 rounded-full"
              :class="nexusRunning ? 'bg-sky-400 animate-pulse' : showBuilt ? 'bg-emerald-400' : 'bg-slate-600'"
            ></span>
            {{ nexusRunning ? 'Building…' : showBuilt ? 'Graph Ready' : 'Idle' }}
          </span>

          <span
            v-if="bootstrapVisible"
            class="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full text-[11px] font-semibold border"
            :class="bootstrapMeta(bootstrapEffectiveStatus).badge"
          >
            <span
              class="material-symbols-outlined text-[13px]"
              :class="[bootstrapMeta(bootstrapEffectiveStatus).color, bootstrapEffectiveStatus === 'running' ? 'animate-spin' : '']"
            >{{ bootstrapMeta(bootstrapEffectiveStatus).icon }}</span>
            {{ bootstrapPillText(bootstrapEffectiveStatus) }}
          </span>

          <!-- Start button (when not running) -->
          <button
            v-if="!nexusRunning"
            @click="openStartModal"
            :disabled="svcBusy"
            class="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-semibold border border-emerald-500/30 bg-emerald-500/10 text-emerald-400 hover:bg-emerald-500/20 transition-colors disabled:opacity-50"
          >
            <span class="material-symbols-outlined text-[14px]">play_arrow</span>
            {{ primaryStartLabel }}
          </button>
          <!-- Stop button (when running) -->
          <button
            v-if="nexusServiceRunning"
            @click="stopNexus"
            :disabled="svcBusy"
            class="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-semibold border border-red-500/30 bg-red-500/10 text-red-400 hover:bg-red-500/20 transition-colors disabled:opacity-50"
          >
            <span class="material-symbols-outlined text-[14px]">stop</span>
            Stop
          </button>
          <!-- Full Rebuild button (always available) -->
          <button
            @click="openAutoUpdateModal"
            :disabled="svcBusy"
            class="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-semibold border border-slate-700 text-slate-300 hover:text-sky-300 hover:border-sky-500/30 hover:bg-sky-500/5 transition-colors disabled:opacity-50"
          >
            <span class="material-symbols-outlined text-[14px]">schedule</span>
            Auto-update
          </button>
          <button
            @click="openRebuildModal"
            :disabled="svcBusy"
            class="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-semibold border border-slate-700 text-slate-400 hover:text-red-400 hover:border-red-500/30 hover:bg-red-500/5 transition-colors disabled:opacity-50"
          >
            <span class="material-symbols-outlined text-[14px]">reset_wrench</span>
            Full Rebuild
          </button>
          <button
            @click="openDeleteModal"
            :disabled="svcBusy || nexusServiceRunning || rebuildOperation.active"
            class="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-semibold border border-slate-700 text-slate-400 hover:text-amber-300 hover:border-amber-500/30 hover:bg-amber-500/5 transition-colors disabled:opacity-50"
          >
            <span class="material-symbols-outlined text-[14px]">delete_sweep</span>
            Delete edges
          </button>
        </div>
      </div>

      <!-- Rebuild result banner -->
      <Transition name="modal-fade">
        <div
          v-if="rebuildResult"
          class="mb-6 rounded-xl border px-4 py-3 flex items-start gap-3"
          :class="rebuildResult.success
            ? 'bg-emerald-500/10 border-emerald-500/20'
            : 'bg-red-500/10 border-red-500/20'"
        >
          <span
            class="material-symbols-outlined text-base mt-0.5 shrink-0"
            :class="rebuildResult.success ? 'text-emerald-400' : 'text-red-400'"
          >{{ rebuildResult.success ? 'check_circle' : 'error' }}</span>
          <div class="flex-1 min-w-0 text-xs">
            <p :class="rebuildResult.success ? 'text-emerald-300' : 'text-red-300'" class="font-semibold mb-1">
              {{ rebuildResult.success ? (rebuildResult.non_destructive ? 'Rebuild initiated' : 'Destructive rebuild initiated') : 'Rebuild failed' }}
            </p>
            <p class="text-slate-400">{{ rebuildResult.message || 'No message returned.' }}</p>
            <div v-if="!rebuildResult.non_destructive" class="flex flex-wrap gap-x-4 gap-y-1 text-slate-400 mt-1">
              <span>
                <span class="material-symbols-outlined text-[11px] align-middle mr-0.5"
                  :class="rebuildResult.neo4j_cleared ? 'text-emerald-400' : 'text-red-400'">
                  {{ rebuildResult.neo4j_cleared ? 'check' : 'close' }}
                </span>
                Neo4j {{ rebuildResult.neo4j_cleared ? 'cleared' : 'not cleared' }}
              </span>
              <span>
                <span class="material-symbols-outlined text-[11px] align-middle mr-0.5"
                  :class="rebuildResult.rethinkdb_cleared ? 'text-emerald-400' : 'text-red-400'">
                  {{ rebuildResult.rethinkdb_cleared ? 'check' : 'close' }}
                </span>
                RethinkDB {{ rebuildResult.rethinkdb_cleared ? 'cleared' : 'not cleared' }}
              </span>
            </div>
            <p v-if="rebuildResult.deleted_cache_paths?.length" class="text-slate-400 mt-1">
              Deleted cache entries: <span class="font-mono text-slate-300">{{ rebuildResult.deleted_cache_paths.join(', ') }}</span>
            </p>
            <p v-if="rebuildResult.container_restarted" class="text-slate-400 mt-1">
              Nexus container restart: <span class="font-semibold text-slate-300">{{ rebuildResult.container_restart_mode === 'server_recreate' ? 'recreated by server' : 'restarted in place' }}</span>
            </p>
            <p v-if="rebuildResult.neo4j_skip_reason" class="text-amber-400/80 mt-1">{{ rebuildResult.neo4j_skip_reason }}</p>
            <p v-if="rebuildResult.error" class="text-red-400/80 mt-1">{{ rebuildResult.error }}</p>
          </div>
          <button @click="rebuildResult = null" class="shrink-0 text-slate-600 hover:text-slate-400 transition-colors">
            <span class="material-symbols-outlined text-[14px]">close</span>
          </button>
        </div>
      </Transition>

      <!-- Loading skeleton -->
      <div v-if="loading" class="space-y-4">
        <div v-for="n in 5" :key="n" class="glass-card rounded-2xl animate-pulse" :style="{ height: n === 1 ? '80px' : '56px' }"></div>
      </div>

      <template v-else-if="status">

        <div class="glass-card rounded-2xl p-4 mb-6 border border-border-subtle">
          <div class="flex items-start justify-between gap-4 flex-wrap">
            <div>
              <p class="text-[10px] text-slate-600 uppercase tracking-widest font-medium mb-2">Auto-update</p>
              <p class="text-sm font-semibold text-slate-200">{{ autoUpdateSummary }}</p>
              <p class="text-xs text-slate-500 mt-1">Next run {{ nextAutoUpdateAt ? fmtDateTime(nextAutoUpdateAt) : 'not scheduled' }}</p>
              <p class="text-[11px] text-slate-600 mt-1">
                Refresh range: {{ autoUpdateStartLabel }} to {{ autoUpdateEndLabel }}
              </p>
              <p class="text-[11px] text-slate-600 mt-1">
                13F history: {{ control.phase7_history_quarters ?? 1 }} quarter<span v-if="(control.phase7_history_quarters ?? 1) !== 1">s</span>
              </p>
              <p class="text-[11px] text-slate-600 mt-1">
                Historical bootstrap: {{ historicalCoverageSummary }}
              </p>
            </div>
            <button
              @click="openAutoUpdateModal"
              :disabled="svcBusy"
              class="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-semibold border border-slate-700 text-slate-300 hover:text-sky-300 hover:border-sky-500/30 hover:bg-sky-500/5 transition-colors disabled:opacity-50"
            >
              <span class="material-symbols-outlined text-[14px]">tune</span>
              Configure
            </button>
          </div>
        </div>

        <!-- ─────────────── BUILT + IDLE state ─────────────────────────── -->
        <div v-if="bootstrapVisible" class="glass-card rounded-2xl p-5 mb-6 border border-amber-500/15">
          <div class="flex items-start justify-between gap-4 flex-wrap">
            <div class="min-w-0 flex-1">
              <div class="flex items-center gap-2">
                <span class="material-symbols-outlined text-amber-400 text-[18px]">history</span>
                <p class="text-sm font-semibold text-slate-200">Historical Bootstrap</p>
              </div>
              <p class="text-xs text-slate-500 mt-1">{{ bootstrapDisplayMessage }}</p>
            </div>
            <div class="flex items-center gap-2 shrink-0">
              <!-- Enable button (shown when not enabled and not running/completed) -->
              <button
                v-if="!control?.historical_mode_enabled && bootstrapEffectiveStatus !== 'running' && bootstrapEffectiveStatus !== 'completed'"
                @click="openEnableBootstrapModal"
                class="inline-flex items-center gap-1 px-2.5 py-1 rounded-lg text-[11px] font-semibold border border-sky-500/30 bg-sky-500/10 text-sky-400 hover:bg-sky-500/20 transition-colors"
              >
                <span class="material-symbols-outlined text-[13px]">toggle_on</span>
                Enable
              </button>
              <!-- Disable button (shown when enabled but not running/completed) -->
              <button
                v-else-if="control?.historical_mode_enabled && bootstrapEnabledNeverRun"
                @click="openDisableBootstrapModal"
                class="inline-flex items-center gap-1 px-2.5 py-1 rounded-lg text-[11px] font-semibold border border-slate-600 bg-slate-500/10 text-slate-400 hover:bg-slate-500/20 transition-colors"
              >
                <span class="material-symbols-outlined text-[13px]">toggle_off</span>
                Disable
              </button>
              <button
                v-else-if="control?.historical_mode_enabled"
                @click="openDisableBootstrapModal"
                class="inline-flex items-center gap-1 px-2.5 py-1 rounded-lg text-[11px] font-semibold border border-slate-600 bg-slate-500/10 text-slate-400 hover:bg-slate-500/20 transition-colors"
              >
                <span class="material-symbols-outlined text-[13px]">toggle_off</span>
                Disable
              </button>
              <span
                class="text-[10px] font-bold px-2 py-0.5 rounded-full border uppercase tracking-wide"
                :class="bootstrapMeta(bootstrapEffectiveStatus).badge"
              >{{ bootstrapPillText(bootstrapEffectiveStatus) }}</span>
            </div>
          </div>

          <template v-if="bootstrapFinished">
            <div class="grid grid-cols-2 lg:grid-cols-4 gap-4 mt-4">
              <div class="rounded-xl border border-border-subtle bg-slate-900/40 px-4 py-3">
                <p class="text-[10px] text-slate-600 uppercase tracking-widest font-medium mb-2">Coverage</p>
                <p class="text-sm font-semibold text-slate-200">{{ bootstrapCoverageSummary }}</p>
              </div>
              <div class="rounded-xl border border-border-subtle bg-slate-900/40 px-4 py-3">
                <p class="text-[10px] text-slate-600 uppercase tracking-widest font-medium mb-2">Duration</p>
                <p class="text-sm font-semibold text-slate-200">{{ fmtDuration(bootstrap.duration_sec) || '—' }}</p>
              </div>
              <div class="rounded-xl border border-border-subtle bg-slate-900/40 px-4 py-3">
                <p class="text-[10px] text-slate-600 uppercase tracking-widest font-medium mb-2">Completed</p>
                <p class="text-sm font-semibold text-slate-200">{{ fmtDateTime(bootstrap.completed_at) }}</p>
              </div>
              <div class="rounded-xl border border-border-subtle bg-slate-900/40 px-4 py-3">
                <p class="text-[10px] text-slate-600 uppercase tracking-widest font-medium mb-2">Temporal Phases</p>
                <p class="text-sm font-semibold text-slate-200">{{ bootstrap.completed_phases }}/{{ bootstrap.total_phases }}</p>
              </div>
            </div>
          </template>

          <template v-else>
            <div class="grid grid-cols-1 sm:grid-cols-3 gap-4 mt-4 mb-4">
              <div class="rounded-xl border border-border-subtle bg-slate-900/40 px-4 py-3">
                <p class="text-[10px] text-slate-600 uppercase tracking-widest font-medium mb-2">Coverage Target</p>
                <p class="text-sm font-semibold text-slate-200">{{ bootstrapCoverageSummary }}</p>
              </div>
              <div class="rounded-xl border border-border-subtle bg-slate-900/40 px-4 py-3">
                <p class="text-[10px] text-slate-600 uppercase tracking-widest font-medium mb-2">Temporal Phases</p>
                <p class="text-sm font-semibold text-slate-200">
                  <template v-if="bootstrap.total_phases != null">{{ bootstrap.completed_phases ?? 0 }}/{{ bootstrap.total_phases }} complete</template>
                  <template v-else>—</template>
                </p>
              </div>
              <div class="rounded-xl border border-border-subtle bg-slate-900/40 px-4 py-3">
                <p class="text-[10px] text-slate-600 uppercase tracking-widest font-medium mb-2">Started</p>
                <p class="text-sm font-semibold text-slate-200">{{ fmtDateTime(bootstrap.started_at) }}</p>
              </div>
            </div>

            <div class="space-y-3">
              <div
                v-for="phase in bootstrapPhaseRows"
                :key="phase.key"
                class="rounded-xl border border-border-subtle bg-slate-900/35 px-4 py-3"
              >
                <div class="flex items-start justify-between gap-3">
                  <div class="min-w-0 flex-1">
                    <div class="flex items-center gap-2">
                      <span
                        class="material-symbols-outlined text-[16px]"
                        :class="[bootstrapMeta(phase.status).color, phase.status === 'running' ? 'animate-spin' : '']"
                      >{{ bootstrapMeta(phase.status).icon }}</span>
                      <p class="text-sm font-semibold text-slate-200">{{ phase.label }}</p>
                    </div>
                    <!-- Live message when running; coverage dates otherwise -->
                    <p class="text-[11px] text-slate-500 mt-1">
                      {{ phase.liveStage?.message || fmtCoverage(phase.coverage_start, phase.coverage_end) }}
                    </p>
                    <!-- Substep progress bar (live only) -->
                    <div v-if="phase.liveStage?.status === 'running' && phase.liveStage.total_substeps > 1" class="mt-2 flex items-center gap-2">
                      <div class="h-1 bg-slate-700 rounded-full overflow-hidden w-28">
                        <div
                          class="h-full bg-sky-500/70 rounded-full transition-all duration-500"
                          :style="{ width: substepPct(phase.liveStage) + '%' }"
                        ></div>
                      </div>
                      <span class="text-[10px] text-sky-500/80 font-mono">{{ phase.liveStage.substeps_completed ?? 0 }}/{{ phase.liveStage.total_substeps }} substeps</span>
                    </div>
                    <!-- Checkpoint: last saved point (partial phases only) -->
                    <p v-if="phase.status === 'partial' && phase.last_incremental_end" class="text-[10px] text-amber-500/60 mt-1 flex items-center gap-1">
                      <span class="material-symbols-outlined text-[11px]">bookmark</span>
                      Checkpoint: {{ phase.last_incremental_end }}
                    </p>
                  </div>
                  <div class="shrink-0 text-right">
                    <span
                      class="text-[10px] font-bold px-2 py-0.5 rounded-full border uppercase tracking-wide"
                      :class="bootstrapMeta(phase.status).badge"
                    >{{ phase.status }}</span>
                    <!-- Stats: snapshots / filings / edges -->
                    <div v-if="phase.processed_snapshots != null || phase.filings_processed != null || phase.edge_count != null" class="mt-2 space-y-0.5">
                      <p v-if="phase.processed_snapshots != null" class="text-[10px] text-slate-500">
                        {{ phase.processed_snapshots }} snapshots
                      </p>
                      <p v-if="phase.filings_processed != null" class="text-[10px] text-slate-500">
                        {{ phase.filings_processed.toLocaleString() }} filings
                      </p>
                      <p v-if="phase.edge_count != null" class="text-[10px] text-slate-600">
                        {{ phase.edge_count.toLocaleString() }} edges
                      </p>
                      <p v-if="phase.latest_event_date" class="text-[10px] text-slate-600">
                        Latest: {{ phase.latest_event_date }}
                      </p>
                    </div>
                  </div>
                </div>
              </div>
            </div>
          </template>
        </div>

        <div v-if="relationshipCounts.length || Object.keys(nodeCounts).length" class="glass-card rounded-2xl p-5 border border-border-subtle mb-6">
          <div class="flex items-start justify-between gap-4 flex-wrap mb-4">
            <div>
              <p class="text-xs font-semibold text-slate-400 uppercase tracking-widest">Graph Counts</p>
              <p class="text-[11px] text-slate-600 mt-1">
                {{ nexusRunning
                  ? 'Current Neo4j relationship totals while the graph build is running.'
                  : 'Current Neo4j relationship totals for the built graph.' }}
              </p>
            </div>
            <div class="flex items-center gap-5 flex-wrap text-right">
              <div v-if="nodeCounts.companies != null">
                <p class="text-[10px] text-slate-600 uppercase tracking-widest font-medium">Companies</p>
                <p class="text-2xl font-bold text-slate-100 leading-none">{{ nodeCounts.companies?.toLocaleString?.() ?? nodeCounts.companies }}</p>
              </div>
              <div v-if="nodeCounts.edge_intervals != null">
                <p class="text-[10px] text-slate-600 uppercase tracking-widest font-medium">Intervals</p>
                <p class="text-2xl font-bold text-slate-100 leading-none">{{ nodeCounts.edge_intervals?.toLocaleString?.() ?? nodeCounts.edge_intervals }}</p>
              </div>
            </div>
          </div>
          <div class="grid grid-cols-1 sm:grid-cols-2 xl:grid-cols-3 gap-2">
            <div
              v-for="rel in relationshipCounts"
              :key="rel.key"
              class="rounded-xl border border-border-subtle bg-slate-900/40 px-4 py-3"
            >
              <div class="flex items-start justify-between gap-3">
                <div class="min-w-0">
                  <p class="text-xs font-medium text-slate-300 truncate">{{ rel.label }}</p>
                  <p class="text-[10px] text-slate-600 mt-1">{{ rel.key }}</p>
                </div>
                <div class="text-right shrink-0">
                  <p class="text-xl font-bold text-slate-100 leading-none">{{ rel.active_count?.toLocaleString?.() ?? rel.active_count }}</p>
                  <p class="text-[10px] text-slate-500 mt-1">
                    {{ rel.total_count !== rel.active_count ? `${rel.total_count?.toLocaleString?.() ?? rel.total_count} total` : 'active' }}
                  </p>
                </div>
              </div>
            </div>
          </div>
        </div>

        <template v-if="showBuilt">

          <!-- Success hero -->
          <div class="glass-card rounded-2xl p-5 mb-6 border border-emerald-500/20 flex items-center gap-4 flex-wrap">
            <div class="size-11 rounded-2xl bg-emerald-500/10 border border-emerald-500/20 flex items-center justify-center shrink-0">
              <span class="material-symbols-outlined text-emerald-400 text-xl">hub</span>
            </div>
            <div class="flex-1 min-w-0">
              <p class="text-sm font-bold text-emerald-400">Knowledge Graph is Built</p>
              <p class="text-xs text-slate-400 mt-0.5">{{ completedCount }} of {{ stages.length }} stages completed · Ready for strategy use.</p>
            </div>
            <button
              @click="openRebuildModal"
              :disabled="svcBusy"
              class="inline-flex items-center gap-1.5 px-3 py-2 rounded-lg text-xs font-semibold border border-slate-700 text-slate-400 hover:text-red-400 hover:border-red-500/30 hover:bg-red-500/5 transition-colors disabled:opacity-50 shrink-0"
            >
              <span class="material-symbols-outlined text-[14px]">reset_wrench</span>
              Full Rebuild
            </button>
            <button
              @click="openDeleteModal"
              :disabled="svcBusy || nexusServiceRunning || rebuildOperation.active"
              class="inline-flex items-center gap-1.5 px-3 py-2 rounded-lg text-xs font-semibold border border-slate-700 text-slate-400 hover:text-amber-300 hover:border-amber-500/30 hover:bg-amber-500/5 transition-colors disabled:opacity-50 shrink-0"
            >
              <span class="material-symbols-outlined text-[14px]">delete_sweep</span>
              Delete edges
            </button>
          </div>

          <!-- Stat cards -->
          <div class="grid grid-cols-2 lg:grid-cols-4 gap-4 mb-6">
            <div class="glass-card rounded-xl p-4 border border-border-subtle">
              <p class="text-[10px] text-slate-600 uppercase tracking-widest font-medium mb-2">Stages Done</p>
              <p class="text-3xl font-bold text-slate-100 leading-none">{{ completedCount }}<span class="text-sm text-slate-500 font-normal ml-1">/{{ stages.length }}</span></p>
            </div>
            <div class="glass-card rounded-xl p-4 border border-border-subtle">
              <p class="text-[10px] text-slate-600 uppercase tracking-widest font-medium mb-2">SEC Tickers</p>
              <p class="text-3xl font-bold text-slate-100 leading-none">
                {{ scraper?.index?.toLocaleString() ?? '—' }}
                <span v-if="scraper?.total_tickers" class="text-sm text-slate-500 font-normal ml-1">/{{ scraper.total_tickers }}</span>
              </p>
            </div>
            <div class="glass-card rounded-xl p-4 border border-border-subtle">
              <p class="text-[10px] text-slate-600 uppercase tracking-widest font-medium mb-2">SEC Edges</p>
              <p class="text-3xl font-bold text-slate-100 leading-none">{{ scraper?.edges_count?.toLocaleString() ?? '—' }}</p>
            </div>
            <div class="glass-card rounded-xl p-4 border border-border-subtle">
              <p class="text-[10px] text-slate-600 uppercase tracking-widest font-medium mb-2">Last Updated</p>
              <p class="text-sm font-semibold text-slate-200 leading-tight mt-1">{{ fmtDateTime(graphBuild?.last_updated) }}</p>
            </div>
          </div>

          <!-- Stage summary grid -->
          <div class="glass-card rounded-2xl p-5 border border-border-subtle">
            <div class="flex items-end justify-between gap-4 flex-wrap mb-4">
              <div>
                <p class="text-xs font-semibold text-slate-400 uppercase tracking-widest">Stage Summary</p>
                <p class="text-[11px] text-slate-600 mt-1">Per-stage runtime from the last completed build.</p>
              </div>
              <div class="text-right">
                <p class="text-[10px] text-slate-600 uppercase tracking-widest font-medium">Total Runtime</p>
                <p class="text-3xl font-bold text-slate-100 leading-none">{{ fmtDuration(totalBuildRuntimeSec) || '—' }}</p>
              </div>
            </div>
            <div class="grid grid-cols-1 sm:grid-cols-2 xl:grid-cols-3 gap-2">
              <div
                v-for="stage in stages"
                :key="stage.stage_index"
                class="flex items-center gap-2.5 px-3 py-2.5 rounded-xl border"
                :class="stage.status === 'completed' || stage.status === 'skipped'
                  ? 'bg-emerald-500/5 border-emerald-500/15'
                  : stage.status === 'failed'
                    ? 'bg-red-500/5 border-red-500/15'
                    : stage.status === 'stopped'
                      ? 'bg-amber-500/5 border-amber-500/15'
                    : 'bg-slate-800/50 border-slate-700/60'"
              >
                <span
                  class="material-symbols-outlined text-[16px] shrink-0"
                  :class="stageMeta(stage.status).color"
                >{{ stageMeta(stage.status).icon || 'radio_button_unchecked' }}</span>
                <div class="min-w-0 flex-1">
                  <p class="text-xs font-medium text-slate-300 truncate">{{ stage.label }}</p>
                  <p v-if="fmtDuration(stage.duration_sec)" class="text-[10px] text-slate-600 mt-0.5">{{ fmtDuration(stage.duration_sec) }}</p>
                </div>
              </div>
            </div>
          </div>

        </template>

        <!-- ─────────────── BUILDING / IDLE state ──────────────────────── -->
        <template v-else>

          <!-- Overall progress card -->
          <div class="glass-card rounded-2xl p-5 mb-6 border" :class="nexusRunning ? 'border-sky-500/20' : 'border-border-subtle'">
            <div class="flex items-start justify-between mb-4 gap-4">
              <div class="min-w-0 flex-1">
                <p class="text-sm font-semibold text-slate-200">
                  {{ graphBuild?.current_phase_label || (nexusRunning ? 'Building graph…' : 'Graph not yet built') }}
                </p>
                <p v-if="graphBuild?.message" class="text-xs text-slate-500 mt-1 max-w-2xl leading-snug">{{ graphBuild.message }}</p>
              </div>
              <div class="text-right shrink-0">
                <p class="text-3xl font-bold text-slate-100 leading-none">
                  {{ Math.round(overallPct) }}<span class="text-lg font-normal text-slate-500">%</span>
                </p>
                <p v-if="etaFormatted" class="text-[11px] text-slate-500 mt-1">~{{ etaFormatted }} remaining</p>
              </div>
            </div>
            <!-- Progress bar -->
            <div class="h-2 bg-slate-800 rounded-full overflow-hidden">
              <div
                class="h-full rounded-full transition-all duration-700"
                :class="nexusRunning ? 'bg-gradient-to-r from-sky-500 to-primary' : 'bg-slate-600'"
                :style="{ width: overallPct + '%' }"
              ></div>
            </div>
          </div>

          <!-- SEC Scraper card (when data available) -->
          <div v-if="scraper" class="glass-card rounded-2xl p-5 mb-6 border border-amber-500/15">
            <div class="flex items-center justify-between mb-3 gap-3">
              <div class="flex items-center gap-2">
                <span class="material-symbols-outlined text-amber-400 text-[18px]">description</span>
                <p class="text-sm font-semibold text-slate-200">SEC EDGAR Scraper</p>
              </div>
              <span
                class="text-[10px] font-bold px-2 py-0.5 rounded-full border uppercase tracking-wide"
                :class="scraper.status === 'running'
                  ? 'text-sky-400 bg-sky-500/10 border-sky-500/20'
                  : scraper.status === 'completed'
                    ? 'text-emerald-400 bg-emerald-500/10 border-emerald-500/20'
                    : 'text-slate-500 bg-slate-500/10 border-slate-700'"
              >{{ scraper.status || 'idle' }}</span>
            </div>
            <div class="flex items-center gap-4 text-xs text-slate-500 mb-3 flex-wrap">
              <span v-if="scraper.index != null">
                Ticker <span class="text-slate-300 font-mono font-semibold">{{ scraper.index?.toLocaleString() }}</span>
                <span v-if="scraper.total_tickers" class="text-slate-600"> / {{ scraper.total_tickers }}</span>
              </span>
              <span v-if="scraper.edges_count != null">
                Edges <span class="text-slate-300 font-semibold">{{ scraper.edges_count?.toLocaleString() }}</span>
              </span>
              <span v-if="scraper.eta_formatted" class="text-amber-500/70">~{{ scraper.eta_formatted }} remaining</span>
            </div>
            <div class="h-1.5 bg-slate-800 rounded-full overflow-hidden">
              <div
                class="h-full rounded-full bg-amber-500/60 transition-all duration-700"
                :style="{ width: (scraper.progress_pct || 0) + '%' }"
              ></div>
            </div>
          </div>

          <!-- Vertical stepper -->
          <div class="glass-card rounded-2xl p-6 border border-border-subtle">
            <p class="text-xs font-semibold text-slate-400 uppercase tracking-widest mb-6">Build Stages</p>

            <div v-if="!stages.length" class="flex flex-col items-center justify-center py-10 text-center">
              <span class="material-symbols-outlined text-slate-700 text-4xl mb-3">hub</span>
              <p class="text-slate-600 text-sm">No stage data yet.</p>
              <p class="text-slate-700 text-xs mt-1">Start the Nexus engine to begin building.</p>
            </div>

            <div v-else class="flex flex-col">
              <div v-for="(stage, si) in stages" :key="stage.stage_index" class="flex gap-4">

                <!-- Icon + connector -->
                <div class="flex flex-col items-center">
                  <div
                    class="size-8 rounded-full border flex items-center justify-center shrink-0 z-10"
                    :class="stageMeta(stage.status).bg"
                  >
                    <span
                      v-if="stageMeta(stage.status).spin"
                      class="material-symbols-outlined text-[15px] animate-spin text-sky-400"
                    >progress_activity</span>
                    <span
                      v-else
                      class="material-symbols-outlined text-[15px]"
                      :class="stageMeta(stage.status).color"
                    >{{ stageMeta(stage.status).icon }}</span>
                  </div>
                  <div
                    v-if="si < stages.length - 1"
                    class="w-px flex-1 mt-1 mb-1 min-h-[20px]"
                    :class="stageLineColor(stage)"
                  ></div>
                </div>

                <!-- Stage content -->
                <div class="pb-5 flex-1 min-w-0">
                  <div class="flex items-start justify-between gap-3">
                    <div class="min-w-0 flex-1">
                      <p
                        class="text-sm font-semibold leading-tight"
                        :class="stage.status === 'running'
                          ? 'text-sky-300'
                          : stage.status === 'stopped'
                            ? 'text-amber-300'
                          : stage.status === 'completed' || stage.status === 'skipped'
                            ? 'text-slate-200'
                            : stage.status === 'failed'
                              ? 'text-red-300'
                              : 'text-slate-600'"
                      >{{ stage.label }}</p>

                      <p v-if="stage.message" class="text-[11px] text-slate-500 mt-1 leading-snug">{{ stage.message }}</p>

                      <!-- Substep progress (only while running and multi-step) -->
                      <div v-if="stage.status === 'running' && stage.total_substeps > 1" class="mt-2 flex items-center gap-2">
                        <div class="h-1 bg-slate-700 rounded-full overflow-hidden w-24">
                          <div
                            class="h-full bg-sky-500/70 rounded-full transition-all duration-500"
                            :style="{ width: substepPct(stage) + '%' }"
                          ></div>
                        </div>
                        <span class="text-[10px] text-sky-500/80 font-mono">{{ stage.substeps_completed ?? 0 }}/{{ stage.total_substeps }} substeps</span>
                      </div>
                    </div>

                    <div class="shrink-0 text-right">
                      <span v-if="fmtDuration(stage.duration_sec)" class="text-[11px] text-slate-600 font-mono">{{ fmtDuration(stage.duration_sec) }}</span>
                      <span v-else-if="stage.status === 'running'" class="text-[11px] text-sky-500 italic">Running…</span>
                      <span v-else-if="stage.status === 'stopped'" class="text-[11px] text-amber-500 italic">Stopped</span>
                      <span v-else-if="stage.status === 'pending'" class="text-[10px] text-slate-700">pending</span>
                    </div>
                  </div>
                </div>

              </div>
            </div>
          </div>

        </template>

        <!-- Live build log terminal (always present when /nexus status loaded) -->
        <NexusLiveLogs />

      </template>

      <!-- Cannot load -->
      <div v-else class="flex flex-col items-center justify-center py-24 text-center">
        <div class="size-16 rounded-2xl bg-primary/10 border border-primary/20 flex items-center justify-center mb-4">
          <span class="material-symbols-outlined text-primary text-3xl">hub</span>
        </div>
        <p class="text-slate-400 font-medium">Unable to load Nexus status</p>
        <p class="text-slate-600 text-sm mt-1">Check that the backend is reachable.</p>
        <button
          @click="fetchStatus"
          class="mt-4 px-4 py-2 rounded-lg border border-border-subtle text-sm text-slate-400 hover:text-slate-200 transition-colors"
        >Retry</button>
      </div>

      <Teleport to="body">
        <Transition name="modal-fade">
          <div
            v-if="showDeleteModal"
            class="fixed inset-0 z-[118] bg-black/70 backdrop-blur-sm flex items-center justify-center p-4"
            @click.self="closeDeleteModal"
          >
            <div class="w-full max-w-2xl max-h-[calc(100dvh-2rem)] flex flex-col bg-[#0d1117] border border-amber-500/20 rounded-2xl shadow-2xl overflow-hidden">
              <div class="shrink-0 flex items-center gap-3 px-6 py-4 border-b border-amber-500/20">
                <div class="size-10 rounded-xl bg-amber-500/10 border border-amber-500/20 flex items-center justify-center shrink-0">
                  <span
                    class="material-symbols-outlined text-amber-300"
                    :class="deleteOperation.active || deleteRequestPending ? 'animate-spin' : ''"
                  >{{ deleteOperation.active || deleteRequestPending ? 'progress_activity' : 'delete_sweep' }}</span>
                </div>
                <div class="min-w-0 flex-1">
                  <p class="text-lg font-semibold text-slate-100">
                    {{ showDeleteProgressState ? deleteProgressTitle : 'Delete Nexus Edges' }}
                  </p>
                  <p class="text-sm text-slate-400 mt-1">
                    {{ showDeleteProgressState
                      ? deleteProgressMessage
                      : 'Select the phases whose graph output should be deleted from Neo4j.' }}
                  </p>
                </div>
                <button
                  v-if="!deleteOperation.active && !deleteRequestPending"
                  @click="closeDeleteModal"
                  class="shrink-0 text-slate-600 hover:text-slate-400 transition-colors"
                >
                  <span class="material-symbols-outlined text-[18px]">close</span>
                </button>
              </div>

              <div v-if="!showDeleteProgressState" class="min-h-0 flex-1 overflow-y-auto px-6 py-5 space-y-4">
                <div class="rounded-xl border border-border-subtle bg-slate-950/50 p-4 space-y-3">
                  <div class="flex items-center justify-between gap-3">
                    <div>
                      <p class="text-[11px] font-semibold text-slate-500 uppercase tracking-widest">Phase Selection</p>
                      <p class="text-[11px] text-slate-500 mt-1">Only the checked phases will be deleted.</p>
                    </div>
                    <div class="flex items-center gap-2">
                      <button
                        type="button"
                        @click="selectAllDeletePhases"
                        class="px-2.5 py-1.5 rounded-lg text-[11px] font-medium text-slate-200 border border-border-subtle hover:border-amber-500/40 hover:text-amber-200 transition-colors"
                      >Select all</button>
                      <button
                        type="button"
                        @click="clearDeletePhases"
                        class="px-2.5 py-1.5 rounded-lg text-[11px] font-medium text-slate-400 border border-border-subtle hover:border-slate-500/40 hover:text-slate-200 transition-colors"
                      >Deselect all</button>
                    </div>
                  </div>
                  <div class="max-h-72 overflow-y-auto space-y-2 pr-1">
                    <label
                      v-for="option in deletePhaseOptions"
                      :key="`delete-phase-${option.value}`"
                      class="flex items-center gap-3 rounded-lg border border-border-subtle/80 bg-[#0d1117] px-3 py-2 hover:border-amber-500/30 transition-colors"
                    >
                      <input
                        v-model="deleteSelectedPhases"
                        :value="Number(option.value)"
                        type="checkbox"
                        class="size-4 accent-amber-500"
                      />
                      <span class="text-sm text-slate-200">{{ option.label }}</span>
                    </label>
                  </div>
                  <p v-if="deletePhaseSelectionInvalid" class="text-[11px] text-red-400">
                    Select at least one phase to delete.
                  </p>
                  <p v-if="deleteActionError" class="text-[11px] text-red-400">
                    {{ deleteActionError }}
                  </p>
                </div>
              </div>

              <div v-else class="min-h-0 flex-1 overflow-y-auto px-6 py-5 space-y-4">
                <div class="rounded-xl border border-border-subtle bg-slate-950/60 px-4 py-3">
                  <p class="text-[10px] font-semibold text-slate-500 uppercase tracking-widest">Overall Progress</p>
                  <p class="text-2xl font-bold text-slate-100 mt-1 leading-none">
                    {{ Number(deleteOperation.current || 0).toLocaleString() }}
                    <span class="text-sm font-normal text-slate-500">
                      / {{ Number(deleteOperation.total || 0).toLocaleString() }} {{ deleteOperation.unit || 'phases' }}
                    </span>
                  </p>
                  <p v-if="deleteOperation.step" class="text-xs text-slate-400 mt-2">{{ deleteOperation.step }}</p>
                  <p v-if="deleteOperation.error" class="text-xs text-red-400 mt-2">{{ deleteOperation.error }}</p>
                </div>

                <div class="space-y-3">
                  <div
                    v-for="row in deleteOperation.phaseRows"
                    :key="`delete-progress-${row.phase_value}`"
                    class="rounded-xl border border-border-subtle bg-slate-950/60 px-4 py-3"
                  >
                    <div class="flex items-start justify-between gap-3">
                      <div class="min-w-0 flex-1">
                        <p class="text-sm font-semibold text-slate-200">{{ row.label }}</p>
                        <p class="text-xs text-slate-500 mt-1">{{ row.message || 'Queued' }}</p>
                      </div>
                      <span
                        class="text-[10px] font-bold px-2 py-0.5 rounded-full border uppercase tracking-wide"
                        :class="row.status === 'failed'
                          ? 'text-red-400 bg-red-500/10 border-red-500/20'
                          : row.status === 'completed'
                            ? 'text-emerald-400 bg-emerald-500/10 border-emerald-500/20'
                            : row.status === 'running'
                              ? 'text-amber-300 bg-amber-500/10 border-amber-500/20'
                              : 'text-slate-400 bg-slate-500/10 border-slate-700'"
                      >{{ row.status }}</span>
                    </div>
                    <div class="mt-3">
                      <div class="h-2 bg-slate-800 rounded-full overflow-hidden">
                        <div
                          class="h-full rounded-full transition-all duration-500"
                          :class="row.status === 'failed'
                            ? 'bg-red-500'
                            : row.status === 'completed'
                              ? 'bg-emerald-500'
                              : row.status === 'running'
                                ? 'bg-amber-500'
                                : 'bg-slate-600'"
                          :style="{ width: `${Math.max(0, Math.min(100, Number(row.progress_pct || 0)))}%` }"
                        ></div>
                      </div>
                      <div class="flex items-center justify-between gap-3 mt-2 text-[11px] text-slate-500">
                        <span>
                          {{ Number(row.current || 0).toLocaleString() }}
                          / {{ Number(row.total || 0).toLocaleString() }} {{ row.unit || 'records' }}
                        </span>
                        <span>{{ Number(row.deleted_count || 0).toLocaleString() }} deleted</span>
                      </div>
                    </div>
                  </div>
                </div>
              </div>

              <div class="shrink-0 flex items-center justify-end gap-3 px-6 py-4 border-t border-amber-500/20">
                <button
                  v-if="!showDeleteProgressState"
                  type="button"
                  @click="closeDeleteModal"
                  class="px-4 py-2 rounded-lg border border-border-subtle text-sm text-slate-400 hover:text-slate-200 transition-colors"
                >Cancel</button>
                <button
                  v-if="!showDeleteProgressState"
                  type="button"
                  @click="deleteEdges"
                  :disabled="deletePhaseSelectionInvalid || deleteRequestPending"
                  class="inline-flex items-center gap-1.5 px-4 py-2 rounded-lg text-sm font-semibold border border-amber-500/30 bg-amber-500/10 text-amber-300 hover:bg-amber-500/20 transition-colors disabled:opacity-50"
                >
                  <span class="material-symbols-outlined text-[16px]">delete</span>
                  Delete selected edges
                </button>
                <button
                  v-else-if="!deleteOperation.active && !deleteRequestPending"
                  type="button"
                  @click="closeDeleteModal"
                  class="px-4 py-2 rounded-lg border border-border-subtle text-sm text-slate-300 hover:text-slate-100 transition-colors"
                >Close</button>
              </div>
            </div>
          </div>
        </Transition>
      </Teleport>

      <Teleport to="body">
        <Transition name="modal-fade">
          <div
            v-if="showRebuildProgressModal"
            class="fixed inset-0 z-[120] bg-black/70 backdrop-blur-sm flex items-center justify-center p-4"
          >
            <div class="w-full max-w-lg bg-[#0d1117] border border-sky-500/20 rounded-2xl shadow-2xl overflow-hidden">
              <div class="flex items-center gap-3 px-6 py-4 border-b border-sky-500/20">
                <div class="size-10 rounded-xl bg-sky-500/10 border border-sky-500/20 flex items-center justify-center shrink-0">
                  <span class="material-symbols-outlined text-sky-400 animate-spin">progress_activity</span>
                </div>
                <div class="min-w-0 flex-1">
                  <p class="text-lg font-semibold text-slate-100">{{ rebuildProgressTitle }}</p>
                  <p class="text-sm text-slate-400 mt-1">{{ rebuildProgressMessage }}</p>
                </div>
              </div>
              <div class="px-6 py-5 space-y-4">
                <div v-if="rebuildOperation.step" class="rounded-xl border border-border-subtle bg-slate-950/60 px-4 py-3">
                  <p class="text-[10px] font-semibold text-slate-500 uppercase tracking-widest">Current Step</p>
                  <p class="text-sm font-medium text-slate-200 mt-1">{{ rebuildOperation.step }}</p>
                </div>
                <div v-if="rebuildProgressCount" class="rounded-xl border border-border-subtle bg-slate-950/60 px-4 py-3">
                  <p class="text-[10px] font-semibold text-slate-500 uppercase tracking-widest">Progress</p>
                  <p class="text-2xl font-bold text-slate-100 mt-1 leading-none">{{ rebuildProgressCount }}</p>
                </div>
                <p class="text-xs text-slate-500">
                  Keep this page open while the rebuild request completes. Nexus will restart automatically after the delete step finishes.
                </p>
              </div>
            </div>
          </div>
        </Transition>
      </Teleport>

      <!-- Auto-update configuration modal -->
      <Teleport to="body">
        <Transition name="modal-fade">
          <div
            v-if="showStartModal"
            class="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/60 backdrop-blur-sm"
            @click.self="closeStartModal"
          >
            <div class="w-full max-w-md max-h-[calc(100dvh-2rem)] flex flex-col bg-[#0d1117] border border-emerald-500/20 rounded-2xl shadow-2xl overflow-hidden">
              <div class="shrink-0 flex items-center gap-3 px-6 py-4 border-b border-emerald-500/20">
                <div class="size-9 rounded-xl bg-emerald-500/10 border border-emerald-500/20 flex items-center justify-center shrink-0">
                  <span class="material-symbols-outlined text-emerald-300 text-base">play_arrow</span>
                </div>
                <div>
                  <p class="text-sm font-semibold text-slate-100">{{ manualStartTitle }}</p>
                  <p class="text-[11px] text-slate-500 mt-0.5">Choose the phases to run for this manual execution.</p>
                </div>
              </div>
              <div class="min-h-0 flex-1 overflow-y-auto px-6 py-5 space-y-4">
                <div class="rounded-xl border border-border-subtle bg-slate-950/50 p-4 space-y-3">
                  <div class="flex items-center justify-between gap-3">
                    <div>
                      <p class="text-[11px] font-semibold text-slate-500 uppercase tracking-widest">Phase Selection</p>
                      <p class="text-[11px] text-slate-500 mt-1">Only the checked phases will run for this manual start.</p>
                    </div>
                    <div class="flex items-center gap-2">
                      <button
                        type="button"
                        @click="selectAllSelectedPhases"
                        class="px-2.5 py-1.5 rounded-lg text-[11px] font-medium text-slate-200 border border-border-subtle hover:border-emerald-500/40 hover:text-emerald-200 transition-colors"
                      >Select all</button>
                      <button
                        type="button"
                        @click="clearSelectedPhases"
                        class="px-2.5 py-1.5 rounded-lg text-[11px] font-medium text-slate-400 border border-border-subtle hover:border-slate-500/40 hover:text-slate-200 transition-colors"
                      >Deselect all</button>
                    </div>
                  </div>
                  <div class="max-h-64 overflow-y-auto space-y-2 pr-1">
                    <label
                      v-for="option in nexusPhaseOptions"
                      :key="`manual-phase-${option.value}`"
                      class="flex items-center gap-3 rounded-lg border border-border-subtle/80 bg-[#0d1117] px-3 py-2 hover:border-emerald-500/30 transition-colors"
                    >
                      <input
                        v-model="selectedPhases"
                        :value="Number(option.value)"
                        type="checkbox"
                        class="size-4 accent-emerald-500"
                      />
                      <span class="text-sm text-slate-200">{{ option.label }}</span>
                    </label>
                  </div>
                  <p v-if="manualPhaseSelectionInvalid" class="text-[11px] text-red-400">
                    Select at least one phase to start Nexus.
                  </p>
                </div>
                <div>
                  <label class="block text-[11px] font-semibold text-slate-500 uppercase tracking-widest mb-1.5">13F History (quarters)</label>
                  <input
                    v-model.number="phase7HistoryQuarters"
                    type="number"
                    min="1"
                    max="120"
                    step="1"
                    :disabled="historicalModeEnabled"
                    class="w-full px-3 py-2 rounded-lg bg-surface border border-border-subtle text-sm text-slate-200 focus:outline-none focus:border-emerald-500/50 transition-colors"
                  />
                  <p class="text-[11px] text-slate-500 mt-1">
                    <span v-if="historicalModeEnabled">
                      Derived automatically from the historical start date while historical bootstrap mode is enabled.
                    </span>
                    <span v-else>
                      Larger values load more quarterly 13F history for backtests and make Phase 7 slower.
                    </span>
                  </p>
                </div>
                <div class="rounded-xl border border-emerald-500/15 bg-emerald-500/5 p-4 space-y-3">
                  <label class="flex items-center justify-between gap-3">
                    <div>
                      <p class="text-sm font-medium text-slate-200">Historical bootstrap</p>
                      <p class="text-[11px] text-slate-500 mt-0.5">Backfill supported temporal phases from a chosen start date through today, then switch future reruns to incremental fetches only.</p>
                    </div>
                    <input v-model="historicalModeEnabled" type="checkbox" class="size-4 accent-emerald-500" />
                  </label>
                  <div>
                    <label class="block text-[11px] font-semibold text-slate-500 uppercase tracking-widest mb-1.5">Historical start date</label>
                    <input
                      v-model="historicalStartDate"
                      type="date"
                      :disabled="!historicalModeEnabled"
                      class="w-full px-3 py-2 rounded-lg bg-surface border border-border-subtle text-sm text-slate-200 focus:outline-none focus:border-emerald-500/50 transition-colors disabled:opacity-50"
                    />
                    <p class="text-[11px] text-slate-500 mt-1">
                      Phase 3, Phase 7, and Phase 11 will use this date to build historical intervals and keep future reruns incremental.
                    </p>
                    <p v-if="historicalConfigInvalid" class="text-[11px] text-red-400 mt-1">
                      Historical bootstrap needs a valid start date.
                    </p>
                  </div>
                  <label class="flex items-start justify-between gap-3 rounded-lg border border-emerald-500/10 bg-emerald-500/5 px-3 py-2">
                    <div>
                      <p class="text-sm font-medium text-slate-200">Force bootstrap rebuild</p>
                      <p class="text-[11px] text-slate-500 mt-0.5">
                        Reset historical bootstrap coverage and rebuild the temporal backfill from the configured start date for this manual run.
                      </p>
                    </div>
                    <input
                      v-model="forceBootstrapRebuild"
                      type="checkbox"
                      :disabled="!bootstrapRebuildAvailable && !historicalModeEnabled"
                      class="mt-0.5 size-4 accent-emerald-500 disabled:opacity-50"
                    />
                  </label>
                  <p v-if="!bootstrapRebuildAvailable && !historicalModeEnabled" class="text-[11px] text-slate-600">
                    Force bootstrap rebuild becomes available after you configure a historical start date.
                  </p>
                </div>
                <p class="text-[11px] text-slate-600">
                  Phase selection only affects this manual run. Auto-update scheduling still uses its own configured start and end phases.
                </p>
              </div>
              <div class="shrink-0 flex items-center justify-end gap-2 px-6 py-4 border-t border-border-subtle">
                <button
                  @click="closeStartModal"
                  class="px-4 py-2 rounded-lg text-sm font-medium text-slate-400 hover:text-slate-200 hover:bg-surface border border-transparent hover:border-border-subtle transition-colors"
                >Cancel</button>
                <button
                  @click="startNexus"
                  :disabled="svcBusy || historicalConfigInvalid || manualPhaseSelectionInvalid"
                  class="px-4 py-2 rounded-lg text-sm font-semibold text-white bg-emerald-600 hover:bg-emerald-500 transition-colors disabled:opacity-50"
                >
                  <span class="material-symbols-outlined text-[14px] align-middle mr-1">play_arrow</span>
                  {{ manualStartTitle }}
                </button>
              </div>
            </div>
          </div>
        </Transition>
      </Teleport>

      <!-- Auto-update configuration modal -->
      <Teleport to="body">
        <Transition name="modal-fade">
          <div
            v-if="showAutoUpdateModal"
            class="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/60 backdrop-blur-sm"
            @click.self="showAutoUpdateModal = false"
          >
            <div class="w-full max-w-md bg-[#0d1117] border border-sky-500/20 rounded-2xl shadow-2xl overflow-hidden">
              <div class="flex items-center gap-3 px-6 py-4 border-b border-sky-500/20">
                <div class="size-9 rounded-xl bg-sky-500/10 border border-sky-500/20 flex items-center justify-center shrink-0">
                  <span class="material-symbols-outlined text-sky-300 text-base">schedule</span>
                </div>
                <div>
                  <p class="text-sm font-semibold text-slate-100">Nexus Auto-update</p>
                  <p class="text-[11px] text-slate-500 mt-0.5">Keep Nexus online and rerun a chosen phase range on a schedule.</p>
                </div>
              </div>
              <div class="px-6 py-5 space-y-4">
                <label class="flex items-center justify-between gap-3">
                  <div>
                    <p class="text-sm font-medium text-slate-200">Enable auto-update</p>
                    <p class="text-[11px] text-slate-500 mt-0.5">Nexus will stay running and queue the next refresh automatically.</p>
                  </div>
                  <input v-model="autoUpdateEnabled" type="checkbox" class="size-4 accent-sky-500" />
                </label>
                <div class="grid grid-cols-1 sm:grid-cols-3 gap-3">
                  <div class="sm:col-span-1">
                    <label class="block text-[11px] font-semibold text-slate-500 uppercase tracking-widest mb-1.5">Interval (hours)</label>
                    <input
                      v-model="autoUpdateIntervalHours"
                      type="number"
                      min="1"
                      step="1"
                      class="w-full px-3 py-2 rounded-lg bg-surface border border-border-subtle text-sm text-slate-200 focus:outline-none focus:border-sky-500/50 transition-colors"
                    />
                  </div>
                  <div>
                    <label class="block text-[11px] font-semibold text-slate-500 uppercase tracking-widest mb-1.5">From phase</label>
                    <select v-model="autoUpdateStartPhase" class="w-full bg-[#0d1117] border border-border-subtle rounded-lg px-3 py-2 text-sm text-slate-300 focus:outline-none focus:border-sky-500/50">
                      <option v-for="option in nexusPhaseOptions" :key="`auto-start-${option.value}`" :value="option.value">{{ option.label }}</option>
                    </select>
                  </div>
                  <div>
                    <label class="block text-[11px] font-semibold text-slate-500 uppercase tracking-widest mb-1.5">To phase</label>
                    <select v-model="autoUpdateEndPhase" class="w-full bg-[#0d1117] border border-border-subtle rounded-lg px-3 py-2 text-sm text-slate-300 focus:outline-none focus:border-sky-500/50">
                      <option v-for="option in nexusPhaseOptions" :key="`auto-end-${option.value}`" :value="option.value">{{ option.label }}</option>
                    </select>
                  </div>
                </div>
                <div>
                  <label class="block text-[11px] font-semibold text-slate-500 uppercase tracking-widest mb-1.5">13F History (quarters)</label>
                  <input
                    v-model.number="phase7HistoryQuarters"
                    type="number"
                    min="1"
                    max="120"
                    step="1"
                    :disabled="historicalModeEnabled"
                    class="w-full px-3 py-2 rounded-lg bg-surface border border-border-subtle text-sm text-slate-200 focus:outline-none focus:border-sky-500/50 transition-colors"
                  />
                  <p class="text-[11px] text-slate-500 mt-1">
                    <span v-if="historicalModeEnabled">
                      Derived automatically from the historical start date while historical bootstrap mode is enabled.
                    </span>
                    <span v-else>
                      Higher values load more historical 13F intervals for backtests, but Phase 7 takes longer and writes more HOLDS edges.
                    </span>
                  </p>
                </div>
                <div class="rounded-xl border border-sky-500/15 bg-sky-500/5 p-4">
                  <p class="text-sm font-medium text-slate-200">Historical bootstrap coverage</p>
                  <p class="text-[11px] text-slate-500 mt-1">
                    Scheduled auto-updates reuse existing bootstrap coverage and fetch only newer source windows. Change bootstrap settings from the start/rerun modal or the bootstrap card, not here.
                  </p>
                  <p class="text-[11px] text-slate-600 mt-2">
                    Current coverage: {{ historicalCoverageSummary }}
                  </p>
                </div>
              </div>
              <div class="flex items-center justify-end gap-2 px-6 py-4 border-t border-border-subtle">
                <button
                  @click="showAutoUpdateModal = false"
                  class="px-4 py-2 rounded-lg text-sm font-medium text-slate-400 hover:text-slate-200 hover:bg-surface border border-transparent hover:border-border-subtle transition-colors"
                >Cancel</button>
                <button
                  @click="saveAutoUpdate"
                  :disabled="historicalConfigInvalid"
                  class="px-4 py-2 rounded-lg text-sm font-semibold text-white bg-sky-600 hover:bg-sky-500 transition-colors"
                >
                  <span class="material-symbols-outlined text-[14px] align-middle mr-1">save</span>
                  Save Schedule
                </button>
              </div>
            </div>
          </div>
        </Transition>
      </Teleport>

      <!-- Rebuild confirmation modal -->
      <Teleport to="body">
        <Transition name="modal-fade">
          <div
            v-if="showRebuildConfirm"
            class="fixed inset-0 z-50 flex items-start justify-center overflow-y-auto p-4 sm:items-center bg-black/60 backdrop-blur-sm"
            @click.self="closeRebuildModal"
          >
            <div class="flex w-full max-w-2xl max-h-[calc(100dvh-2rem)] flex-col overflow-hidden rounded-2xl border border-red-500/30 bg-[#0d1117] shadow-2xl">
              <div class="shrink-0 flex items-center gap-3 px-6 py-4 border-b border-red-500/20">
                <div class="size-9 rounded-xl bg-red-500/10 border border-red-500/20 flex items-center justify-center shrink-0">
                  <span class="material-symbols-outlined text-red-400 text-base">reset_wrench</span>
                </div>
                <div>
                  <p class="text-sm font-semibold text-slate-100">Full Rebuild</p>
                  <p class="text-[11px] text-red-400/80 mt-0.5">
                    {{ rebuildIsDestructive
                      ? 'Destructive mode clears Neo4j data and Nexus progress before restarting from phase 1.'
                      : 'In-place mode reruns from phase 1, optionally clears selected cache entries, and keeps the existing graph online.' }}
                  </p>
                </div>
              </div>
              <div class="min-h-0 flex-1 overflow-y-auto px-6 py-5 space-y-4">
                <div class="rounded-xl border border-slate-800 bg-slate-950/60 p-4">
                  <p class="text-sm font-medium text-slate-200">Rebuild mode</p>
                  <div class="mt-3 grid grid-cols-1 sm:grid-cols-2 gap-3">
                    <label
                      class="rounded-xl border px-4 py-3 cursor-pointer transition-colors"
                      :class="rebuildMode === 'non_destructive'
                        ? 'border-emerald-500/30 bg-emerald-500/10'
                        : 'border-slate-800 bg-slate-900/70 hover:border-emerald-500/20'"
                    >
                      <div class="flex items-start gap-3">
                        <input v-model="rebuildMode" value="non_destructive" type="radio" class="mt-0.5 accent-emerald-500" />
                        <div>
                          <p class="text-sm font-medium text-slate-100">In-place rebuild</p>
                          <p class="text-[11px] text-slate-500 mt-1">Recommended. Keeps current graph data online while Nexus reruns and swaps in refreshed intervals.</p>
                        </div>
                      </div>
                    </label>
                    <label
                      class="rounded-xl border px-4 py-3 cursor-pointer transition-colors"
                      :class="rebuildMode === 'destructive'
                        ? 'border-red-500/30 bg-red-500/10'
                        : 'border-slate-800 bg-slate-900/70 hover:border-red-500/20'"
                    >
                      <div class="flex items-start gap-3">
                        <input v-model="rebuildMode" value="destructive" type="radio" class="mt-0.5 accent-red-500" />
                        <div>
                          <p class="text-sm font-medium text-slate-100">Destructive rebuild</p>
                          <p class="text-[11px] text-slate-500 mt-1">Clears Neo4j graph data and resets Nexus progress in RethinkDB before phase 1 starts again.</p>
                        </div>
                      </div>
                    </label>
                  </div>
                </div>

                <p class="text-sm leading-relaxed" :class="rebuildIsDestructive ? 'text-red-300' : 'text-slate-400'">
                  {{ rebuildIsDestructive
                    ? 'This will intentionally take Nexus graph data offline until the rebuild repopulates Neo4j. Use this only when you want the legacy wipe-and-rebuild behavior.'
                    : 'Nexus will rerun from phase 1 and preserve historical edge intervals so strategies can keep reading the current graph while the refresh is in progress.' }}
                </p>
                <div class="rounded-xl border border-slate-800 bg-slate-950/60 p-4">
                  <label class="flex items-start justify-between gap-3">
                    <div>
                      <p class="text-sm font-medium text-slate-200">Force bootstrap rebuild</p>
                      <p class="text-[11px] text-slate-500 mt-1">
                        {{ rebuildIsDestructive
                          ? 'Destructive rebuild already resets historical bootstrap checkpoints when bootstrap mode is configured.'
                          : 'Also clear historical bootstrap checkpoints so Phase 3, Phase 7, and Phase 11 rebuild historical coverage from the configured bootstrap start date.' }}
                      </p>
                    </div>
                    <input
                      v-model="rebuildForceBootstrap"
                      type="checkbox"
                      :disabled="!bootstrapRebuildAvailable"
                      class="mt-0.5 size-4 accent-red-500 disabled:opacity-50"
                    />
                  </label>
                  <p v-if="!bootstrapRebuildAvailable" class="text-[11px] text-slate-600 mt-2">
                    Historical bootstrap needs a stored start date before there is bootstrap coverage to rebuild.
                  </p>
                </div>
                <div class="rounded-xl border border-slate-800 bg-slate-950/60 p-4">
                  <div class="flex items-start justify-between gap-3 flex-wrap">
                    <div>
                      <p class="text-sm font-medium text-slate-200">Optional cache cleanup</p>
                      <p class="text-[11px] text-slate-500 mt-1">
                        Visible entries are top-level items under <span class="font-mono text-slate-300">{{ rebuildCacheRoot }}</span>.
                        Selected entries are deleted before the rebuild starts. If the Nexus container is running, it is restarted afterward.
                      </p>
                    </div>
                    <button
                      v-if="rebuildCacheAvailable && rebuildCacheEntries.length"
                      @click="toggleAllRebuildCacheSelections"
                      type="button"
                      class="px-3 py-1.5 rounded-lg text-[11px] font-semibold border border-slate-700 text-slate-300 hover:text-sky-300 hover:border-sky-500/30 hover:bg-sky-500/5 transition-colors"
                    >
                      {{ allRebuildCacheSelected ? 'Clear selection' : 'Select all' }}
                    </button>
                  </div>

                  <div v-if="rebuildCacheLoading" class="mt-4 text-sm text-slate-500">
                    Loading Nexus cache entries...
                  </div>

                  <div
                    v-else-if="rebuildCacheAvailable && rebuildCacheEntries.length"
                    class="mt-4 grid grid-cols-1 sm:grid-cols-2 gap-2 max-h-72 overflow-y-auto pr-1"
                  >
                    <label
                      v-for="entry in rebuildCacheEntries"
                      :key="entry.path"
                      class="flex items-start gap-3 rounded-xl border border-slate-800 bg-slate-900/70 px-3 py-2.5 cursor-pointer hover:border-red-500/20 hover:bg-red-500/5 transition-colors"
                    >
                      <input
                        v-model="rebuildSelectedCachePaths"
                        :value="entry.path"
                        type="checkbox"
                        class="mt-0.5 size-4 accent-red-500"
                      />
                      <span
                        class="material-symbols-outlined text-[18px] mt-0.5"
                        :class="entry.is_dir ? 'text-amber-400' : 'text-sky-400'"
                      >{{ entry.is_dir ? 'folder' : 'description' }}</span>
                      <span class="min-w-0 flex-1">
                        <span class="block text-sm font-mono text-slate-200 break-all">{{ entry.path }}</span>
                        <span class="block text-[11px] text-slate-500 mt-1">
                          {{ entry.is_dir ? 'Folder' : 'File' }}
                          <span v-if="!entry.is_dir && entry.size_bytes != null"> · {{ entry.size_bytes.toLocaleString() }} bytes</span>
                        </span>
                      </span>
                    </label>
                  </div>

                  <p v-else-if="rebuildCacheAvailable" class="mt-4 text-sm text-slate-500">
                    No cache entries were found in {{ rebuildCacheRoot }}.
                  </p>

                  <p v-else class="mt-4 text-sm text-amber-400/80">
                    Cache selection is available when the API can access the Nexus cache root directly or when the Nexus container is running.
                  </p>

                  <p v-if="rebuildCacheError" class="mt-3 text-xs text-amber-400/80">{{ rebuildCacheError }}</p>
                </div>
                <div>
                  <label class="block text-[11px] font-semibold text-slate-500 uppercase tracking-widest mb-1.5">
                    Type <span class="text-red-400 font-mono">confirm</span> to proceed
                  </label>
                  <input
                    v-model="rebuildConfirmText"
                    type="text"
                    placeholder="confirm"
                    autocomplete="off"
                    @keydown.enter="rebuildNexus"
                    class="w-full px-3 py-2 rounded-lg bg-surface border border-border-subtle text-sm text-slate-200 placeholder-slate-600 focus:outline-none focus:border-red-500/50 transition-colors font-mono"
                  />
                </div>
              </div>
              <div class="shrink-0 flex items-center justify-end gap-2 px-6 py-4 border-t border-border-subtle bg-[#0d1117]">
                <button
                  @click="closeRebuildModal"
                  class="px-4 py-2 rounded-lg text-sm font-medium text-slate-400 hover:text-slate-200 hover:bg-surface border border-transparent hover:border-border-subtle transition-colors"
                >Cancel</button>
                <button
                  @click="rebuildNexus"
                  :disabled="rebuildConfirmText.toLowerCase().trim() !== 'confirm' || rebuildCacheLoading"
                  class="px-4 py-2 rounded-lg text-sm font-semibold text-white disabled:opacity-40 disabled:cursor-not-allowed transition-colors"
                  :class="rebuildIsDestructive ? 'bg-red-600 hover:bg-red-500' : 'bg-amber-600 hover:bg-amber-500'"
                >
                  <span class="material-symbols-outlined text-[14px] align-middle mr-1">restart_alt</span>
                  {{ rebuildIsDestructive ? 'Confirm Destructive Rebuild' : 'Confirm Rebuild' }}
                </button>
              </div>
            </div>
          </div>
        </Transition>
      </Teleport>

      <Teleport to="body">
        <Transition name="modal-fade">
          <div
            v-if="showDisableBootstrapModal"
            class="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/60 backdrop-blur-sm"
            @click.self="closeDisableBootstrapModal"
          >
            <div class="w-full max-w-md rounded-2xl border border-slate-700 bg-[#0d1117] shadow-2xl overflow-hidden">
              <div class="flex items-center gap-3 px-6 py-4 border-b border-slate-700">
                <div class="size-9 rounded-xl bg-slate-500/10 border border-slate-600/30 flex items-center justify-center shrink-0">
                  <span class="material-symbols-outlined text-slate-300 text-base">toggle_off</span>
                </div>
                <div>
                  <p class="text-sm font-semibold text-slate-100">Disable Historical Bootstrap</p>
                  <p class="text-[11px] text-slate-500 mt-0.5">This clears bootstrap checkpoints and disables future historical interval maintenance.</p>
                </div>
              </div>
              <div class="px-6 py-5 space-y-3">
                <p class="text-sm text-slate-300">
                  Disable historical bootstrap for Nexus? Existing graph data stays in Neo4j, but future Nexus runs will stop maintaining historical bootstrap coverage and manifests.
                </p>
                <p class="text-[11px] text-slate-500">
                  Current bootstrap coverage: {{ historicalCoverageSummary }}
                </p>
              </div>
              <div class="flex items-center justify-end gap-2 px-6 py-4 border-t border-border-subtle">
                <button
                  @click="closeDisableBootstrapModal"
                  class="px-4 py-2 rounded-lg text-sm font-medium text-slate-400 hover:text-slate-200 hover:bg-surface border border-transparent hover:border-border-subtle transition-colors"
                >Cancel</button>
                <button
                  @click="confirmDisableBootstrap"
                  :disabled="svcBusy"
                  class="px-4 py-2 rounded-lg text-sm font-semibold text-white bg-slate-700 hover:bg-slate-600 transition-colors disabled:opacity-50"
                >
                  Disable bootstrap
                </button>
              </div>
            </div>
          </div>
        </Transition>
      </Teleport>

    <!-- ── Enable Historical Bootstrap modal ───────────────────────────── -->
    <Teleport to="body">
      <Transition name="modal-fade">
        <div
          v-if="showEnableBootstrapModal"
          class="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/60 backdrop-blur-sm"
          @click.self="closeEnableBootstrapModal"
        >
          <div class="w-full max-w-sm bg-[#0d1117] border border-border-subtle rounded-2xl shadow-2xl overflow-hidden">
            <!-- Header -->
            <div class="flex items-center gap-3 px-6 py-4 border-b border-border-subtle">
              <div class="size-9 rounded-xl bg-sky-500/10 border border-sky-500/20 flex items-center justify-center shrink-0">
                <span class="material-symbols-outlined text-sky-400 text-base">history</span>
              </div>
              <div>
                <p class="text-sm font-semibold text-slate-100">Enable Historical Bootstrap</p>
                <p class="text-[11px] text-slate-500 mt-0.5">Set the start date for temporal data coverage.</p>
              </div>
              <button @click="closeEnableBootstrapModal" class="ml-auto text-slate-600 hover:text-slate-300 transition-colors">
                <span class="material-symbols-outlined text-[18px]">close</span>
              </button>
            </div>

            <!-- Body -->
            <div class="px-6 py-5 space-y-4">
              <div>
                <label class="block text-[11px] font-semibold text-slate-500 uppercase tracking-widest mb-1.5">Historical start date</label>
                <input
                  v-model="enableBootstrapDate"
                  type="date"
                  class="w-full bg-surface border border-border-subtle rounded-lg px-3 py-2 text-sm text-slate-200 focus:outline-none focus:border-sky-500/40 transition-colors"
                  @keydown.enter="confirmEnableBootstrap"
                />
                <p class="text-[10px] text-slate-600 mt-1.5">
                  Phases 3, 7, and 11 will be backfilled from this date. Future reruns fetch only newer data.
                </p>
              </div>
            </div>

            <!-- Footer -->
            <div class="flex items-center justify-end gap-2 px-6 py-4 border-t border-border-subtle">
              <button
                @click="closeEnableBootstrapModal"
                class="px-4 py-2 rounded-lg text-sm font-medium text-slate-400 hover:text-slate-200 hover:bg-surface border border-transparent hover:border-border-subtle transition-colors"
              >Cancel</button>
              <button
                @click="confirmEnableBootstrap"
                :disabled="!enableBootstrapDate"
                class="px-4 py-2 rounded-lg text-sm font-semibold bg-primary hover:brightness-110 text-white disabled:opacity-40 disabled:cursor-not-allowed transition-colors"
              >
                <span class="material-symbols-outlined text-[14px] align-middle mr-1">toggle_on</span>
                Enable Bootstrap
              </button>
            </div>
          </div>
        </div>
      </Transition>
    </Teleport>

    </main>
  </AppShell>
</template>

<style scoped>
.modal-fade-enter-active,
.modal-fade-leave-active { transition: opacity 0.2s ease; }
.modal-fade-enter-from,
.modal-fade-leave-to    { opacity: 0; }
</style>
