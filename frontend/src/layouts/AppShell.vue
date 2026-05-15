<script setup>
import { computed, ref, watch, onMounted, onBeforeUnmount } from 'vue'
import { useRouter, useRoute } from 'vue-router'
import { getUser, clearSession } from '../utils/auth.js'
import { fullscreenMode } from '../composables/useFullscreen.js'
import ChatbotDock from '../components/ChatbotDock.vue'
// new-logo.png lives in /public/ — served at the root URL without a
// content hash. If the bytes change and you hit cache issues, bust
// it with a query string at the call site rather than at the import.
const fullLogoUrl = '/new-logo.png'

const router = useRouter()
const route  = useRoute()
const user   = computed(() => getUser())
const sidebarOpen = ref(false)
const isMobile = ref(false)
// fullscreenMode is a module-level shared ref — pages import it directly
// and flip it. We can't use provide/inject because pages render <AppShell>
// as their root, so the page is AppShell's parent in the component tree.

function isEditableTarget(target) {
  if (!(target instanceof HTMLElement)) return false
  const tag = String(target.tagName || '').toUpperCase()
  if (target.isContentEditable) return true
  if (target.closest?.('[contenteditable="true"]')) return true
  if (tag === 'TEXTAREA' || tag === 'SELECT') return true
  if (tag === 'INPUT') {
    return !target.readOnly && !target.hasAttribute('readonly') && !target.hasAttribute('disabled')
  }
  return false
}

function onGlobalKeydown(event) {
  if (event.key !== 'Backspace') return
  if (event.defaultPrevented) return
  if (event.ctrlKey || event.metaKey || event.altKey) return
  if (isEditableTarget(event.target)) return
  event.preventDefault()
}

function onResize() {
  isMobile.value = window.innerWidth < 1024
  if (!isMobile.value) sidebarOpen.value = false
}

function toggleSidebar() {
  sidebarOpen.value = !sidebarOpen.value
}

function closeSidebar() {
  sidebarOpen.value = false
}

function logout() {
  closeSidebar()
  clearSession()
  router.push('/login')
}

const navItems = [
  { label: 'Dashboard',  icon: 'dashboard',       to: '/dashboard'   },
  { label: 'Brokerages', icon: 'account_balance',  to: '/brokerages'  },
  { label: 'Instances',  icon: 'memory',           to: '/instances'   },
  { label: 'Backtests',  icon: 'analytics',        to: '/backtests'   },
  { label: 'Agent Runs', icon: 'smart_toy',        to: '/agent-runs'  },
  { label: 'Nexus Graph', icon: 'hub',             to: '/nexus'       },
  { label: 'Strategies',  icon: 'schema',          to: '/strategies'  },
  { label: 'Models',      icon: 'psychology',       to: '/models'      },
  { label: 'Token Usage', icon: 'payments',         to: '/token-usage' },
]

watch(
  () => route.fullPath,
  () => {
    if (isMobile.value) closeSidebar()
  }
)

onMounted(() => {
  onResize()
  window.addEventListener('resize', onResize)
  window.addEventListener('keydown', onGlobalKeydown)
})

onBeforeUnmount(() => {
  window.removeEventListener('resize', onResize)
  window.removeEventListener('keydown', onGlobalKeydown)
})
</script>

