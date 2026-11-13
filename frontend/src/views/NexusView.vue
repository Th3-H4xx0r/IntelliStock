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
  return `${start} → ${end}`
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
    return `Historical bootstrap is enabled from ${start} but has not run yet`
  }
  if (bootstrapEffectiveStatus.value === 'completed') {
    const start = bootstrap.value?.start_date || control.value?.historical_start_date || 'unknown'
    const end = bootstrap.value?.coverage_end || control.value?.historical_coverage_end || 'today'
    return `Historical bootstrap complete for ${start} → ${end}`
  }
  return bootstrap.value?.message || 'Historical bootstrap status unavailable'
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
  return `${start} → ${end}`
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
  if (rebuildOperation.value.destructive || rebuildIsDestructive.value) return 'Destructive rebuild in progress'
  return 'Rebuild in progress'
})
const rebuildProgressMessage = computed(() => {
  if (rebuildOperation.value.message) return rebuildOperation.value.message
  if (rebuildRequestPending.value) {
    return rebuildIsDestructive.value
      ? 'Preparing destructive rebuild and clearing Neo4j graph data…'
      : 'Preparing Nexus rebuild request…'
  }
  return 'Processing Nexus rebuild request…'
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
    ? 'Deleting Nexus edges'
    : 'Delete complete'
)
const deleteProgressMessage = computed(() => {
  if (deleteActionError.value) return deleteActionError.value
  if (deleteOperation.value.message) return deleteOperation.value.message
  if (deleteRequestPending.value) return 'Starting selected phase cleanup…'
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
// Stage status: a neutral line icon; red only for a failure. A running stage
// shows the live dot instead of an icon (spin flag kept for the template).
const STAGE_META = {
  pending:   { icon: 'radio_button_unchecked', spin: false, color: 'text-slate-600' },
  running:   { icon: null,                     spin: true,  color: 'text-slate-200' },
  stopped:   { icon: 'pause_circle',           spin: false, color: 'text-slate-400' },
  completed: { icon: 'check_circle',           spin: false, color: 'text-slate-400' },
  skipped:   { icon: 'remove_circle',          spin: false, color: 'text-slate-600' },
  failed:    { icon: 'cancel',                 spin: false, color: 'text-loss' },
}

// Bootstrap status as a dot beside the word. Green only while it runs;
// amber for a partial (paused) backfill; a light dot for done.
const BOOTSTRAP_META = {
  never_built:      { dot: 'ui-dot-off',  text: 'text-slate-400' },
  disabled:         { dot: 'ui-dot-off',  text: 'text-slate-500' },
  enabled_never_run:{ dot: 'ui-dot-off',  text: 'text-slate-300' },
  pending:          { dot: 'ui-dot-off',  text: 'text-slate-300' },
  running:          { dot: 'ui-dot-live', text: 'text-slate-200' },
  partial:          { dot: 'ui-dot-warn', text: 'text-slate-300' },
  completed:        { dot: 'bg-slate-300', text: 'text-slate-200' },
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
  if (st === 'completed')         return 'Bootstrap ready'
  if (st === 'running')           return 'Bootstrap running'
  if (st === 'never_built')       return 'Bootstrap not built'
  if (st === 'disabled')          return 'Disabled'
  if (st === 'enabled_never_run') return 'Enabled — never run'
  if (st === 'partial')           return 'Bootstrap partial'
  return 'Bootstrap pending'
}

function stageLineColor(stage) {
  if (stage.status === 'completed' || stage.status === 'skipped') return 'bg-slate-600'
  if (stage.status === 'running') return 'bg-slate-700'
  if (stage.status === 'stopped') return 'bg-slate-700'
  return 'bg-white/[0.06]'
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
  if (start && end) return `${start} → ${end}`
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
    <main class="ui-page">

      <!-- Header -->
      <header class="flex flex-wrap items-end justify-between gap-x-4 gap-y-3">
        <div class="min-w-0">
          <h1 class="ui-page-title">Nexus graph</h1>
          <p class="ui-page-sub">Knowledge graph builder — S&amp;P 500 company relationships across 10 data sources</p>
          <p class="mt-2 flex flex-wrap items-center gap-x-2 gap-y-1 text-xs text-slate-500">
            <!-- Build status -->
            <span
              class="inline-flex items-center gap-1.5"
              :class="nexusRunning || showBuilt ? 'text-slate-200' : 'text-slate-400'"
            >
              <span
                class="ui-dot"
                :class="nexusRunning ? 'ui-dot-live' : showBuilt ? 'bg-slate-300' : 'ui-dot-off'"
              ></span>
              {{ nexusRunning ? 'Building…' : showBuilt ? 'Graph ready' : 'Idle' }}
            </span>
            <template v-if="bootstrapVisible">
              <span class="text-slate-600" aria-hidden="true">·</span>
              <span
                class="inline-flex items-center gap-1.5"
                :class="bootstrapMeta(bootstrapEffectiveStatus).text"
              >
                <span class="ui-dot" :class="bootstrapMeta(bootstrapEffectiveStatus).dot"></span>
                {{ bootstrapPillText(bootstrapEffectiveStatus) }}
              </span>
            </template>
          </p>
        </div>
        <div class="flex items-center gap-2 flex-wrap">
          <!-- Start button (when not running) -->
          <button
            v-if="!nexusRunning"
            @click="openStartModal"
            :disabled="svcBusy"
            class="ui-btn-primary h-8 px-3 text-[13px]"
          >
            {{ primaryStartLabel }}
          </button>
          <!-- Stop button (when running) -->
          <button
            v-if="nexusServiceRunning"
            @click="stopNexus"
            :disabled="svcBusy"
            class="ui-btn text-loss"
          >
            Stop
          </button>
          <button
            @click="openAutoUpdateModal"
            :disabled="svcBusy"
            class="ui-btn"
          >
            Auto-update
          </button>
          <!-- Full rebuild (always available) -->
          <button
            @click="openRebuildModal"
            :disabled="svcBusy"
            class="ui-btn"
          >
            Full rebuild
          </button>
          <button
            @click="openDeleteModal"
            :disabled="svcBusy || nexusServiceRunning || rebuildOperation.active"
            class="ui-btn"
          >
            Delete edges
          </button>
        </div>
      </header>

      <!-- Rebuild result banner -->
      <Transition name="modal-fade">
        <div
          v-if="rebuildResult"
          class="mt-6 rounded-xl border px-5 py-4 flex items-start gap-3"
          :class="rebuildResult.success
            ? 'border-white/[0.07] bg-surface'
            : 'border-loss/20 bg-loss/5'"
        >
          <div class="flex-1 min-w-0 text-sm">
            <p :class="rebuildResult.success ? 'text-gain' : 'text-loss'">
              {{ rebuildResult.success ? (rebuildResult.non_destructive ? 'Rebuild initiated' : 'Destructive rebuild initiated') : 'Rebuild failed' }}
            </p>
            <p class="text-slate-400 mt-1">{{ rebuildResult.message || 'No message returned.' }}</p>
            <div v-if="!rebuildResult.non_destructive" class="flex flex-wrap gap-x-4 gap-y-1 text-xs mt-2">
              <span :class="rebuildResult.neo4j_cleared ? 'text-slate-300' : 'text-loss'">
                Neo4j {{ rebuildResult.neo4j_cleared ? 'cleared' : 'not cleared' }}
              </span>
              <span :class="rebuildResult.rethinkdb_cleared ? 'text-slate-300' : 'text-loss'">
                RethinkDB {{ rebuildResult.rethinkdb_cleared ? 'cleared' : 'not cleared' }}
              </span>
            </div>
            <p v-if="rebuildResult.deleted_cache_paths?.length" class="text-xs text-slate-500 mt-2">
              Deleted cache entries: <span class="text-slate-300">{{ rebuildResult.deleted_cache_paths.join(', ') }}</span>
            </p>
            <p v-if="rebuildResult.container_restarted" class="text-xs text-slate-500 mt-1">
              Nexus container restart: <span class="text-slate-300">{{ rebuildResult.container_restart_mode === 'server_recreate' ? 'recreated by server' : 'restarted in place' }}</span>
            </p>
            <p v-if="rebuildResult.neo4j_skip_reason" class="text-xs text-slate-400 mt-1 inline-flex items-center gap-2">
              <span class="ui-dot ui-dot-warn"></span>{{ rebuildResult.neo4j_skip_reason }}
            </p>
            <p v-if="rebuildResult.error" class="text-xs text-loss mt-1">{{ rebuildResult.error }}</p>
          </div>
          <button @click="rebuildResult = null" class="ui-icon-btn -mr-2 -mt-1 shrink-0" aria-label="Dismiss">
            <span class="material-symbols-outlined text-[18px]">close</span>
          </button>
        </div>
      </Transition>

      <!-- Loading skeleton -->
      <div v-if="loading" class="mt-8 space-y-3" aria-hidden="true">
        <div v-for="n in 5" :key="n" class="rounded-xl bg-surface" :style="{ height: n === 1 ? '80px' : '56px' }"></div>
      </div>

      <template v-else-if="status">

        <!-- Auto-update -->
        <section class="mt-8">
          <div class="mb-3 flex items-end justify-between gap-4">
            <h2 class="ui-section-title">Auto-update</h2>
            <button
              @click="openAutoUpdateModal"
              :disabled="svcBusy"
              class="ui-btn"
            >
              Configure
            </button>
          </div>
          <div class="ui-card px-5 py-4 grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-4">
            <div class="min-w-0">
              <p class="text-xs text-slate-500">Schedule</p>
              <p class="mt-1 text-sm text-slate-100">{{ autoUpdateSummary }}</p>
              <p class="text-xs text-slate-500 mt-0.5 tabular-nums">Next run {{ nextAutoUpdateAt ? fmtDateTime(nextAutoUpdateAt) : 'not scheduled' }}</p>
            </div>
            <div class="min-w-0">
              <p class="text-xs text-slate-500">Refresh range</p>
              <p class="mt-1 text-sm text-slate-100">{{ autoUpdateStartLabel }}</p>
              <p class="text-xs text-slate-500 mt-0.5">to {{ autoUpdateEndLabel }}</p>
            </div>
            <div class="min-w-0">
              <p class="text-xs text-slate-500">13F history</p>
              <p class="mt-1 text-sm text-slate-100 tabular-nums">
                {{ control.phase7_history_quarters ?? 1 }} quarter<span v-if="(control.phase7_history_quarters ?? 1) !== 1">s</span>
              </p>
            </div>
            <div class="min-w-0">
              <p class="text-xs text-slate-500">Historical bootstrap</p>
              <p class="mt-1 text-sm text-slate-100 tabular-nums">{{ historicalCoverageSummary }}</p>
            </div>
          </div>
        </section>

        <!-- ─────────────── Historical bootstrap ─────────────────────────── -->
        <section v-if="bootstrapVisible" class="mt-12">
          <div class="mb-3 flex flex-wrap items-end justify-between gap-x-4 gap-y-2">
            <div class="min-w-0">
              <h2 class="ui-section-title">Historical bootstrap</h2>
              <p class="ui-section-sub">{{ bootstrapDisplayMessage }}</p>
            </div>
            <div class="flex items-center gap-3 shrink-0">
              <span
                class="inline-flex items-center gap-1.5 text-xs"
                :class="bootstrapMeta(bootstrapEffectiveStatus).text"
              >
                <span class="ui-dot" :class="bootstrapMeta(bootstrapEffectiveStatus).dot"></span>
                {{ bootstrapPillText(bootstrapEffectiveStatus) }}
              </span>
              <!-- Enable button (shown when not enabled and not running/completed) -->
              <button
                v-if="!control?.historical_mode_enabled && bootstrapEffectiveStatus !== 'running' && bootstrapEffectiveStatus !== 'completed'"
                @click="openEnableBootstrapModal"
                class="ui-btn"
              >
                Enable
              </button>
              <!-- Disable button (shown when enabled but not running/completed) -->
              <button
                v-else-if="control?.historical_mode_enabled && bootstrapEnabledNeverRun"
                @click="openDisableBootstrapModal"
                class="ui-btn"
              >
                Disable
              </button>
              <button
                v-else-if="control?.historical_mode_enabled"
                @click="openDisableBootstrapModal"
                class="ui-btn"
              >
                Disable
              </button>
            </div>
          </div>

          <div class="ui-card overflow-hidden">
            <template v-if="bootstrapFinished">
              <div class="px-5 py-4 grid grid-cols-2 gap-4 lg:grid-cols-4">
                <div>
                  <p class="text-xs text-slate-500">Coverage</p>
                  <p class="mt-1 text-sm text-slate-100 tabular-nums">{{ bootstrapCoverageSummary }}</p>
                </div>
                <div>
                  <p class="text-xs text-slate-500">Duration</p>
                  <p class="mt-1 text-sm text-slate-100 tabular-nums">{{ fmtDuration(bootstrap.duration_sec) || '—' }}</p>
                </div>
                <div>
                  <p class="text-xs text-slate-500">Completed</p>
                  <p class="mt-1 text-sm text-slate-100 tabular-nums">{{ fmtDateTime(bootstrap.completed_at) }}</p>
                </div>
                <div>
                  <p class="text-xs text-slate-500">Temporal phases</p>
                  <p class="mt-1 text-sm text-slate-100 tabular-nums">{{ bootstrap.completed_phases }}/{{ bootstrap.total_phases }}</p>
                </div>
              </div>
            </template>

            <template v-else>
              <div class="px-5 py-4 grid grid-cols-1 gap-4 sm:grid-cols-3 border-b border-white/[0.06]">
                <div>
                  <p class="text-xs text-slate-500">Coverage target</p>
                  <p class="mt-1 text-sm text-slate-100 tabular-nums">{{ bootstrapCoverageSummary }}</p>
                </div>
                <div>
                  <p class="text-xs text-slate-500">Temporal phases</p>
                  <p class="mt-1 text-sm text-slate-100 tabular-nums">
                    <template v-if="bootstrap.total_phases != null">{{ bootstrap.completed_phases ?? 0 }}/{{ bootstrap.total_phases }} complete</template>
                    <template v-else>—</template>
                  </p>
                </div>
                <div>
                  <p class="text-xs text-slate-500">Started</p>
                  <p class="mt-1 text-sm text-slate-100 tabular-nums">{{ fmtDateTime(bootstrap.started_at) }}</p>
                </div>
              </div>

              <div class="ui-rows">
                <div
                  v-for="phase in bootstrapPhaseRows"
                  :key="phase.key"
                  class="min-h-14 px-5 py-3"
                >
                  <div class="flex items-start justify-between gap-4">
                    <div class="min-w-0 flex-1">
                      <p class="text-sm text-slate-100">{{ phase.label }}</p>
                      <!-- Live message when running; coverage dates otherwise -->
                      <p class="text-xs text-slate-500 mt-0.5 tabular-nums">
                        {{ phase.liveStage?.message || fmtCoverage(phase.coverage_start, phase.coverage_end) }}
                      </p>
                      <!-- Substep progress bar (live only) -->
                      <div v-if="phase.liveStage?.status === 'running' && phase.liveStage.total_substeps > 1" class="mt-2 flex items-center gap-3">
                        <div class="h-1 bg-raised rounded-full overflow-hidden w-28">
                          <div
                            class="h-full bg-slate-300 rounded-full progress-fill"
                            :style="{ transform: `scaleX(${substepPct(phase.liveStage) / 100})` }"
                          ></div>
                        </div>
                        <span class="text-xs text-slate-400 tabular-nums">{{ phase.liveStage.substeps_completed ?? 0 }}/{{ phase.liveStage.total_substeps }} substeps</span>
                      </div>
                      <!-- Checkpoint: last saved point (partial phases only) -->
                      <p v-if="phase.status === 'partial' && phase.last_incremental_end" class="text-xs text-slate-400 mt-1 tabular-nums">
                        Checkpoint: {{ phase.last_incremental_end }}
                      </p>
                    </div>
                    <div class="shrink-0 text-right">
                      <span
                        class="inline-flex items-center gap-1.5 text-xs capitalize"
                        :class="bootstrapMeta(phase.status).text"
                      >
                        <span class="ui-dot" :class="bootstrapMeta(phase.status).dot"></span>
                        {{ phase.status }}
                      </span>
                      <!-- Stats: snapshots / filings / edges -->
                      <div v-if="phase.processed_snapshots != null || phase.filings_processed != null || phase.edge_count != null" class="mt-1 text-xs text-slate-500 tabular-nums">
                        <p v-if="phase.processed_snapshots != null">
                          {{ phase.processed_snapshots }} snapshots
                        </p>
                        <p v-if="phase.filings_processed != null">
                          {{ phase.filings_processed.toLocaleString() }} filings
                        </p>
                        <p v-if="phase.edge_count != null">
                          {{ phase.edge_count.toLocaleString() }} edges
                        </p>
                        <p v-if="phase.latest_event_date">
                          Latest: {{ phase.latest_event_date }}
                        </p>
                      </div>
                    </div>
                  </div>
                </div>
              </div>
            </template>
          </div>
        </section>

        <!-- Graph counts -->
        <section v-if="relationshipCounts.length || Object.keys(nodeCounts).length" class="mt-12">
          <div class="mb-3">
            <h2 class="ui-section-title">Graph counts</h2>
            <p class="ui-section-sub">
              {{ nexusRunning
                ? 'Current Neo4j relationship totals while the graph build is running'
                : 'Current Neo4j relationship totals for the built graph' }}
            </p>
          </div>
          <div class="ui-card overflow-hidden">
            <div v-if="nodeCounts.companies != null || nodeCounts.edge_intervals != null" class="px-5 py-4 flex flex-wrap gap-x-12 gap-y-4 border-b border-white/[0.06]">
              <div v-if="nodeCounts.companies != null">
                <p class="text-xs text-slate-500">Companies</p>
                <p class="mt-1 text-2xl font-medium text-slate-100 tabular-nums leading-none">{{ nodeCounts.companies?.toLocaleString?.() ?? nodeCounts.companies }}</p>
              </div>
              <div v-if="nodeCounts.edge_intervals != null">
                <p class="text-xs text-slate-500">Intervals</p>
                <p class="mt-1 text-2xl font-medium text-slate-100 tabular-nums leading-none">{{ nodeCounts.edge_intervals?.toLocaleString?.() ?? nodeCounts.edge_intervals }}</p>
              </div>
            </div>
            <div v-if="relationshipCounts.length" class="overflow-x-auto">
              <table class="w-full text-sm">
                <thead>
                  <tr class="h-10 text-left text-xs text-slate-500 border-b border-white/[0.06]">
                    <th class="px-5 font-normal">Relationship</th>
                    <th class="px-5 font-normal text-right">Active</th>
                    <th class="px-5 font-normal text-right">Total</th>
                  </tr>
                </thead>
                <tbody class="divide-y divide-white/[0.05]">
                  <tr
                    v-for="rel in relationshipCounts"
                    :key="rel.key"
                    class="h-14 tabular-nums"
                  >
                    <td class="px-5">
                      <p class="text-slate-100">{{ rel.label }}</p>
                      <p class="text-xs text-slate-500 mt-0.5">{{ rel.key }}</p>
                    </td>
                    <td class="px-5 text-right text-slate-100">{{ rel.active_count?.toLocaleString?.() ?? rel.active_count }}</td>
                    <td class="px-5 text-right text-slate-400 whitespace-nowrap">
                      {{ rel.total_count !== rel.active_count ? (rel.total_count?.toLocaleString?.() ?? rel.total_count) : 'All active' }}
                    </td>
                  </tr>
                </tbody>
              </table>
            </div>
          </div>
        </section>

        <template v-if="showBuilt">

          <!-- Built: summary and stats in one card -->
          <section class="mt-12 ui-card divide-y divide-white/[0.06]">
            <div class="px-5 py-4 flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
              <div class="min-w-0">
                <p class="text-sm text-slate-100 inline-flex items-center gap-2">
                  <span class="ui-dot bg-slate-300"></span>
                  Knowledge graph is built
                </p>
                <p class="text-xs text-slate-500 mt-0.5 pl-3.5 tabular-nums">{{ completedCount }} of {{ stages.length }} stages completed · ready for strategy use</p>
              </div>
              <div class="flex items-center gap-2 shrink-0">
                <button
                  @click="openRebuildModal"
                  :disabled="svcBusy"
                  class="ui-btn"
                >
                  Full rebuild
                </button>
                <button
                  @click="openDeleteModal"
                  :disabled="svcBusy || nexusServiceRunning || rebuildOperation.active"
                  class="ui-btn"
                >
                  Delete edges
                </button>
              </div>
            </div>

            <div class="px-5 py-4 grid grid-cols-2 gap-4 lg:grid-cols-4">
              <div>
                <p class="text-xs text-slate-500">Stages done</p>
                <p class="mt-1 text-2xl font-medium text-slate-100 tabular-nums leading-tight">{{ completedCount }}<span class="text-sm text-slate-500 font-normal ml-1">/{{ stages.length }}</span></p>
              </div>
              <div>
                <p class="text-xs text-slate-500">SEC tickers</p>
                <p class="mt-1 text-2xl font-medium text-slate-100 tabular-nums leading-tight">
                  {{ scraper?.index?.toLocaleString() ?? '—' }}<span v-if="scraper?.total_tickers" class="text-sm text-slate-500 font-normal ml-1">/{{ scraper.total_tickers }}</span>
                </p>
              </div>
              <div>
                <p class="text-xs text-slate-500">SEC edges</p>
                <p class="mt-1 text-2xl font-medium text-slate-100 tabular-nums leading-tight">{{ scraper?.edges_count?.toLocaleString() ?? '—' }}</p>
              </div>
              <div>
                <p class="text-xs text-slate-500">Last updated</p>
                <p class="mt-2 text-sm text-slate-100 tabular-nums">{{ fmtDateTime(graphBuild?.last_updated) }}</p>
              </div>
            </div>
          </section>

          <!-- Stage summary -->
          <section class="mt-12">
            <div class="mb-3 flex flex-wrap items-end justify-between gap-x-4 gap-y-2">
              <div>
                <h2 class="ui-section-title">Stage summary</h2>
                <p class="ui-section-sub">Per-stage runtime from the last completed build</p>
              </div>
              <div class="text-right">
                <p class="text-xs text-slate-500">Total runtime</p>
                <p class="mt-1 text-xl font-medium text-slate-100 tabular-nums leading-none">{{ fmtDuration(totalBuildRuntimeSec) || '—' }}</p>
              </div>
            </div>
            <div class="ui-card px-5 py-2 grid grid-cols-1 gap-x-8 sm:grid-cols-2 xl:grid-cols-3">
              <div
                v-for="stage in stages"
                :key="stage.stage_index"
                class="h-12 flex items-center gap-3 border-b border-white/[0.05]"
              >
                <span
                  class="material-symbols-outlined text-[16px] shrink-0"
                  :class="stageMeta(stage.status).color"
                >{{ stageMeta(stage.status).icon || 'radio_button_unchecked' }}</span>
                <p class="min-w-0 flex-1 text-sm truncate" :class="stage.status === 'failed' ? 'text-loss' : 'text-slate-300'">{{ stage.label }}</p>
                <p v-if="fmtDuration(stage.duration_sec)" class="text-xs text-slate-500 tabular-nums shrink-0">{{ fmtDuration(stage.duration_sec) }}</p>
              </div>
            </div>
          </section>

        </template>

        <!-- ─────────────── BUILDING / IDLE state ──────────────────────── -->
        <template v-else>

          <!-- Overall progress, with the SEC scraper beneath when it reports -->
          <section class="mt-12">
            <div class="mb-3">
              <h2 class="ui-section-title">Build progress</h2>
            </div>
            <div class="ui-card divide-y divide-white/[0.06]">
              <div class="px-5 py-4">
                <div class="flex items-start justify-between mb-4 gap-4">
                  <div class="min-w-0 flex-1">
                    <p class="text-sm text-slate-100">
                      {{ graphBuild?.current_phase_label || (nexusRunning ? 'Building graph…' : 'Graph not yet built') }}
                    </p>
                    <p v-if="graphBuild?.message" class="text-xs text-slate-500 mt-1 max-w-2xl leading-snug">{{ graphBuild.message }}</p>
                  </div>
                  <div class="text-right shrink-0">
                    <p class="text-2xl font-medium text-slate-100 leading-none tabular-nums">
                      {{ Math.round(overallPct) }}<span class="text-base font-normal text-slate-500">%</span>
                    </p>
                    <p v-if="etaFormatted" class="text-xs text-slate-500 mt-1 tabular-nums">~{{ etaFormatted }} remaining</p>
                  </div>
                </div>
                <!-- Progress bar -->
                <div class="h-1 bg-raised rounded-full overflow-hidden">
                  <div
                    class="h-full rounded-full progress-fill"
                    :class="nexusRunning ? 'bg-slate-200' : 'bg-slate-600'"
                    :style="{ transform: `scaleX(${overallPct / 100})` }"
                  ></div>
                </div>
              </div>

              <!-- SEC Scraper (when data available) -->
              <div v-if="scraper" class="px-5 py-4">
                <div class="flex items-center justify-between mb-2 gap-3">
                  <p class="text-sm text-slate-100">SEC EDGAR scraper</p>
                  <span
                    class="inline-flex items-center gap-1.5 text-xs capitalize"
                    :class="scraper.status === 'running' || scraper.status === 'completed' ? 'text-slate-200' : 'text-slate-500'"
                  >
                    <span
                      class="ui-dot"
                      :class="scraper.status === 'running'
                        ? 'ui-dot-live'
                        : scraper.status === 'completed'
                          ? 'bg-slate-300'
                          : 'ui-dot-off'"
                    ></span>
                    {{ scraper.status || 'idle' }}
                  </span>
                </div>
                <div class="flex items-center gap-4 text-xs text-slate-500 mb-3 flex-wrap tabular-nums">
                  <span v-if="scraper.index != null">
                    Ticker <span class="text-slate-300">{{ scraper.index?.toLocaleString() }}</span>
                    <span v-if="scraper.total_tickers" class="text-slate-600"> / {{ scraper.total_tickers }}</span>
                  </span>
                  <span v-if="scraper.edges_count != null">
                    Edges <span class="text-slate-300">{{ scraper.edges_count?.toLocaleString() }}</span>
                  </span>
                  <span v-if="scraper.eta_formatted">~{{ scraper.eta_formatted }} remaining</span>
                </div>
                <div class="h-1 bg-raised rounded-full overflow-hidden">
                  <div
                    class="h-full rounded-full bg-slate-400 progress-fill"
                    :style="{ transform: `scaleX(${(scraper.progress_pct || 0) / 100})` }"
                  ></div>
                </div>
              </div>
            </div>
          </section>

          <!-- Vertical stepper -->
          <section class="mt-12">
            <div class="mb-3">
              <h2 class="ui-section-title">Build stages</h2>
            </div>
            <div class="ui-card px-5 py-5">
              <div v-if="!stages.length" class="ui-empty gap-1">
                <p class="text-slate-300">No stage data yet</p>
                <p class="text-xs text-slate-500">Start the Nexus engine to begin building.</p>
              </div>

              <div v-else class="flex flex-col">
                <div v-for="(stage, si) in stages" :key="stage.stage_index" class="flex gap-4">

                  <!-- Icon + connector -->
                  <div class="flex flex-col items-center w-5 shrink-0">
                    <div class="size-5 flex items-center justify-center shrink-0">
                      <span
                        v-if="stageMeta(stage.status).spin"
                        class="ui-dot ui-dot-live size-2"
                        aria-label="Running"
                      ></span>
                      <span
                        v-else
                        class="material-symbols-outlined text-[18px]"
                        :class="stageMeta(stage.status).color"
                      >{{ stageMeta(stage.status).icon }}</span>
                    </div>
                    <div
                      v-if="si < stages.length - 1"
                      class="w-px flex-1 my-1 min-h-4"
                      :class="stageLineColor(stage)"
                    ></div>
                  </div>

                  <!-- Stage content -->
                  <div class="pb-5 flex-1 min-w-0">
                    <div class="flex items-start justify-between gap-3">
                      <div class="min-w-0 flex-1">
                        <p
                          class="text-sm leading-5"
                          :class="stage.status === 'running'
                            ? 'text-slate-100'
                            : stage.status === 'stopped'
                              ? 'text-slate-300'
                            : stage.status === 'completed' || stage.status === 'skipped'
                              ? 'text-slate-300'
                              : stage.status === 'failed'
                                ? 'text-loss'
                                : 'text-slate-500'"
                        >{{ stage.label }}</p>

                        <p v-if="stage.message" class="text-xs text-slate-500 mt-0.5 leading-snug">{{ stage.message }}</p>

                        <!-- Substep progress (only while running and multi-step) -->
                        <div v-if="stage.status === 'running' && stage.total_substeps > 1" class="mt-2 flex items-center gap-3">
                          <div class="h-1 bg-raised rounded-full overflow-hidden w-24">
                            <div
                              class="h-full bg-slate-300 rounded-full progress-fill"
                              :style="{ transform: `scaleX(${substepPct(stage) / 100})` }"
                            ></div>
                          </div>
                          <span class="text-xs text-slate-400 tabular-nums">{{ stage.substeps_completed ?? 0 }}/{{ stage.total_substeps }} substeps</span>
                        </div>
                      </div>

                      <div class="shrink-0 text-right leading-5">
                        <span v-if="fmtDuration(stage.duration_sec)" class="text-xs text-slate-500 tabular-nums">{{ fmtDuration(stage.duration_sec) }}</span>
                        <span v-else-if="stage.status === 'running'" class="text-xs text-slate-300">Running</span>
                        <span v-else-if="stage.status === 'stopped'" class="text-xs text-slate-300 inline-flex items-center gap-1.5"><span class="ui-dot ui-dot-warn"></span>Stopped</span>
                        <span v-else-if="stage.status === 'pending'" class="text-xs text-slate-600">Pending</span>
                      </div>
                    </div>
                  </div>

                </div>
              </div>
            </div>
          </section>

        </template>

        <!-- Live build log terminal (always present when /nexus status loaded) -->
        <div class="mt-12">
          <NexusLiveLogs />
        </div>

      </template>

      <!-- Cannot load -->
      <div v-else class="mt-8 ui-card">
        <div class="ui-empty gap-1">
          <p class="text-slate-300">Unable to load Nexus status</p>
          <p class="text-xs text-slate-500">Check that the backend is reachable.</p>
          <button
            @click="fetchStatus"
            class="ui-btn mt-3"
          >Retry</button>
        </div>
      </div>

      <Teleport to="body">
        <Transition name="modal-fade">
          <div
            v-if="showDeleteModal"
            class="ui-overlay z-[118]"
            @click.self="closeDeleteModal"
          >
            <div class="ui-modal max-w-2xl max-h-[calc(100dvh-2rem)] flex flex-col">
              <div class="ui-modal-head shrink-0 border-b border-white/[0.06]">
                <div class="min-w-0 flex-1">
                  <h2 class="text-base font-medium text-slate-100 inline-flex items-center gap-2">
                    <span
                      v-if="deleteOperation.active || deleteRequestPending"
                      class="material-symbols-outlined text-[18px] text-slate-400 animate-spin"
                    >progress_activity</span>
                    {{ showDeleteProgressState ? deleteProgressTitle : 'Delete Nexus edges' }}
                  </h2>
                  <p class="text-sm text-slate-500 mt-1">
                    {{ showDeleteProgressState
                      ? deleteProgressMessage
                      : 'Select the phases whose graph output should be deleted from Neo4j.' }}
                  </p>
                </div>
                <button
                  v-if="!deleteOperation.active && !deleteRequestPending"
                  @click="closeDeleteModal"
                  class="ui-icon-btn shrink-0"
                  aria-label="Close"
                >
                  <span class="material-symbols-outlined text-[18px]">close</span>
                </button>
              </div>

              <div v-if="!showDeleteProgressState" class="min-h-0 flex-1 overflow-y-auto px-6 py-5">
                <div class="flex items-end justify-between gap-3">
                  <div>
                    <p class="text-sm text-slate-300">Phase selection</p>
                    <p class="text-xs text-slate-500 mt-0.5">Only the checked phases will be deleted.</p>
                  </div>
                  <div class="flex items-center gap-1">
                    <button
                      type="button"
                      @click="selectAllDeletePhases"
                      class="ui-btn-ghost"
                    >Select all</button>
                    <button
                      type="button"
                      @click="clearDeletePhases"
                      class="ui-btn-ghost"
                    >Deselect all</button>
                  </div>
                </div>
                <div class="mt-3 max-h-72 overflow-y-auto rounded-lg border border-white/[0.07] divide-y divide-white/[0.05]">
                  <label
                    v-for="option in deletePhaseOptions"
                    :key="`delete-phase-${option.value}`"
                    class="h-10 flex items-center gap-3 px-3 cursor-pointer transition-colors duration-150 ease-out hover:bg-white/[0.03]"
                  >
                    <input
                      v-model="deleteSelectedPhases"
                      :value="Number(option.value)"
                      type="checkbox"
                      class="size-4 accent-slate-200"
                    />
                    <span class="text-sm text-slate-200">{{ option.label }}</span>
                  </label>
                </div>
                <p v-if="deletePhaseSelectionInvalid" class="mt-2 text-xs text-loss">
                  Select at least one phase to delete.
                </p>
                <p v-if="deleteActionError" class="mt-2 text-xs text-loss">
                  {{ deleteActionError }}
                </p>
              </div>

              <div v-else class="min-h-0 flex-1 overflow-y-auto px-6 py-5 space-y-4">
                <div>
                  <p class="text-xs text-slate-500">Overall progress</p>
                  <p class="text-2xl font-medium text-slate-100 mt-1 leading-none tabular-nums">
                    {{ Number(deleteOperation.current || 0).toLocaleString() }}
                    <span class="text-sm font-normal text-slate-500">
                      / {{ Number(deleteOperation.total || 0).toLocaleString() }} {{ deleteOperation.unit || 'phases' }}
                    </span>
                  </p>
                  <p v-if="deleteOperation.step" class="text-xs text-slate-400 mt-2">{{ deleteOperation.step }}</p>
                  <p v-if="deleteOperation.error" class="text-xs text-loss mt-2">{{ deleteOperation.error }}</p>
                </div>

                <div class="rounded-lg border border-white/[0.07] divide-y divide-white/[0.05]">
                  <div
                    v-for="row in deleteOperation.phaseRows"
                    :key="`delete-progress-${row.phase_value}`"
                    class="px-4 py-3"
                  >
                    <div class="flex items-start justify-between gap-3">
                      <div class="min-w-0 flex-1">
                        <p class="text-sm text-slate-100">{{ row.label }}</p>
                        <p class="text-xs text-slate-500 mt-0.5">{{ row.message || 'Queued' }}</p>
                      </div>
                      <span
                        class="inline-flex items-center gap-1.5 text-xs capitalize"
                        :class="row.status === 'failed' ? 'text-loss' : 'text-slate-300'"
                      >
                        <span
                          class="ui-dot"
                          :class="row.status === 'failed'
                            ? 'ui-dot-error'
                            : row.status === 'completed'
                              ? 'bg-slate-300'
                              : row.status === 'running'
                                ? 'ui-dot-live'
                                : 'ui-dot-off'"
                        ></span>
                        {{ row.status }}
                      </span>
                    </div>
                    <div class="mt-3">
                      <div class="h-1 bg-raised rounded-full overflow-hidden">
                        <div
                          class="h-full rounded-full progress-fill"
                          :class="row.status === 'failed'
                            ? 'bg-loss'
                            : row.status === 'completed'
                              ? 'bg-slate-300'
                              : row.status === 'running'
                                ? 'bg-slate-200'
                                : 'bg-slate-600'"
                          :style="{ transform: `scaleX(${Math.max(0, Math.min(100, Number(row.progress_pct || 0))) / 100})` }"
                        ></div>
                      </div>
                      <div class="flex items-center justify-between gap-3 mt-2 text-xs text-slate-500 tabular-nums">
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

              <div class="ui-modal-foot shrink-0">
                <button
                  v-if="!showDeleteProgressState"
                  type="button"
                  @click="closeDeleteModal"
                  class="ui-btn-ghost"
                >Cancel</button>
                <button
                  v-if="!showDeleteProgressState"
                  type="button"
                  @click="deleteEdges"
                  :disabled="deletePhaseSelectionInvalid || deleteRequestPending"
                  class="ui-btn text-loss"
                >
                  Delete selected edges
                </button>
                <button
                  v-else-if="!deleteOperation.active && !deleteRequestPending"
                  type="button"
                  @click="closeDeleteModal"
                  class="ui-btn"
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
            class="ui-overlay z-[120]"
          >
            <div class="ui-modal max-w-lg">
              <div class="ui-modal-head border-b border-white/[0.06]">
                <span class="material-symbols-outlined text-[18px] text-slate-400 animate-spin mt-0.5 shrink-0">progress_activity</span>
                <div class="min-w-0 flex-1">
                  <h2 class="text-base font-medium text-slate-100">{{ rebuildProgressTitle }}</h2>
                  <p class="text-sm text-slate-500 mt-1">{{ rebuildProgressMessage }}</p>
                </div>
              </div>
              <div class="px-6 py-5 space-y-4">
                <div v-if="rebuildOperation.step">
                  <p class="text-xs text-slate-500">Current step</p>
                  <p class="text-sm text-slate-100 mt-1">{{ rebuildOperation.step }}</p>
                </div>
                <div v-if="rebuildProgressCount">
                  <p class="text-xs text-slate-500">Progress</p>
                  <p class="text-2xl font-medium text-slate-100 mt-1 leading-none tabular-nums">{{ rebuildProgressCount }}</p>
                </div>
                <p class="text-xs text-slate-500">
                  Keep this page open while the rebuild request completes. Nexus will restart automatically after the delete step finishes.
                </p>
              </div>
            </div>
          </div>
        </Transition>
      </Teleport>

      <!-- Manual start modal -->
      <Teleport to="body">
        <Transition name="modal-fade">
          <div
            v-if="showStartModal"
            class="ui-overlay"
            @click.self="closeStartModal"
          >
            <div class="ui-modal max-w-md max-h-[calc(100dvh-2rem)] flex flex-col">
              <div class="ui-modal-head shrink-0 border-b border-white/[0.06]">
                <div class="min-w-0">
                  <h2 class="text-base font-medium text-slate-100">{{ manualStartTitle }}</h2>
                  <p class="text-sm text-slate-500 mt-1">Choose the phases to run for this manual execution</p>
                </div>
              </div>
              <div class="min-h-0 flex-1 overflow-y-auto px-6 py-5 space-y-6">
                <div>
                  <div class="flex items-end justify-between gap-3">
                    <div>
                      <p class="text-sm text-slate-300">Phase selection</p>
                      <p class="text-xs text-slate-500 mt-0.5">Only the checked phases will run for this manual start.</p>
                    </div>
                    <div class="flex items-center gap-1 shrink-0">
                      <button
                        type="button"
                        @click="selectAllSelectedPhases"
                        class="ui-btn-ghost"
                      >Select all</button>
                      <button
                        type="button"
                        @click="clearSelectedPhases"
                        class="ui-btn-ghost"
                      >Deselect all</button>
                    </div>
                  </div>
                  <div class="mt-3 max-h-64 overflow-y-auto rounded-lg border border-white/[0.07] divide-y divide-white/[0.05]">
                    <label
                      v-for="option in nexusPhaseOptions"
                      :key="`manual-phase-${option.value}`"
                      class="h-10 flex items-center gap-3 px-3 cursor-pointer transition-colors duration-150 ease-out hover:bg-white/[0.03]"
                    >
                      <input
                        v-model="selectedPhases"
                        :value="Number(option.value)"
                        type="checkbox"
                        class="size-4 accent-slate-200"
                      />
                      <span class="text-sm text-slate-200">{{ option.label }}</span>
                    </label>
                  </div>
                  <p v-if="manualPhaseSelectionInvalid" class="mt-2 text-xs text-loss">
                    Select at least one phase to start Nexus.
                  </p>
                </div>
                <div>
                  <label class="ui-field-label">13F history (quarters)</label>
                  <input
                    v-model.number="phase7HistoryQuarters"
                    type="number"
                    min="1"
                    max="120"
                    step="1"
                    :disabled="historicalModeEnabled"
                    class="ui-input tabular-nums"
                  />
                  <p class="ui-help">
                    <span v-if="historicalModeEnabled">
                      Derived automatically from the historical start date while historical bootstrap mode is enabled.
                    </span>
                    <span v-else>
                      Larger values load more quarterly 13F history for backtests and make Phase 7 slower.
                    </span>
                  </p>
                </div>
                <div class="rounded-lg border border-white/[0.07] divide-y divide-white/[0.06]">
                  <div class="p-4 space-y-4">
                    <label class="flex items-start justify-between gap-3 cursor-pointer">
                      <div>
                        <p class="text-sm text-slate-200">Historical bootstrap</p>
                        <p class="text-xs text-slate-500 mt-0.5">Backfill supported temporal phases from a chosen start date through today, then switch future reruns to incremental fetches only.</p>
                      </div>
                      <input v-model="historicalModeEnabled" type="checkbox" class="mt-0.5 size-4 shrink-0 accent-slate-200" />
                    </label>
                    <div>
                      <label class="ui-field-label">Historical start date</label>
                      <input
                        v-model="historicalStartDate"
                        type="date"
                        :disabled="!historicalModeEnabled"
                        class="ui-input tabular-nums [color-scheme:dark]"
                      />
                      <p class="ui-help">
                        Phase 3, Phase 7, and Phase 11 will use this date to build historical intervals and keep future reruns incremental.
                      </p>
                      <p v-if="historicalConfigInvalid" class="mt-1 text-xs text-loss">
                        Historical bootstrap needs a valid start date.
                      </p>
                    </div>
                  </div>
                  <div class="p-4">
                    <label class="flex items-start justify-between gap-3 cursor-pointer">
                      <div>
                        <p class="text-sm text-slate-200">Force bootstrap rebuild</p>
                        <p class="text-xs text-slate-500 mt-0.5">
                          Reset historical bootstrap coverage and rebuild the temporal backfill from the configured start date for this manual run.
                        </p>
                      </div>
                      <input
                        v-model="forceBootstrapRebuild"
                        type="checkbox"
                        :disabled="!bootstrapRebuildAvailable && !historicalModeEnabled"
                        class="mt-0.5 size-4 shrink-0 accent-slate-200 disabled:opacity-50"
                      />
                    </label>
                    <p v-if="!bootstrapRebuildAvailable && !historicalModeEnabled" class="mt-2 text-xs text-slate-500">
                      Force bootstrap rebuild becomes available after you configure a historical start date.
                    </p>
                  </div>
                </div>
                <p class="text-xs text-slate-500">
                  Phase selection only affects this manual run. Auto-update scheduling still uses its own configured start and end phases.
                </p>
              </div>
              <div class="ui-modal-foot shrink-0">
                <button
                  @click="closeStartModal"
                  class="ui-btn-ghost"
                >Cancel</button>
                <button
                  @click="startNexus"
                  :disabled="svcBusy || historicalConfigInvalid || manualPhaseSelectionInvalid"
                  class="ui-btn-primary"
                >
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
            class="ui-overlay"
            @click.self="showAutoUpdateModal = false"
          >
            <div class="ui-modal max-w-md max-h-[calc(100dvh-2rem)] flex flex-col">
              <div class="ui-modal-head shrink-0 border-b border-white/[0.06]">
                <div class="min-w-0">
                  <h2 class="text-base font-medium text-slate-100">Nexus auto-update</h2>
                  <p class="text-sm text-slate-500 mt-1">Keep Nexus online and rerun a chosen phase range on a schedule</p>
                </div>
              </div>
              <div class="min-h-0 flex-1 overflow-y-auto px-6 py-5 space-y-5">
                <label class="flex items-start justify-between gap-3 cursor-pointer">
                  <div>
                    <p class="text-sm text-slate-200">Enable auto-update</p>
                    <p class="text-xs text-slate-500 mt-0.5">Nexus will stay running and queue the next refresh automatically.</p>
                  </div>
                  <input v-model="autoUpdateEnabled" type="checkbox" class="mt-0.5 size-4 shrink-0 accent-slate-200" />
                </label>
                <div class="grid grid-cols-1 sm:grid-cols-3 gap-3">
                  <div class="sm:col-span-1">
                    <label class="ui-field-label">Interval (hours)</label>
                    <input
                      v-model="autoUpdateIntervalHours"
                      type="number"
                      min="1"
                      step="1"
                      class="ui-input tabular-nums"
                    />
                  </div>
                  <div>
                    <label class="ui-field-label">From phase</label>
                    <select v-model="autoUpdateStartPhase" class="ui-input">
                      <option v-for="option in nexusPhaseOptions" :key="`auto-start-${option.value}`" :value="option.value">{{ option.label }}</option>
                    </select>
                  </div>
                  <div>
                    <label class="ui-field-label">To phase</label>
                    <select v-model="autoUpdateEndPhase" class="ui-input">
                      <option v-for="option in nexusPhaseOptions" :key="`auto-end-${option.value}`" :value="option.value">{{ option.label }}</option>
                    </select>
                  </div>
                </div>
                <div>
                  <label class="ui-field-label">13F history (quarters)</label>
                  <input
                    v-model.number="phase7HistoryQuarters"
                    type="number"
                    min="1"
                    max="120"
                    step="1"
                    :disabled="historicalModeEnabled"
                    class="ui-input tabular-nums"
                  />
                  <p class="ui-help">
                    <span v-if="historicalModeEnabled">
                      Derived automatically from the historical start date while historical bootstrap mode is enabled.
                    </span>
                    <span v-else>
                      Higher values load more historical 13F intervals for backtests, but Phase 7 takes longer and writes more HOLDS edges.
                    </span>
                  </p>
                </div>
                <div class="rounded-lg border border-white/[0.07] p-4">
                  <p class="text-sm text-slate-200">Historical bootstrap coverage</p>
                  <p class="text-xs text-slate-500 mt-1">
                    Scheduled auto-updates reuse existing bootstrap coverage and fetch only newer source windows. Change bootstrap settings from the start/rerun modal or the bootstrap card, not here.
                  </p>
                  <p class="text-xs text-slate-400 mt-2 tabular-nums">
                    Current coverage: {{ historicalCoverageSummary }}
                  </p>
                </div>
              </div>
              <div class="ui-modal-foot shrink-0">
                <button
                  @click="showAutoUpdateModal = false"
                  class="ui-btn-ghost"
                >Cancel</button>
                <button
                  @click="saveAutoUpdate"
                  :disabled="historicalConfigInvalid"
                  class="ui-btn-primary"
                >
                  Save schedule
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
            class="ui-overlay items-start overflow-y-auto sm:items-center"
            @click.self="closeRebuildModal"
          >
            <div class="ui-modal max-w-2xl max-h-[calc(100dvh-2rem)] flex flex-col">
              <div class="ui-modal-head shrink-0 border-b border-white/[0.06]">
                <div class="min-w-0">
                  <h2 class="text-base font-medium text-slate-100">Full rebuild</h2>
                  <p class="text-sm text-slate-500 mt-1">
                    {{ rebuildIsDestructive
                      ? 'Destructive mode clears Neo4j data and Nexus progress before restarting from phase 1.'
                      : 'In-place mode reruns from phase 1, optionally clears selected cache entries, and keeps the existing graph online.' }}
                  </p>
                </div>
              </div>
              <div class="min-h-0 flex-1 overflow-y-auto px-6 py-5 space-y-5">
                <div>
                  <p class="text-sm text-slate-300">Rebuild mode</p>
                  <div class="mt-3 grid grid-cols-1 sm:grid-cols-2 gap-3">
                    <label
                      class="rounded-lg border px-4 py-3 cursor-pointer transition-colors duration-150 ease-out"
                      :class="rebuildMode === 'non_destructive'
                        ? 'border-white/[0.16] bg-raised'
                        : 'border-white/[0.07] hover:bg-white/[0.03]'"
                    >
                      <div class="flex items-start gap-3">
                        <input v-model="rebuildMode" value="non_destructive" type="radio" class="mt-0.5 accent-slate-200" />
                        <div>
                          <p class="text-sm text-slate-100">In-place rebuild</p>
                          <p class="text-xs text-slate-500 mt-1">Recommended. Keeps current graph data online while Nexus reruns and swaps in refreshed intervals.</p>
                        </div>
                      </div>
                    </label>
                    <label
                      class="rounded-lg border px-4 py-3 cursor-pointer transition-colors duration-150 ease-out"
                      :class="rebuildMode === 'destructive'
                        ? 'border-loss/30 bg-raised'
                        : 'border-white/[0.07] hover:bg-white/[0.03]'"
                    >
                      <div class="flex items-start gap-3">
                        <input v-model="rebuildMode" value="destructive" type="radio" class="mt-0.5 accent-slate-200" />
                        <div>
                          <p class="text-sm text-slate-100">Destructive rebuild</p>
                          <p class="text-xs text-slate-500 mt-1">Clears Neo4j graph data and resets Nexus progress in RethinkDB before phase 1 starts again.</p>
                        </div>
                      </div>
                    </label>
                  </div>
                </div>

                <p class="text-sm leading-relaxed" :class="rebuildIsDestructive ? 'text-loss' : 'text-slate-400'">
                  {{ rebuildIsDestructive
                    ? 'This will intentionally take Nexus graph data offline until the rebuild repopulates Neo4j. Use this only when you want the legacy wipe-and-rebuild behavior.'
                    : 'Nexus will rerun from phase 1 and preserve historical edge intervals so strategies can keep reading the current graph while the refresh is in progress.' }}
                </p>
                <div class="rounded-lg border border-white/[0.07] p-4">
                  <label class="flex items-start justify-between gap-3 cursor-pointer">
                    <div>
                      <p class="text-sm text-slate-200">Force bootstrap rebuild</p>
                      <p class="text-xs text-slate-500 mt-1">
                        {{ rebuildIsDestructive
                          ? 'Destructive rebuild already resets historical bootstrap checkpoints when bootstrap mode is configured.'
                          : 'Also clear historical bootstrap checkpoints so Phase 3, Phase 7, and Phase 11 rebuild historical coverage from the configured bootstrap start date.' }}
                      </p>
                    </div>
                    <input
                      v-model="rebuildForceBootstrap"
                      type="checkbox"
                      :disabled="!bootstrapRebuildAvailable"
                      class="mt-0.5 size-4 shrink-0 accent-slate-200 disabled:opacity-50"
                    />
                  </label>
                  <p v-if="!bootstrapRebuildAvailable" class="text-xs text-slate-500 mt-2">
                    Historical bootstrap needs a stored start date before there is bootstrap coverage to rebuild.
                  </p>
                </div>
                <div class="rounded-lg border border-white/[0.07] p-4">
                  <div class="flex items-start justify-between gap-3 flex-wrap">
                    <div class="min-w-0">
                      <p class="text-sm text-slate-200">Optional cache cleanup</p>
                      <p class="text-xs text-slate-500 mt-1">
                        Visible entries are top-level items under <span class="text-slate-300">{{ rebuildCacheRoot }}</span>.
                        Selected entries are deleted before the rebuild starts. If the Nexus container is running, it is restarted afterward.
                      </p>
                    </div>
                    <button
                      v-if="rebuildCacheAvailable && rebuildCacheEntries.length"
                      @click="toggleAllRebuildCacheSelections"
                      type="button"
                      class="ui-btn"
                    >
                      {{ allRebuildCacheSelected ? 'Clear selection' : 'Select all' }}
                    </button>
                  </div>

                  <div v-if="rebuildCacheLoading" class="mt-4 text-sm text-slate-500">
                    Loading Nexus cache entries…
                  </div>

                  <div
                    v-else-if="rebuildCacheAvailable && rebuildCacheEntries.length"
                    class="mt-4 grid grid-cols-1 sm:grid-cols-2 gap-2 max-h-72 overflow-y-auto pr-1"
                  >
                    <label
                      v-for="entry in rebuildCacheEntries"
                      :key="entry.path"
                      class="flex items-start gap-3 rounded-lg border border-white/[0.07] px-3 py-2.5 cursor-pointer transition-colors duration-150 ease-out hover:bg-white/[0.03]"
                    >
                      <input
                        v-model="rebuildSelectedCachePaths"
                        :value="entry.path"
                        type="checkbox"
                        class="mt-0.5 size-4 shrink-0 accent-slate-200"
                      />
                      <span class="material-symbols-outlined text-[16px] mt-px text-slate-500">{{ entry.is_dir ? 'folder' : 'description' }}</span>
                      <span class="min-w-0 flex-1">
                        <span class="block text-sm text-slate-200 break-all">{{ entry.path }}</span>
                        <span class="block text-xs text-slate-500 mt-0.5 tabular-nums">
                          {{ entry.is_dir ? 'Folder' : 'File' }}
                          <span v-if="!entry.is_dir && entry.size_bytes != null"> · {{ entry.size_bytes.toLocaleString() }} bytes</span>
                        </span>
                      </span>
                    </label>
                  </div>

                  <p v-else-if="rebuildCacheAvailable" class="mt-4 text-sm text-slate-500">
                    No cache entries were found in {{ rebuildCacheRoot }}.
                  </p>

                  <p v-else class="mt-4 text-sm text-slate-400">
                    Cache selection is available when the API can access the Nexus cache root directly or when the Nexus container is running.
                  </p>

                  <p v-if="rebuildCacheError" class="mt-3 text-xs text-loss">{{ rebuildCacheError }}</p>
                </div>
                <div>
                  <label class="ui-field-label">
                    Type <span class="text-slate-100">confirm</span> to proceed
                  </label>
                  <input
                    v-model="rebuildConfirmText"
                    type="text"
                    placeholder="confirm"
                    autocomplete="off"
                    @keydown.enter="rebuildNexus"
                    class="ui-input"
                  />
                </div>
              </div>
              <div class="ui-modal-foot shrink-0">
                <button
                  @click="closeRebuildModal"
                  class="ui-btn-ghost"
                >Cancel</button>
                <button
                  @click="rebuildNexus"
                  :disabled="rebuildConfirmText.toLowerCase().trim() !== 'confirm' || rebuildCacheLoading"
                  :class="rebuildIsDestructive ? 'ui-btn h-9 px-4 text-loss' : 'ui-btn-primary'"
                >
                  {{ rebuildIsDestructive ? 'Confirm destructive rebuild' : 'Confirm rebuild' }}
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
            class="ui-overlay"
            @click.self="closeDisableBootstrapModal"
          >
            <div class="ui-modal max-w-md">
              <div class="ui-modal-head">
                <div class="min-w-0">
                  <h2 class="text-base font-medium text-slate-100">Disable historical bootstrap</h2>
                  <p class="text-sm text-slate-500 mt-1">This clears bootstrap checkpoints and disables future historical interval maintenance</p>
                </div>
              </div>
              <div class="ui-modal-body space-y-3">
                <p class="text-sm text-slate-300">
                  Disable historical bootstrap for Nexus? Existing graph data stays in Neo4j, but future Nexus runs will stop maintaining historical bootstrap coverage and manifests.
                </p>
                <p class="text-xs text-slate-500 tabular-nums">
                  Current bootstrap coverage: {{ historicalCoverageSummary }}
                </p>
              </div>
              <div class="ui-modal-foot">
                <button
                  @click="closeDisableBootstrapModal"
                  class="ui-btn-ghost"
                >Cancel</button>
                <button
                  @click="confirmDisableBootstrap"
                  :disabled="svcBusy"
                  class="ui-btn text-loss"
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
          class="ui-overlay"
          @click.self="closeEnableBootstrapModal"
        >
          <div class="ui-modal max-w-sm">
            <!-- Header -->
            <div class="ui-modal-head">
              <div class="min-w-0">
                <h2 class="text-base font-medium text-slate-100">Enable historical bootstrap</h2>
                <p class="text-sm text-slate-500 mt-1">Set the start date for temporal data coverage</p>
              </div>
              <button @click="closeEnableBootstrapModal" class="ui-icon-btn ml-auto shrink-0" aria-label="Close">
                <span class="material-symbols-outlined text-[18px]">close</span>
              </button>
            </div>

            <!-- Body -->
            <div class="ui-modal-body">
              <label class="ui-field-label">Historical start date</label>
              <input
                v-model="enableBootstrapDate"
                type="date"
                class="ui-input tabular-nums [color-scheme:dark]"
                @keydown.enter="confirmEnableBootstrap"
              />
              <p class="ui-help">
                Phases 3, 7, and 11 will be backfilled from this date. Future reruns fetch only newer data.
              </p>
            </div>

            <!-- Footer -->
            <div class="ui-modal-foot">
              <button
                @click="closeEnableBootstrapModal"
                class="ui-btn-ghost"
              >Cancel</button>
              <button
                @click="confirmEnableBootstrap"
                :disabled="!enableBootstrapDate"
                class="ui-btn-primary"
              >
                Enable bootstrap
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
.modal-fade-leave-active { transition: opacity 150ms cubic-bezier(0.22, 1, 0.36, 1); }
.modal-fade-enter-from,
.modal-fade-leave-to    { opacity: 0; }

/* Progress bars grow with transform, matching the dashboard. */
.progress-fill {
  transform-origin: left center;
  transition: transform 500ms cubic-bezier(0.22, 1, 0.36, 1);
}
</style>
