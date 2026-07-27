<script setup>
import { ref, computed, onMounted, onUnmounted } from 'vue'

// ═══════════════════════════════════════════════════════════════════════════════
//  HELPERS
// ═══════════════════════════════════════════════════════════════════════════════
const easeOut = (t) => 1 - Math.pow(1 - t, 3)
const clamp01 = (v) => Math.max(0, Math.min(1, v))

// ═══════════════════════════════════════════════════════════════════════════════
//  CANVAS — ambient particle-network background
// ═══════════════════════════════════════════════════════════════════════════════
const canvasRef = ref(null)
let ctx = null, canvasRaf = null
const pts = []

function initPts(w, h) {
  pts.length = 0
  for (let i = 0; i < 72; i++) pts.push({
    x: Math.random() * w, y: Math.random() * h,
    vx: (Math.random() - 0.5) * 0.22, vy: (Math.random() - 0.5) * 0.22,
    r: Math.random() * 1.4 + 0.7, op: Math.random() * 0.35 + 0.18,
    hub: Math.random() < 0.11,
  })
}

function drawCanvas() {
  const cv = canvasRef.value; if (!cv) return
  const w = cv.width, h = cv.height
  ctx.clearRect(0, 0, w, h)
  for (const p of pts) {
    p.x += p.vx; p.y += p.vy
    if (p.x < 0) { p.x = 0; p.vx *= -1 } if (p.x > w) { p.x = w; p.vx *= -1 }
    if (p.y < 0) { p.y = 0; p.vy *= -1 } if (p.y > h) { p.y = h; p.vy *= -1 }
  }
  for (let i = 0; i < pts.length; i++) for (let j = i + 1; j < pts.length; j++) {
    const dx = pts[i].x - pts[j].x, dy = pts[i].y - pts[j].y
    const d = Math.sqrt(dx*dx + dy*dy)
    if (d < 125) {
      ctx.beginPath(); ctx.moveTo(pts[i].x, pts[i].y); ctx.lineTo(pts[j].x, pts[j].y)
      ctx.strokeStyle = `rgba(0,225,255,${((1-d/125)*0.15).toFixed(3)})`
      ctx.lineWidth = 0.55; ctx.stroke()
    }
  }
  for (const p of pts) {
    const r = p.hub ? p.r * 2.2 : p.r, op = p.hub ? Math.min(p.op*2, 0.8) : p.op
    if (p.hub) {
      ctx.beginPath(); ctx.arc(p.x, p.y, r*4, 0, Math.PI*2)
      ctx.fillStyle = 'rgba(0,225,255,0.04)'; ctx.fill()
    }
    ctx.beginPath(); ctx.arc(p.x, p.y, r, 0, Math.PI*2)
    ctx.fillStyle = `rgba(0,225,255,${op})`; ctx.fill()
  }
  canvasRaf = requestAnimationFrame(drawCanvas)
}

function resizeCanvas() {
  const cv = canvasRef.value; if (!cv) return
  cv.width = window.innerWidth; cv.height = window.innerHeight
  initPts(cv.width, cv.height)
}

// ═══════════════════════════════════════════════════════════════════════════════
//  SCROLL PROGRESS  (section is 550vh; 450vh of scrollable runway)
// ═══════════════════════════════════════════════════════════════════════════════
const sectionRef = ref(null)
const progress   = ref(0)
let   raf_scroll = false

function updateProgress() {
  if (!sectionRef.value) return
  const el = sectionRef.value
  const scrolled   = -el.getBoundingClientRect().top
  const scrollable = el.offsetHeight - window.innerHeight
  progress.value   = clamp01(scrolled / scrollable)
  raf_scroll = false
}
function onScroll() {
  if (!raf_scroll) { raf_scroll = true; requestAnimationFrame(updateProgress) }
}

// ═══════════════════════════════════════════════════════════════════════════════
//  CAROUSEL LOGIC
//
//  5 frames enter one at a time in sequence.  Each gets a "spotlight" where it
//  is flat and full-size.  As the next frame enters, the current one recedes
//  into the back-of-stack.
//
//  STARTS = when each frame's spotlight begins (after its enter transition)
//  T_IN   = duration of the enter / exit transition
//
//  slot(i) = continuous position in the stack:
//    -1  = hidden below (not yet entered)
//     0  = spotlight  (full size, flat)
//    1+  = back-of-stack (peek upward, scaled down)
//
//  slot(i) = -(1 - enterProgress(i))
//           + Σ_{k>i} enterProgress(k)   ← each later frame pushes i one back
// ═══════════════════════════════════════════════════════════════════════════════