<template>
  <div class="app-shell-root min-h-screen text-slate-100 overflow-x-hidden">
    <div class="flex min-h-screen">

      <!-- Mobile overlay -->
      <div
        v-if="isMobile && sidebarOpen"
        class="fixed inset-0 z-40 bg-black/60 backdrop-blur-sm lg:hidden"
        @click="closeSidebar"
      ></div>

      <!-- Sidebar -->
      <aside
        v-show="!fullscreenMode"
        class="app-shell-sidebar w-64 lg:w-56 shrink-0 flex flex-col fixed top-0 left-0 h-screen z-50 transition-transform duration-300 ease-out"
        :class="isMobile && !sidebarOpen ? '-translate-x-full lg:translate-x-0' : 'translate-x-0'"
      >

        <!-- Brand — bundled full-logo.png is a 500×134 wordmark with
             transparent padding inside the canvas. h-12 image lands
             visible content around 32-36px tall which sits naturally
             against the nav rows below. Padding mirrors the nav's
             px-3 so the wordmark left-aligns with the row icons.
             No border-b — the empty space below the wordmark already
             reads as a section break. -->
        <div class="h-[68px] flex items-center justify-between px-3 sm:px-3 pt-2 gap-2">
          <RouterLink
            to="/"
            class="min-w-0 flex items-center group"
            @click="closeSidebar"
            aria-label="IntelliStock home"
          >
            <img
              :src="fullLogoUrl"
              alt="IntelliStock"
              width="1200"
              height="240"
              class="block h-10 w-auto max-w-full object-contain object-left transition-opacity group-hover:opacity-90"
              draggable="false"
            />
          </RouterLink>

          <button
            class="lg:hidden shrink-0 text-slate-500 hover:text-slate-200 transition-colors"
            aria-label="Close navigation"
            @click="closeSidebar"
          >
            <span class="material-symbols-outlined text-[20px]">close</span>
          </button>
        </div>

        <!-- Nav items -->
        <nav class="flex-1 py-4 px-3 space-y-1 overflow-y-auto">
          <RouterLink
            v-for="item in navItems"
            :key="item.to"
            :to="item.to"
            class="flex items-center gap-3 px-3 py-2.5 rounded-lg text-sm font-medium transition-colors"
            :class="route.path === item.to
              ? 'bg-primary/10 text-primary border border-primary/20'
              : 'text-slate-400 hover:text-slate-100 hover:bg-surface border border-transparent'"
            @click="closeSidebar"
          >
            <span class="material-symbols-outlined text-[20px]">{{ item.icon }}</span>
            {{ item.label }}
          </RouterLink>
        </nav>

        <!-- User + logout at bottom -->
        <div class="px-4 py-4 border-t border-border-subtle">
          <div class="flex items-center justify-between gap-2">
            <div class="flex items-center gap-2 min-w-0">
              <div class="size-8 shrink-0 rounded-full bg-surface border border-border-subtle flex items-center justify-center">
                <span class="material-symbols-outlined text-slate-400 text-base">person</span>
              </div>
              <div class="min-w-0">
                <p class="text-sm font-medium text-slate-300 truncate leading-none">{{ user?.username }}</p>
                <p v-if="user?.role === 'admin'" class="text-[10px] text-primary mt-0.5 font-bold uppercase tracking-widest">Admin</p>
              </div>
            </div>
            <button
              @click="logout"
              class="shrink-0 text-slate-500 hover:text-primary transition-colors"
              title="Sign out"
            >
              <span class="material-symbols-outlined text-base">logout</span>
            </button>
          </div>
        </div>
      </aside>

      <!-- Page content -->
      <div
        class="flex-1 flex flex-col min-w-0 lg:min-h-screen"
        :class="fullscreenMode ? 'lg:pl-0' : 'lg:pl-56'"
      >
        <!-- Mobile top bar -->
        <header v-show="!fullscreenMode" class="lg:hidden h-14 px-4 border-b border-border-subtle bg-[#04040c]/85 backdrop-blur-md sticky top-0 z-30 flex items-center justify-between">
          <button
            class="inline-flex items-center justify-center size-9 rounded-lg border border-border-subtle text-slate-300 hover:text-primary hover:border-primary/30 transition-colors"
            aria-label="Open navigation"
            @click="toggleSidebar"
          >
            <span class="material-symbols-outlined text-[20px]">menu</span>
          </button>
          <p class="text-sm font-semibold text-slate-300 truncate px-3">{{ user?.username }}</p>
          <button
            @click="logout"
            class="inline-flex items-center justify-center size-9 rounded-lg border border-border-subtle text-slate-500 hover:text-primary hover:border-primary/30 transition-colors"
            aria-label="Sign out"
            title="Sign out"
          >
            <span class="material-symbols-outlined text-[18px]">logout</span>
          </button>
        </header>

        <div class="flex-1 min-w-0">
          <slot />
        </div>
      </div>

    </div>

    <!-- Global chatbot dock — only on authenticated pages, hidden in fullscreen views. -->
    <ChatbotDock v-if="!fullscreenMode" />
  </div>
</template>

<style scoped>
/* Mirrors LandingView.vue's `.page` background — the deep near-black
   gradient with two soft purple radials. Keeps the dashboard chrome
   visually continuous with the marketing site instead of feeling like
   a separate slate-themed product. */
.app-shell-root {
  background:
    radial-gradient(circle at 16% 18%, rgba(118, 55, 242, 0.14), transparent 24%),
    radial-gradient(circle at 84% 82%, rgba(210, 78, 255, 0.10), transparent 22%),
    linear-gradient(180deg, #010107 0%, #02030a 46%, #04040c 100%);
}

/* Sidebar: violet-tinted vertical gradient + soft purple ambient at
   the top edge, matching the landing page's surface-card treatment.
   Right border picks up the same lavender hue as the global
   `--border-subtle` token so the seam against the page disappears
   into the radial glow. */
.app-shell-sidebar {
  background:
    radial-gradient(180% 60% at 0% 0%, rgba(118, 55, 242, 0.12) 0%, transparent 60%),
    linear-gradient(180deg, rgba(15, 10, 28, 0.88) 0%, rgba(8, 5, 18, 0.92) 100%);
  border-right: 1px solid rgba(188, 154, 255, 0.10);
  backdrop-filter: blur(14px);
  box-shadow: 22px 0 60px rgba(5, 2, 12, 0.45);
}
</style>
