<!-- frontend/src/components/stock/TradeTicket.vue
     Buy or sell from the stock page: a ticket (account, shares or dollars,
     market or limit, time in force), then a review that names the account
     and says Live money or Paper, then the order Alpaca returned.

     The ticket checks the same rules as the server (utils/trade.js mirrors
     build_manual_order) before anything is sent. One client_order_id is
     generated when the ticket opens and reused for every attempt, so a
     retried confirm can't place the order twice. Nothing is retried
     automatically; a refusal shows the server's `detail` in the review. -->
<template>
  <Teleport to="body">
    <div class="ui-overlay p-0 items-stretch sm:items-center sm:p-4" @click.self="onDismiss">
      <div
        ref="panelEl"
        class="ui-modal max-w-md h-full sm:h-auto sm:max-h-[90vh] rounded-none sm:rounded-2xl border-0 sm:border flex flex-col outline-none"
        role="dialog"
        aria-modal="true"
        :aria-labelledby="titleId"
        tabindex="-1"
      >
        <!-- ── Result ─────────────────────────────────────────────── -->
        <template v-if="step === 'result' && placed">
          <div class="flex-1 flex flex-col items-center justify-center gap-3 px-6 py-14 text-center" role="status">
            <span class="material-symbols-outlined text-[36px] text-gain" aria-hidden="true">check_circle</span>
            <h2 :id="titleId" class="text-lg font-medium text-slate-100">{{ placedOrderLine(placed) }}</h2>
            <p class="inline-flex items-center gap-2 text-sm text-slate-300">
              <span class="ui-dot" :class="orderStatusDot(placed)"></span>
              {{ orderStatusWord(placed) }}
              <span class="text-slate-600" aria-hidden="true">·</span>
              <span class="text-slate-400">{{ account?.account_name }}, {{ modeLabel }}</span>
            </p>
            <p class="text-xs text-slate-500 max-w-sm leading-relaxed">
              Alpaca has the order. It shows under Orders on this page until it fills or is canceled
            </p>
            <p class="font-mono text-xs text-slate-500 select-all break-all">{{ placed.id }}</p>
          </div>
          <div class="ui-modal-foot shrink-0">
            <button type="button" class="ui-btn-primary" @click="emit('close')">Done</button>
          </div>
        </template>

        <!-- ── Review ─────────────────────────────────────────────── -->
        <template v-else-if="step === 'review'">
          <div class="ui-modal-head border-b border-white/[0.06] shrink-0">
            <div class="min-w-0">
              <div class="flex items-baseline gap-x-3 flex-wrap">
                <h2 :id="titleId" class="text-2xl font-medium text-slate-100 leading-tight">{{ ticket.symbol }}</h2>
                <span class="text-sm text-slate-300">{{ ticket.side === 'sell' ? 'Sell' : 'Buy' }} · {{ typeLabel(ticket.orderType) }}</span>
              </div>
              <p class="mt-1.5 flex items-center gap-2 text-sm">
                <span class="ui-dot" :class="isLive ? 'ui-dot-warn' : 'ui-dot-off'"></span>
                <span :class="isLive ? 'text-slate-100' : 'text-slate-400'">{{ modeLabel }}</span>
                <span class="text-slate-600" aria-hidden="true">·</span>
                <span class="text-slate-400 truncate">{{ account?.account_name }}</span>
              </p>
            </div>
            <button type="button" class="ui-icon-btn ml-auto shrink-0 disabled:opacity-40" :disabled="busy" aria-label="Close" @click="emit('close')">
              <span class="material-symbols-outlined text-[18px]">close</span>
            </button>
          </div>

          <div class="flex-1 min-h-0 overflow-y-auto px-6 py-5 space-y-5">
            <div class="grid grid-cols-2 gap-4">
              <div class="min-w-0">
                <p class="text-xs text-slate-500">{{ estimate.label }}</p>
                <p class="text-xl font-medium text-slate-100 tabular-nums mt-1 truncate">{{ estimate.amount == null ? '—' : fmtUsd(estimate.amount) }}</p>
                <p class="text-xs text-slate-500 mt-0.5">{{ basis }}</p>
              </div>
              <div class="min-w-0">
                <p class="text-xs text-slate-500">{{ ticket.sizeMode === 'dollars' ? 'Amount' : 'Shares' }}</p>
                <p class="text-xl font-medium text-slate-100 tabular-nums mt-1 truncate">{{ ticket.sizeMode === 'dollars' ? fmtUsd(parseDecimal(ticket.amount).value) : plainQtyText(parseDecimal(ticket.amount).value) }}</p>
              </div>
            </div>

            <dl class="rounded-lg border border-white/[0.07] divide-y divide-white/[0.05]">
              <div v-for="r in rows" :key="r.label" class="row">
                <dt>{{ r.label }}</dt>
                <dd class="tabular-nums text-slate-100">{{ r.value }}</dd>
              </div>
            </dl>

            <p v-if="oversell" class="flex items-start gap-2 text-sm text-slate-300 leading-relaxed">
              <span class="ui-dot ui-dot-warn mt-2"></span>
              <span>This sells more than the {{ plainQtyText(Math.max(0, held || 0)) }} sh you hold. Alpaca refuses it or opens a short position</span>
            </p>
            <p v-if="notice" class="text-xs text-slate-500 leading-relaxed">{{ notice }}</p>
            <p class="text-xs text-slate-500 leading-relaxed">{{ caveat }}</p>
          </div>

          <div v-if="error" class="px-6 pb-3 shrink-0">
            <div class="ui-error py-3" role="alert">{{ error }}</div>
          </div>

          <div class="ui-modal-foot shrink-0">
            <button type="button" class="ui-btn-ghost h-9 px-4" :disabled="busy" @click="backToEdit">Back</button>
            <button type="button" class="ui-btn-primary" :disabled="busy" @click="confirm">
              <span v-if="busy" class="material-symbols-outlined text-[16px] animate-spin">progress_activity</span>
              {{ busy ? 'Sending…' : confirmLabel(ticket) }}
            </button>
          </div>
        </template>

        <!-- ── Ticket ─────────────────────────────────────────────── -->
        <template v-else>
          <div class="ui-modal-head shrink-0">
            <div class="min-w-0">
              <h2 :id="titleId" class="text-base font-medium text-slate-100">{{ ticket.side === 'sell' ? 'Sell' : 'Buy' }} {{ ticket.symbol }}</h2>
              <p class="text-sm text-slate-500 mt-1 tabular-nums">{{ lastPrice != null ? `Last ${fmtPrice(lastPrice)}` : 'No recent price' }}</p>
            </div>
            <button type="button" class="ui-icon-btn ml-auto shrink-0" aria-label="Close" @click="emit('close')">
              <span class="material-symbols-outlined text-[18px]">close</span>
            </button>
          </div>

          <form class="flex-1 min-h-0 overflow-y-auto ui-modal-body space-y-5" novalidate @submit.prevent="toReview">
            <div class="ui-seg w-full grid grid-cols-2" role="group" aria-label="Side">
              <button
                v-for="s in [['buy', 'Buy'], ['sell', 'Sell']]"
                :key="s[0]"
                type="button"
                class="ui-seg-btn h-8"
                :class="ticket.side === s[0] ? 'ui-seg-btn-active' : ''"
                :aria-pressed="ticket.side === s[0]"
                @click="ticket.side = s[0]"
              >{{ s[1] }}</button>
            </div>

            <div>
              <label :for="`${uid}-account`" class="ui-field-label">Account</label>
              <select :id="`${uid}-account`" v-model="accountId" class="ui-input">
                <option v-for="a in accounts" :key="a.id" :value="a.id">{{ a.account_name }} · {{ accountModeLabel(a) }}</option>
              </select>
              <div class="mt-2 min-h-5 flex items-center justify-between gap-3 text-xs">
                <span class="text-slate-500 tabular-nums">
                  <template v-if="held === undefined">Checking your position…</template>
                  <template v-else-if="heldText">{{ heldText }}</template>
                  <template v-else>You don't hold {{ ticket.symbol }} here</template>
                </span>
                <button
                  v-if="ticket.side === 'sell' && held > 0"
                  type="button"
                  class="text-xs text-slate-300 hover:text-slate-100 underline decoration-white/20 underline-offset-2 transition-colors duration-150 ease-out"
                  @click="sellAll"
                >Sell all</button>
              </div>
            </div>

            <div>
              <div class="flex items-center justify-between gap-3 mb-2">
                <label :for="`${uid}-amount`" class="text-sm text-slate-300">{{ ticket.sizeMode === 'dollars' ? 'Amount' : 'Shares' }}</label>
                <div class="ui-seg" role="group" aria-label="Order size in">
                  <button
                    v-for="m in [['shares', 'Shares'], ['dollars', 'Dollars']]"
                    :key="m[0]"
                    type="button"
                    class="ui-seg-btn"
                    :class="ticket.sizeMode === m[0] ? 'ui-seg-btn-active' : ''"
                    :aria-pressed="ticket.sizeMode === m[0]"
                    @click="setSizeMode(m[0])"
                  >{{ m[1] }}</button>
                </div>
              </div>
              <div class="relative">
                <span v-if="ticket.sizeMode === 'dollars'" class="absolute left-3 top-1/2 -translate-y-1/2 text-sm text-slate-500 pointer-events-none">$</span>
                <input
                  :id="`${uid}-amount`"
                  ref="amountEl"
                  v-model.trim="ticket.amount"
                  inputmode="decimal"
                  autocomplete="off"
                  :placeholder="ticket.sizeMode === 'dollars' ? '0.00' : '0'"
                  class="ui-input tabular-nums"
                  :class="ticket.sizeMode === 'dollars' ? 'pl-7' : ''"
                />
              </div>
            </div>

            <div>
              <p class="ui-field-label">Order type</p>
              <div class="ui-seg w-full grid grid-cols-2" role="group" aria-label="Order type">
                <button
                  v-for="o in [['market', 'Market'], ['limit', 'Limit']]"
                  :key="o[0]"
                  type="button"
                  class="ui-seg-btn disabled:opacity-40 disabled:cursor-default"
                  :class="ticket.orderType === o[0] ? 'ui-seg-btn-active' : ''"
                  :aria-pressed="ticket.orderType === o[0]"
                  :disabled="o[0] === 'limit' && ticket.sizeMode === 'dollars'"
                  @click="setOrderType(o[0])"
                >{{ o[1] }}</button>
              </div>
              <p v-if="ticket.sizeMode === 'dollars'" class="ui-help">Dollar amounts are market orders for the day</p>
              <div v-if="ticket.orderType === 'limit'" class="mt-3 relative">
                <label :for="`${uid}-limit`" class="sr-only">Limit price</label>
                <span class="absolute left-3 top-1/2 -translate-y-1/2 text-sm text-slate-500 pointer-events-none">$</span>
                <input
                  :id="`${uid}-limit`"
                  v-model.trim="ticket.limitPrice"
                  inputmode="decimal"
                  autocomplete="off"
                  placeholder="Limit price"
                  class="ui-input pl-7 tabular-nums"
                />
              </div>
            </div>

            <div>
              <p class="ui-field-label">Time in force</p>
              <div class="ui-seg w-full grid grid-cols-2" role="group" aria-label="Time in force">
                <button
                  v-for="f in [['day', 'Day'], ['gtc', 'Good till canceled']]"
                  :key="f[0]"
                  type="button"
                  class="ui-seg-btn disabled:opacity-40 disabled:cursor-default"
                  :class="ticket.tif === f[0] ? 'ui-seg-btn-active' : ''"
                  :aria-pressed="ticket.tif === f[0]"
                  :disabled="f[0] === 'gtc' && !gtcAllowed(ticket)"
                  @click="ticket.tif = f[0]"
                >{{ f[1] }}</button>
              </div>
              <p v-if="!gtcAllowed(ticket) && ticket.sizeMode === 'shares'" class="ui-help">Fractional shares are day orders only</p>
            </div>

            <div v-if="extendedHoursAllowed(ticket)" class="flex items-start justify-between gap-4">
              <div class="min-w-0">
                <p class="text-sm text-slate-300">Extended hours</p>
                <p class="text-xs text-slate-500 mt-0.5">Can fill before the open and after the close</p>
              </div>
              <button
                type="button"
                role="switch"
                class="ui-switch mt-0.5"
                :aria-checked="ticket.extendedHours"
                aria-label="Extended hours"
                @click="ticket.extendedHours = !ticket.extendedHours"
              ><span class="ui-switch-knob"></span></button>
            </div>

            <div class="rounded-lg border border-white/[0.07] px-4 py-3">
              <div class="flex items-baseline justify-between gap-4">
                <span class="text-sm text-slate-400">{{ estimate.label }}</span>
                <span class="text-base font-medium text-slate-100 tabular-nums">{{ estimate.amount == null ? '—' : fmtUsd(estimate.amount) }}</span>
              </div>
              <p class="text-xs text-slate-500 mt-1">{{ basis }}</p>
            </div>

            <div v-if="showIssue && issue" class="ui-error py-3" role="alert">{{ issue }}</div>
            <!-- Enter in a field submits the form -->
            <button type="submit" class="hidden" tabindex="-1" aria-hidden="true"></button>
          </form>

          <div class="ui-modal-foot shrink-0">
            <button type="button" class="ui-btn-ghost h-9 px-4" @click="emit('close')">Cancel</button>
            <button type="button" class="ui-btn-primary" :disabled="!account" @click="toReview">Review order</button>
          </div>
        </template>
      </div>
    </div>
  </Teleport>
