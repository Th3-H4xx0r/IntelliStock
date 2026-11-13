<script setup>
import { RouterLink } from 'vue-router'
</script>

<template>
  <div class="min-h-screen flex flex-col bg-background-dark text-slate-100">
    <header class="h-16 px-4 sm:px-8 flex items-center justify-between border-b border-white/[0.06]">
      <RouterLink to="/" class="text-base font-medium text-slate-100">IntelliStock</RouterLink>
      <nav class="flex items-center gap-6 text-sm">
        <RouterLink
          to="/"
          class="text-slate-400 hover:text-slate-100 transition-colors duration-150 ease-out"
          active-class="text-slate-100"
        >Home</RouterLink>
        <!-- Add more nav links here as you add pages -->
      </nav>
    </header>

    <main class="flex-1">
      <slot />
    </main>

    <footer class="py-4 text-center text-xs text-slate-500 border-t border-white/[0.06]">
      <span>© 2026 IntelliStock</span>
    </footer>
  </div>
</template>