const FRAME_COUNT = 5
//                   Dashboard  Instances  AgentRuns  Strategies  Backtest
const STARTS      = [0.12,      0.28,      0.44,      0.60,       0.76]
const T_IN        = 0.07   // transition time per frame (enter/exit)

function getSlot(i, p) {
  // Not started entering yet
  if (p < STARTS[i] - T_IN) return -1

  // Enter transition: moves slot from -1 → 0
  const enterP = easeOut(clamp01((p - (STARTS[i] - T_IN)) / T_IN))
  let slot = -(1 - enterP)

  // Each subsequent frame entering pushes this frame one slot back
  for (let k = i + 1; k < FRAME_COUNT; k++) {
    if (p >= STARTS[k] - T_IN) {
      slot += easeOut(clamp01((p - (STARTS[k] - T_IN)) / T_IN))
    }
  }
  return slot
}

// ═══════════════════════════════════════════════════════════════════════════════
//  SCROLL-DRIVEN STYLES
// ═══════════════════════════════════════════════════════════════════════════════

// ── 1. Headline + all text: fades out as first frame slides in ────────────────
const textGroupStyle = computed(() => {
  const fadeP = easeOut(clamp01(progress.value / 0.18))
  return {
    position: 'absolute', left: '50%', top: '50%',
    width: '100%', padding: '0 1.5rem',
    transform: `translateX(-50%) translateY(calc(-50% - ${(fadeP * 28).toFixed(1)}px))`,
    opacity: (1 - fadeP).toFixed(4),
    zIndex: 20,
    willChange: 'transform, opacity',
    pointerEvents: fadeP > 0.95 ? 'none' : 'auto',
  }
})

// ── 2. Scroll hint vanishes immediately ──────────────────────────────────────
const scrollHintStyle = computed(() => ({
  opacity: Math.max(0, 1 - progress.value * 10).toFixed(3),
}))

// ── 3. Stack container — slides up from bottom as first frame enters ──────────
const stackContainerStyle = computed(() => {
  const p = easeOut(clamp01((progress.value - 0.06) / 0.09))
  return {
    position: 'absolute', bottom: '0', left: '50%',
    width: '92%', maxWidth: '1100px',
    transform: `translateX(-50%) translateY(${((1 - p) * 55).toFixed(1)}%)`,
    opacity: Math.min(p * 2.5, 1).toFixed(4),
    zIndex: 10,
    perspective: '1400px',
    perspectiveOrigin: '50% 100%',
  }
})

// ── 4. Stage — flat container (no tilt; depth comes from scale + offset) ─────
const stageStyle = {
  position: 'relative',
  height: '500px',
}

// ── 5. Per-frame style ────────────────────────────────────────────────────────
function frameStyle(i) {
  const slot = getSlot(i, progress.value)

  // Completely hidden — not yet approaching
  if (slot <= -0.99) {
    return {
      position: 'absolute', top: '0', left: '0', right: '0', height: '500px',
      opacity: '0', pointerEvents: 'none', zIndex: 0,
    }
  }

  let ty, scale, opacity, zIndex, boxShadow

  if (slot < 0) {
    // Entering from below — slot -1 → 0
    const t = -slot                        // 1 = far below, 0 = spotlight
    ty      = t * 80                       // slides up 80px into position
    scale   = 1 - t * 0.08
    opacity = Math.max(0, 1 - t)
    zIndex  = 15
    boxShadow = '0 0 0 1px rgba(0,225,255,0.20), 0 -24px 80px rgba(0,0,0,0.65), 0 0 60px rgba(0,225,255,0.08)'
  } else {
    // In stack — slot 0 = spotlight, slot 1+ = increasingly behind
    ty      = -(slot * 36)                 // peek upward; 36px per slot
    scale   = 1 - slot * 0.042            // shrink slightly going back
    opacity = Math.max(0.2, 1 - slot * 0.14)
    zIndex  = Math.max(1, Math.round(14 - slot * 2))
    boxShadow = slot < 0.25
      ? '0 0 0 1px rgba(0,225,255,0.20), 0 -24px 80px rgba(0,0,0,0.65), 0 0 60px rgba(0,225,255,0.08)'
      : '0 0 0 1px rgba(255,255,255,0.07), 0 8px 32px rgba(0,0,0,0.55)'
  }

  return {
    position: 'absolute',
    top: '0', left: '0', right: '0', height: '500px',
    transform: `translateY(${ty.toFixed(2)}px) scale(${scale.toFixed(4)})`,
    transformOrigin: 'center bottom',
    opacity: opacity.toFixed(4),
    zIndex,
    borderRadius: '14px', overflow: 'hidden',
    willChange: 'transform, opacity',
    boxShadow,
  }
}