</template>

<script setup>
import { computed, nextTick, onMounted, onUnmounted, reactive, ref, watch } from 'vue'
import { loadPositions, placeOrder } from '../../composables/useStockApi.js'
import { fmtUsd, fmtPrice } from '../../utils/dashboardAnalytics.js'
import { orderStatusDot } from '../../utils/orderBook.js'
import {
  accountModeLabel, confirmLabel, estimateBasis, extendedHoursAllowed, gtcAllowed, heldLine,
  instanceNotice, instancesTradingAccount, newClientOrderId, newTicket, orderRequestBody,
  orderStatusWord, parseDecimal, placeOrderFailure, placedOrderLine, plainQtyText, reviewRows,
  sellsMoreThanHeld, ticketEstimate, ticketIssue, typeLabel,
} from '../../utils/trade.js'

const props = defineProps({
  symbol: { type: String, required: true },
  side: { type: String, default: 'buy' },
  /** Tradable (Alpaca) accounts, live money first. */
  accounts: { type: Array, required: true },
  accountId: { type: [String, Number], default: null },
  lastPrice: { type: Number, default: null },
  instances: { type: Array, default: () => [] },
  /** Held quantities already known, by account id (0 = none). */
  heldByAccount: { type: Object, default: () => ({}) },
})
const emit = defineEmits(['close', 'placed'])

