<script setup>
import { computed } from 'vue'
import { useRouter } from 'vue-router'
import { isAuthenticated, clearSession, getUser } from '../utils/auth.js'
import { isPreviewMode, GITHUB_URL } from '../utils/preview.js'

const router = useRouter()
const authed = computed(() => isAuthenticated())
const user   = computed(() => getUser())
const preview = isPreviewMode()

function logout() {
  clearSession()
  router.push('/login')
}
</script>

<template>
  <!-- fixed so it floats above the pinned scroll-hero -->
  <nav class="fixed top-0 left-0 right-0 z-50 border-b border-border-subtle bg-[#080b0f]/85 backdrop-blur-md">
    <div class="max-w-7xl mx-auto px-6 h-20 flex items-center justify-between">
      <RouterLink to="/" class="flex items-center" aria-label="IntelliStock home">
        <img
          src="/new-logo.png"
          alt="IntelliStock"
          width="1200"
          height="240"
          class="block h-10 w-auto max-w-full object-contain object-left"
          draggable="false"
        />
      </RouterLink>

      <div class="flex items-center gap-4">

        <!-- Preview mode: no portal exists, just a GitHub CTA -->
        <template v-if="preview">
          <a
            :href="GITHUB_URL"
            target="_blank"
            rel="noopener"
            class="inline-flex items-center gap-2 px-4 py-2 rounded-lg bg-primary text-background-dark text-sm font-bold hover:brightness-110 transition-all"
          >
            <svg viewBox="0 0 16 16" width="16" height="16" fill="currentColor" aria-hidden="true">
              <path d="M8 0C3.58 0 0 3.58 0 8c0 3.54 2.29 6.53 5.47 7.59.4.07.55-.17.55-.38 0-.19-.01-.82-.01-1.49-2.01.37-2.53-.49-2.69-.94-.09-.23-.48-.94-.82-1.13-.28-.15-.68-.52-.01-.53.63-.01 1.08.58 1.23.82.72 1.21 1.87.87 2.33.66.07-.52.28-.87.51-1.07-1.78-.2-3.64-.89-3.64-3.95 0-.87.31-1.59.82-2.15-.08-.2-.36-1.02.08-2.12 0 0 .67-.21 2.2.82.64-.18 1.32-.27 2-.27.68 0 1.36.09 2 .27 1.53-1.04 2.2-.82 2.2-.82.44 1.1.16 1.92.08 2.12.51.56.82 1.27.82 2.15 0 3.07-1.87 3.75-3.65 3.95.29.25.54.73.54 1.48 0 1.07-.01 1.93-.01 2.2 0 .21.15.46.55.38A8.01 8.01 0 0 0 16 8c0-4.42-3.58-8-8-8z"/>
            </svg>
            View on GitHub
          </a>
        </template>

        <!-- Normal mode: auth-aware controls -->
        <template v-else>
          <template v-if="authed">
            <span class="text-sm text-slate-400 hidden sm:block">{{ user?.username }}</span>
            <button
              @click="logout"
              class="px-5 py-2 text-sm font-medium hover:text-primary transition-colors"
            >Log Out</button>
          </template>

          <template v-else>
            <RouterLink
              to="/login"
              class="px-5 py-2 text-sm font-medium hover:text-primary transition-colors"
            >Log In</RouterLink>
          </template>

          <RouterLink
            v-if="authed"
            to="/dashboard"
            class="px-4 py-2 rounded-lg bg-primary text-background-dark text-sm font-bold hover:brightness-110 transition-all"
          >Dashboard</RouterLink>
          <div v-else class="px-3 py-1 rounded-full border border-border-subtle bg-surface/50 text-[10px] font-bold uppercase tracking-wider text-slate-400">
            Private Access
          </div>
        </template>
      </div>
    </div>
  </nav>
</template>
