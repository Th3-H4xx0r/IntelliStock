<script setup>
import { ref, onMounted, onUnmounted } from 'vue'
import { useRouter, useRoute } from 'vue-router'
import { login, isAuthenticated } from '../utils/auth.js'

const router   = useRouter()
const route    = useRoute()
const username = ref('')
const password = ref('')
const showPw   = ref(false)
const loading  = ref(false)
const error    = ref('')

// Redirect already-authenticated users
onMounted(() => {
  if (isAuthenticated()) router.replace('/dashboard')
})

async function submit() {
  error.value = ''
  if (!username.value.trim() || !password.value) {
    error.value = 'Please enter your username and password.'
    return
  }
  loading.value = true
  try {
    await login(username.value.trim(), password.value)
    // Open-redirect guard: only honour same-origin path-style redirects.
    // Reject protocol-relative (//attacker.com), userinfo paths (/@evil.com),
    // backslash-prefixed paths (/\evil.com), and any absolute URLs.
    const requested = route.query.redirect
    const safe = (typeof requested === 'string'
                  && requested.startsWith('/')
                  && !requested.startsWith('//')
                  && !requested.startsWith('/\\')
                  && !requested.includes('@'))
                 ? requested
                 : '/dashboard'
    router.push(safe)
  } catch (e) {
    error.value = e.message
  } finally {
    loading.value = false
  }
}

// ── Canvas particle background (same as HeroSection, lightweight version) ───
const canvasRef = ref(null)
let animId = null

onMounted(() => {
  const canvas = canvasRef.value
  if (!canvas) return
  const ctx = canvas.getContext('2d')

  function resize() {
    canvas.width  = canvas.offsetWidth
    canvas.height = canvas.offsetHeight
  }
  resize()
  window.addEventListener('resize', resize)

  const COUNT = 48
  const particles = Array.from({ length: COUNT }, () => ({
    x:  Math.random() * canvas.width,
    y:  Math.random() * canvas.height,
    vx: (Math.random() - 0.5) * 0.35,
    vy: (Math.random() - 0.5) * 0.35,
    r:  Math.random() * 1.5 + 0.5,
  }))

  function draw() {
    ctx.clearRect(0, 0, canvas.width, canvas.height)
    for (const p of particles) {
      p.x += p.vx; p.y += p.vy
      if (p.x < 0) p.x = canvas.width
      if (p.x > canvas.width) p.x = 0
      if (p.y < 0) p.y = canvas.height
      if (p.y > canvas.height) p.y = 0

      ctx.beginPath()
      ctx.arc(p.x, p.y, p.r, 0, Math.PI * 2)
      ctx.fillStyle = 'rgba(167,139,250,0.28)'
      ctx.fill()
    }
    for (let i = 0; i < COUNT; i++) {
      for (let j = i + 1; j < COUNT; j++) {
        const dx = particles[i].x - particles[j].x
        const dy = particles[i].y - particles[j].y
        const d  = Math.sqrt(dx * dx + dy * dy)
        if (d < 110) {
          ctx.beginPath()
          ctx.moveTo(particles[i].x, particles[i].y)
          ctx.lineTo(particles[j].x, particles[j].y)
          ctx.strokeStyle = `rgba(167,139,250,${(1 - d / 110) * 0.10})`
          ctx.lineWidth = 0.6
          ctx.stroke()
        }
      }
    }
    animId = requestAnimationFrame(draw)
  }
  draw()

  onUnmounted(() => {
    cancelAnimationFrame(animId)
    window.removeEventListener('resize', resize)
  })
})
</script>