const uid = `ticket-${Math.random().toString(36).slice(2, 8)}`
const titleId = `${uid}-title`
const panelEl = ref(null)
const amountEl = ref(null)

const ticket = reactive(newTicket(props.symbol, props.side))
const accountId = ref(props.accountId ?? props.accounts[0]?.id ?? null)
const clientOrderId = newClientOrderId()   // once per ticket
const step = ref('edit')
const showIssue = ref(false)
const busy = ref(false)
const error = ref('')
const placed = ref(null)

const account = computed(() => props.accounts.find(a => String(a.id) === String(accountId.value)) || null)
const isLive = computed(() => !!account.value && !account.value.alpaca_paper)
const modeLabel = computed(() => (account.value ? accountModeLabel(account.value) : ''))

// Held quantity per account: undefined while loading, 0 when none.
const heldMap = reactive({ ...props.heldByAccount })
const held = computed(() => heldMap[accountId.value])
const heldText = computed(() => heldLine(held.value))

async function ensureHeld(id) {
  if (id == null || heldMap[id] !== undefined) return
  try {
    const book = await loadPositions(id)
    const p = book.positions.find(x => x.symbol === ticket.symbol)
    heldMap[id] = p ? p.qty : 0
  } catch {
    heldMap[id] = null
  }
}
watch(accountId, id => { ensureHeld(id); error.value = '' }, { immediate: true })

