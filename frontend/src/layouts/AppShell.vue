<script setup>
import { computed, ref, watch, onMounted, onBeforeUnmount } from 'vue'
import { useRouter, useRoute } from 'vue-router'
import { getUser, clearSession } from '../utils/auth.js'
import { fullscreenMode } from '../composables/useFullscreen.js'
import ChatbotDock from '../components/ChatbotDock.vue'
import SymbolSearch from '../components/SymbolSearch.vue'
import { openSymbolSearch, symbolSearchShortcut } from '../composables/useSymbolSearch.js'
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

// Grouped in the original order: trading desk, research tools, settings.
const navGroups = [
  { label: 'Trading', items: [
    { label: 'Dashboard',  icon: 'dashboard',       to: '/dashboard'   },
    { label: 'AI Analysis', icon: 'insights',       to: '/analysis'    },
    { label: 'Kalshi',    icon: 'sports_soccer',    to: '/kalshi'      },
    { label: 'Crypto',     icon: 'currency_bitcoin', to: '/crypto'      },
    { label: 'Brokerages', icon: 'account_balance',  to: '/brokerages'  },
    { label: 'Instances',  icon: 'memory',           to: '/instances'   },
  ] },
  { label: 'Research', items: [
    { label: 'Backtests',   icon: 'analytics',       to: '/backtests'   },
    { label: 'Agent runs',  icon: 'smart_toy',       to: '/agent-runs'  },
    { label: 'Nexus graph', icon: 'hub',             to: '/nexus'       },
    { label: 'Learning',    icon: 'lightbulb',       to: '/learning'    },
    { label: 'Strategies',  icon: 'schema',          to: '/strategies'  },
  ] },
  { label: 'Settings', items: [
    { label: 'Models',        icon: 'psychology',    to: '/models'      },
    { label: 'Token usage',   icon: 'payments',      to: '/token-usage' },
    { label: 'Notifications', icon: 'notifications', to: '/notification-settings' },
    { label: 'Users',         icon: 'group',         to: '/users'       },
  ] },
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
  <div class="app-shell-root min-h-screen bg-background-dark text-slate-100 overflow-x-hidden">
    <div class="flex min-h-screen">

      <!-- Mobile overlay -->
      <div
        v-if="isMobile && sidebarOpen"
        class="fixed inset-0 z-40 bg-black/60 lg:hidden"
        @click="closeSidebar"
      ></div>

      <!-- Sidebar -->
      <aside
        v-show="!fullscreenMode"
        class="w-64 lg:w-60 shrink-0 flex flex-col fixed top-0 left-0 h-screen z-50 bg-sidebar border-r border-white/[0.06] transition-transform duration-300 ease-out"
        :class="isMobile && !sidebarOpen ? '-translate-x-full lg:translate-x-0' : 'translate-x-0'"
      >
        <!-- Brand -->
        <div class="h-16 flex items-center justify-between px-4 gap-2">
          <RouterLink
            to="/"
            class="min-w-0 flex items-center rounded-md"
            @click="closeSidebar"
            aria-label="IntelliStock home"
          >
            <img
              :src="fullLogoUrl"
              alt="IntelliStock"
              width="1200"
              height="240"
              class="block h-8 w-auto max-w-full object-contain object-left"
              draggable="false"
            />
          </RouterLink>

          <button
            class="lg:hidden shrink-0 size-8 inline-flex items-center justify-center rounded-md text-slate-500 hover:text-slate-200 hover:bg-raised transition-colors"
            aria-label="Close navigation"
            @click="closeSidebar"
          >
            <span class="material-symbols-outlined text-[18px]">close</span>
          </button>
        </div>

        <!-- Search stocks: / or the shortcut opens it from anywhere -->
        <div class="px-3 pb-2">
          <button
            type="button"
            class="w-full h-9 px-3 flex items-center gap-2.5 rounded-lg border border-white/[0.07] bg-white/[0.02] text-sm text-slate-500 transition-colors duration-150 ease-out hover:text-slate-300 hover:border-white/[0.12] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-white/25"
            aria-label="Search stocks"
            @click="closeSidebar(); openSymbolSearch()"
          >
            <span class="material-symbols-outlined w-[18px] text-[18px] shrink-0" aria-hidden="true">search</span>
            <span class="flex-1 text-left">Search</span>
            <kbd class="h-5 px-1.5 inline-flex items-center rounded-[5px] border border-white/10 bg-raised font-sans text-[11px] leading-none text-slate-500">{{ symbolSearchShortcut }}</kbd>
          </button>
        </div>

        <!-- Nav -->
        <nav class="flex-1 px-3 pb-4 overflow-y-auto">
          <div v-for="(group, gi) in navGroups" :key="group.label" :class="gi === 0 ? 'pt-2' : 'pt-6'">
            <p class="px-3 pb-2 text-xs text-slate-500">{{ group.label }}</p>
            <div class="space-y-0.5">
              <RouterLink
                v-for="item in group.items"
                :key="item.to"
                :to="item.to"
                class="h-9 flex items-center gap-3 px-3 rounded-lg text-sm transition-colors"
                :class="route.path === item.to
                  ? 'bg-raised text-slate-100 shadow-[inset_0_1px_0_rgba(255,255,255,0.06)]'
                  : 'text-slate-400 hover:text-slate-100 hover:bg-white/[0.03]'"
                @click="closeSidebar"
              >
                <span
                  class="material-symbols-outlined w-[18px] text-[18px] shrink-0"
                  :class="route.path === item.to ? 'text-slate-200' : 'text-slate-500'"
                >{{ item.icon }}</span>
                {{ item.label }}
              </RouterLink>
            </div>
          </div>
        </nav>

        <!-- Account -->
        <div class="px-3 py-3 border-t border-white/[0.06]">
          <div class="h-10 flex items-center justify-between gap-2 px-2">
            <div class="flex items-center gap-3 min-w-0">
              <div class="size-7 shrink-0 rounded-full bg-raised flex items-center justify-center text-xs font-medium text-slate-300 uppercase">
                {{ (user?.username || '?').slice(0, 1) }}
              </div>
              <p class="text-sm text-slate-300 truncate">{{ user?.username }}</p>
            </div>
            <button
              @click="logout"
              class="shrink-0 size-8 inline-flex items-center justify-center rounded-md text-slate-500 hover:text-slate-200 hover:bg-raised transition-colors"
              title="Sign out"
              aria-label="Sign out"
            >
              <span class="material-symbols-outlined text-[18px]">logout</span>
            </button>
          </div>
        </div>
      </aside>

      <!-- Page content -->
      <div
        class="flex-1 flex flex-col min-w-0 lg:min-h-screen"
        :class="fullscreenMode ? 'lg:pl-0' : 'lg:pl-60'"
      >
        <!-- Mobile top bar -->
        <header v-show="!fullscreenMode" class="lg:hidden h-14 px-4 border-b border-white/[0.06] bg-background-dark sticky top-0 z-30 flex items-center justify-between">
          <div class="w-[72px] flex items-center">
            <button
              class="inline-flex items-center justify-center size-9 rounded-lg text-slate-300 hover:bg-raised transition-colors"
              aria-label="Open navigation"
              @click="toggleSidebar"
            >
              <span class="material-symbols-outlined text-[20px]">menu</span>
            </button>
          </div>
          <img :src="fullLogoUrl" alt="IntelliStock" class="h-7 w-auto" draggable="false" />
          <div class="w-[72px] flex items-center justify-end">
            <button
              class="inline-flex items-center justify-center size-9 rounded-lg text-slate-400 hover:text-slate-200 hover:bg-raised transition-colors"
              aria-label="Search stocks"
              title="Search"
              @click="openSymbolSearch"
            >
              <span class="material-symbols-outlined text-[20px]">search</span>
            </button>
            <button
              @click="logout"
              class="inline-flex items-center justify-center size-9 rounded-lg text-slate-500 hover:text-slate-200 hover:bg-raised transition-colors"
              aria-label="Sign out"
              title="Sign out"
            >
              <span class="material-symbols-outlined text-[18px]">logout</span>
            </button>
          </div>
        </header>

        <div class="flex-1 min-w-0">
          <slot />
        </div>
      </div>

    </div>

    <!-- Global chatbot dock — only on authenticated pages, hidden in fullscreen views. -->
    <ChatbotDock v-if="!fullscreenMode" />

    <!-- App-wide stock search (one instance; the triggers above open it). -->
    <SymbolSearch />
  </div>
</template>