<template>
  <div class="login-root min-h-screen text-slate-100 flex flex-col">

    <!-- Canvas background -->
    <canvas ref="canvasRef" class="absolute inset-0 w-full h-full pointer-events-none opacity-60"></canvas>

    <!-- Soft purple ambient glows — mirrors LandingView.vue's `.page` -->
    <div class="absolute inset-0 pointer-events-none"
         style="background:
                radial-gradient(circle at 16% 18%, rgba(118, 55, 242, 0.16), transparent 24%),
                radial-gradient(circle at 84% 82%, rgba(210, 78, 255, 0.12), transparent 22%);">
    </div>

    <!-- Top-left brand -->
    <div class="relative z-10 p-8">
      <RouterLink to="/" class="inline-flex items-center group" aria-label="IntelliStock home">
        <img
          src="/new-logo.png"
          alt="IntelliStock"
          width="1200"
          height="240"
          class="block h-10 w-auto max-w-full object-contain object-left transition-opacity group-hover:opacity-90"
          draggable="false"
        />
      </RouterLink>
    </div>

    <!-- Centered card -->
    <div class="relative z-10 flex-1 flex items-center justify-center px-4 pb-16">
      <div class="w-full max-w-sm space-y-8">

        <!-- Private access badge -->
        <div class="flex flex-col items-center text-center space-y-5">
          <div class="inline-flex items-center gap-2 px-4 py-1.5 rounded-full border border-border-subtle bg-surface/60 backdrop-blur-sm">
            <span class="material-symbols-outlined text-primary text-base">lock</span>
            <span class="text-[11px] font-bold uppercase tracking-widest text-slate-400">Private Access Only</span>
          </div>
          <div>
            <h1 class="text-3xl font-bold tracking-tight">Welcome back.</h1>
            <p class="text-slate-400 text-sm mt-2">
              IntelliStock is invite-only. Sign in with your credentials.
            </p>
          </div>
        </div>

        <!-- Form card -->
        <div class="glass-card rounded-2xl p-8 space-y-5">

          <!-- Error message -->
          <Transition name="fade-down">
            <div v-if="error"
              class="flex items-start gap-3 p-4 rounded-xl bg-red-500/10 border border-red-500/20 text-red-400 text-sm">
              <span class="material-symbols-outlined text-base shrink-0 mt-0.5">error</span>
              <span>{{ error }}</span>
            </div>
          </Transition>

          <form @submit.prevent="submit" class="space-y-4" novalidate>

            <!-- Username field -->
            <div class="space-y-1.5">
              <label for="username" class="text-xs font-semibold uppercase tracking-widest text-slate-400">Username</label>
              <div class="relative">
                <span class="material-symbols-outlined absolute left-3.5 top-1/2 -translate-y-1/2 text-slate-600 text-lg pointer-events-none">person</span>
                <input
                  id="username"
                  v-model="username"
                  type="text"
                  autocomplete="username"
                  placeholder="your username"
                  :disabled="loading"
                  class="w-full bg-background-dark border border-border-subtle rounded-xl pl-10 pr-4 py-3 text-sm text-slate-100 placeholder-slate-600
                         focus:outline-none focus:border-primary/60 focus:ring-1 focus:ring-primary/30
                         disabled:opacity-50 transition-colors"
                />
              </div>
            </div>

            <!-- Password field -->
            <div class="space-y-1.5">
              <label for="password" class="text-xs font-semibold uppercase tracking-widest text-slate-400">Password</label>
              <div class="relative">
                <span class="material-symbols-outlined absolute left-3.5 top-1/2 -translate-y-1/2 text-slate-600 text-lg pointer-events-none">lock</span>
                <input
                  id="password"
                  v-model="password"
                  :type="showPw ? 'text' : 'password'"
                  autocomplete="current-password"
                  placeholder="••••••••"
                  :disabled="loading"
                  class="w-full bg-background-dark border border-border-subtle rounded-xl pl-10 pr-11 py-3 text-sm text-slate-100 placeholder-slate-600
                         focus:outline-none focus:border-primary/60 focus:ring-1 focus:ring-primary/30
                         disabled:opacity-50 transition-colors"
                />
                <button
                  type="button"
                  tabindex="-1"
                  @click="showPw = !showPw"
                  class="absolute right-3.5 top-1/2 -translate-y-1/2 text-slate-600 hover:text-slate-300 transition-colors"
                >
                  <span class="material-symbols-outlined text-lg">{{ showPw ? 'visibility_off' : 'visibility' }}</span>
                </button>
              </div>
            </div>

            <!-- Submit -->
            <button
              type="submit"
              :disabled="loading"
              class="w-full mt-2 flex items-center justify-center gap-2.5 py-3 rounded-xl font-semibold text-sm
                     bg-primary text-background-dark hover:brightness-110 active:brightness-95
                     disabled:opacity-60 disabled:cursor-not-allowed transition-all"
            >
              <svg v-if="loading" class="size-4 animate-spin" viewBox="0 0 24 24" fill="none">
                <circle cx="12" cy="12" r="10" stroke="currentColor" stroke-width="3" stroke-dasharray="31.4" stroke-dashoffset="10" stroke-linecap="round"/>
              </svg>
              <span>{{ loading ? 'Signing in…' : 'Sign In' }}</span>
              <span v-if="!loading" class="material-symbols-outlined text-lg">arrow_forward</span>
            </button>
          </form>

        </div>

        <!-- Footer note -->
        <p class="text-center text-xs text-slate-600">
          Access is by invitation only. Contact your administrator if you need an account.
        </p>

      </div>
    </div>
  </div>
</template>

<style scoped>
/* Mirrors LandingView.vue + AppShell — deep near-black gradient base
   so the canvas particles + radial glow read on top of the same
   surface as the rest of the product. */
.login-root {
  background: linear-gradient(180deg, #010107 0%, #02030a 46%, #04040c 100%);
}

.fade-down-enter-active,
.fade-down-leave-active { transition: opacity 0.2s, transform 0.2s; }
.fade-down-enter-from   { opacity: 0; transform: translateY(-6px); }
.fade-down-leave-to     { opacity: 0; transform: translateY(-6px); }
</style>