// A sell can't go past what the account holds (no shorting from the app),
// worded as on iOS. Unknown holdings don't block.
const issue = computed(() => {
  const base = ticketIssue(ticket)
  if (base || !oversell.value) return base
  const h = Math.max(0, held.value || 0)
  return h > 0
    ? `That's more than the ${plainQtyText(h)} sh you hold`
    : `You don't hold ${props.symbol} in this account`
})
const estimate = computed(() => ticketEstimate(ticket, props.lastPrice))
const basis = computed(() => estimateBasis(ticket, props.lastPrice))
const rows = computed(() => reviewRows(ticket, { account: account.value, lastPrice: props.lastPrice }))
const oversell = computed(() => held.value != null && sellsMoreThanHeld(ticket, held.value, props.lastPrice))
const notice = computed(() => instanceNotice(instancesTradingAccount(props.instances, accountId.value)))
const caveat = computed(() => (ticket.orderType === 'market'
  ? 'A market order fills at the next available price, which can differ from the estimate'
  : 'A limit order fills only at your limit or better, and may not fill at all'))

// Dollar amounts and fractional shares are market/day only: keep the ticket valid.
watch(() => gtcAllowed(ticket), ok => { if (!ok && ticket.tif === 'gtc') ticket.tif = 'day' })
watch(() => extendedHoursAllowed(ticket), ok => { if (!ok) ticket.extendedHours = false })