// ── 6. Label: visible only when frame is in the back stack ───────────────────
function labelStyle(i) {
  const slot = getSlot(i, progress.value)
  const opacity = slot > 0.4 ? Math.min(1, (slot - 0.4) * 5) : 0
  return { opacity: opacity.toFixed(4) }
}

// ═══════════════════════════════════════════════════════════════════════════════
//  LIFECYCLE
// ═══════════════════════════════════════════════════════════════════════════════
onMounted(async () => {
  resizeCanvas()
  ctx = canvasRef.value.getContext('2d')
  drawCanvas()
  window.addEventListener('scroll', onScroll, { passive: true })
  window.addEventListener('resize', resizeCanvas)
})
onUnmounted(() => {
  cancelAnimationFrame(canvasRaf)
  window.removeEventListener('scroll', onScroll)
  window.removeEventListener('resize', resizeCanvas)
})
</script>

<template>
  <!--
    550vh outer section = 450vh of scrollable runway.
    Phases:
      0.00 – 0.18  → text visible + fades out
      0.06 – 0.15  → stack container slides up from below
      0.12 – 0.19  → Dashboard enters spotlight
      0.19 – 0.21  → Dashboard in spotlight
      0.21 – 0.28  → Dashboard recedes, Instances enters
      ... and so on for each of the 5 frames
  -->
  <section ref="sectionRef" class="relative" style="min-height: 550vh;">
    <div class="sticky top-0 h-screen overflow-hidden" style="background: #080b0f;">

      <!-- ── Background ───────────────────────────────────────────────────── -->
      <canvas ref="canvasRef" class="absolute inset-0 z-0" />
      <div class="absolute inset-0 z-0 pointer-events-none"
        style="background: radial-gradient(60% 55% at 50% 36%, rgba(0,225,255,0.07) 0%, transparent 100%);"></div>
      <div class="absolute inset-0 z-0 pointer-events-none" style="
        background-image:
          linear-gradient(rgba(0,225,255,0.026) 1px, transparent 1px),
          linear-gradient(90deg, rgba(0,225,255,0.026) 1px, transparent 1px);
        background-size: 56px 56px;"></div>
      <div class="absolute inset-0 z-0 pointer-events-none"
        style="background: radial-gradient(ellipse 80% 80% at 50% 50%, transparent 38%, rgba(8,11,15,0.75) 100%);"></div>

      <!-- ── Centred headline + copy (fades out as frames appear) ────────── -->
      <div :style="textGroupStyle">
        <div class="text-center">

          <!-- Badge -->
          <div class="mb-7">
            <div class="inline-flex items-center gap-2 px-3 py-1.5 rounded-full
                        bg-primary/10 border border-primary/20 text-primary
                        text-[11px] font-bold uppercase tracking-widest">
              <span class="relative flex h-2 w-2">
                <span class="animate-ping absolute inset-0 rounded-full bg-primary opacity-75"></span>
                <span class="relative rounded-full h-2 w-2 bg-primary"></span>
              </span>
              Integrated with Alpaca
            </div>
          </div>

          <!-- Headline -->
          <h1 class="gradient-text font-black tracking-tight leading-[1.08] mx-auto"
              style="font-size: clamp(2.5rem, 6.6vw, 5.2rem); max-width: 840px;">
            The Intelligent Layer<br />for Quantitative Trading.
          </h1>

          <!-- Stat pills -->
          <div class="mt-6 flex items-center justify-center gap-4 flex-wrap">
            <span class="px-3 py-1 rounded-full bg-surface/60 border border-border-subtle text-xs text-slate-400 font-medium">500+ AI Signals</span>
            <span class="px-3 py-1 rounded-full bg-surface/60 border border-border-subtle text-xs text-slate-400 font-medium">S&amp;P 500 Universe</span>
            <span class="px-3 py-1 rounded-full bg-surface/60 border border-border-subtle text-xs text-slate-400 font-medium">13 Data Phases</span>
          </div>

          <!-- Subtitle -->
          <div class="mt-6">
            <p class="text-base md:text-lg text-slate-400 leading-relaxed max-w-xl mx-auto">
              Build, validate, and deploy institutional-grade strategies
              using modular AI signals. No boilerplate — just performance.
            </p>
          </div>

          <!-- CTAs -->
          <div class="mt-8 flex flex-col sm:flex-row items-center justify-center gap-4">
            <button class="px-8 py-4 bg-primary text-background-dark font-bold rounded-xl
                           flex items-center gap-2 group
                           transition-all hover:shadow-[0_0_32px_rgba(0,225,255,0.4)]
                           hover:scale-[1.02] active:scale-[0.98]">
              Start Building Now
              <span class="material-symbols-outlined group-hover:translate-x-1 transition-transform">arrow_forward</span>
            </button>
            <button class="px-8 py-4 border border-border-subtle rounded-xl text-slate-400
                           font-semibold hover:border-primary/40 hover:text-slate-200 transition-all">
              View Demo
            </button>
          </div>
        </div>
      </div>

      <!-- ── Scroll hint ──────────────────────────────────────────────────── -->
      <div :style="scrollHintStyle"
           class="absolute bottom-9 left-1/2 -translate-x-1/2 z-20
                  flex flex-col items-center gap-2 pointer-events-none">
        <span class="text-[9px] font-bold uppercase tracking-widest text-slate-600">Scroll</span>
        <div class="w-px h-8 bg-gradient-to-b from-primary/40 to-transparent animate-pulse"></div>
      </div>

      <!-- ── Spotlight carousel stack ─────────────────────────────────────── -->
      <div :style="stackContainerStyle">
        <div :style="stageStyle">

          <!-- Frame 0: Dashboard (enters first, goes furthest back) -->
          <div :style="frameStyle(0)">
            <div class="absolute top-0 inset-x-0 z-10 flex items-center px-4 py-1.5"
                 style="background: linear-gradient(to bottom, rgba(8,11,15,0.9) 0%, transparent 100%);">
              <span :style="labelStyle(0)" class="text-[8px] font-bold text-slate-500 uppercase tracking-widest">Dashboard</span>
            </div>
            <img src="/mockups/dashboard.svg" class="w-full h-full object-cover object-top select-none" draggable="false" alt="Dashboard"/>
          </div>

          <!-- Frame 1: Instances -->
          <div :style="frameStyle(1)">
            <div class="absolute top-0 inset-x-0 z-10 flex items-center px-4 py-1.5"
                 style="background: linear-gradient(to bottom, rgba(8,11,15,0.9) 0%, transparent 100%);">
              <span :style="labelStyle(1)" class="text-[8px] font-bold text-slate-500 uppercase tracking-widest">Instances</span>
            </div>
            <img src="/mockups/instances.svg" class="w-full h-full object-cover object-top select-none" draggable="false" alt="Instances"/>
          </div>

          <!-- Frame 2: Agent Runs -->
          <div :style="frameStyle(2)">
            <div class="absolute top-0 inset-x-0 z-10 flex items-center px-4 py-1.5"
                 style="background: linear-gradient(to bottom, rgba(8,11,15,0.9) 0%, transparent 100%);">
              <span :style="labelStyle(2)" class="text-[8px] font-bold text-slate-500 uppercase tracking-widest">Agent Runs</span>
            </div>
            <img src="/mockups/agent-runs.svg" class="w-full h-full object-cover object-top select-none" draggable="false" alt="Agent Runs"/>
          </div>

          <!-- Frame 3: Strategies -->
          <div :style="frameStyle(3)">
            <div class="absolute top-0 inset-x-0 z-10 flex items-center px-4 py-1.5"
                 style="background: linear-gradient(to bottom, rgba(8,11,15,0.9) 0%, transparent 100%);">
              <span :style="labelStyle(3)" class="text-[8px] font-bold text-slate-500 uppercase tracking-widest">Strategies</span>
            </div>
            <img src="/mockups/strategies.svg" class="w-full h-full object-cover object-top select-none" draggable="false" alt="Strategies"/>
          </div>

          <!-- Frame 4: Backtest Detail (enters last, stays in front) -->
          <div :style="frameStyle(4)">
            <div class="absolute top-0 inset-x-0 z-10 flex items-center px-4 py-1.5"
                 style="background: linear-gradient(to bottom, rgba(8,11,15,0.9) 0%, transparent 100%);">
              <span :style="labelStyle(4)" class="text-[8px] font-bold text-slate-500 uppercase tracking-widest">Backtest Detail</span>
            </div>
            <img src="/mockups/backtest.svg" class="w-full h-full object-cover object-top select-none" draggable="false" alt="Backtest Detail"/>
          </div>

        </div>
      </div>

      <!-- Bottom gradient -->
      <div class="absolute bottom-0 left-0 right-0 h-40 z-30 pointer-events-none"
           style="background: linear-gradient(to bottom, transparent, #080b0f);"></div>
    </div>
  </section>
</template>