function setSizeMode(m) {
  if (ticket.sizeMode === m) return
  ticket.sizeMode = m
  ticket.amount = ''
  if (m === 'dollars') {
    ticket.orderType = 'market'
    ticket.limitPrice = ''
  }
  nextTick(() => amountEl.value?.focus())
}

function setOrderType(t) {
  ticket.orderType = t
  if (t === 'limit' && !ticket.limitPrice && props.lastPrice != null) {
    ticket.limitPrice = props.lastPrice >= 1 ? props.lastPrice.toFixed(2) : props.lastPrice.toFixed(4)
  }
}

function sellAll() {
  if (!(held.value > 0)) return
  ticket.sizeMode = 'shares'
  ticket.amount = plainQtyText(held.value)   // exact, not rounded
}

function toReview() {
  showIssue.value = true
  if (issue.value || !account.value) return
  error.value = ''
  step.value = 'review'
  nextTick(() => panelEl.value?.focus({ preventScroll: true }))
}

function backToEdit() {
  if (busy.value) return
  step.value = 'edit'
}

async function confirm() {
  if (busy.value || issue.value || !account.value) return
  busy.value = true
  error.value = ''
  const id = account.value.id
  const res = await placeOrder(id, orderRequestBody(ticket, clientOrderId))
  busy.value = false
  if (res.ok && res.body?.order) {
    placed.value = res.body.order
    step.value = 'result'
    emit('placed', { order: res.body.order, accountId: id })
    return
  }
  error.value = placeOrderFailure(res.status, res.body)
}

function onDismiss() {
  if (busy.value) return
  emit('close')
}

function onKey(e) {
  if (e.key === 'Escape') onDismiss()
}

onMounted(() => {
  document.addEventListener('keydown', onKey)
  nextTick(() => amountEl.value?.focus({ preventScroll: true }))
})
onUnmounted(() => document.removeEventListener('keydown', onKey))
</script>

<style scoped>
.row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  min-height: 40px;
  padding: 8px 14px;
  font-size: 14px;
  line-height: 20px;
}
.row > dt { color: #a1a1aa; min-width: 0; }
.row > dd { text-align: right; min-width: 0; overflow-wrap: anywhere; }
</style>
