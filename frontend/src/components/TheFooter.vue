<script setup>
import { GITHUB_URL } from '../utils/preview.js'
</script>

<template>
  <footer class="mt-8 border-t border-white/[0.06] bg-background-dark">
    <div class="mx-auto w-full max-w-[1200px] px-4 sm:px-6 lg:px-8 py-8 flex flex-col gap-6 sm:flex-row sm:items-center sm:justify-between">
      <div class="min-w-0">
        <img
          src="/new-logo.png"
          alt="IntelliStock"
          width="1200"
          height="240"
          class="block h-7 w-auto object-contain object-left"
          draggable="false"
        />
        <p class="mt-3 text-xs text-slate-500">
          Built with love by Pranav Krishna
          <span class="text-slate-600" aria-hidden="true">·</span>
          Free and open source
          <span class="text-slate-600" aria-hidden="true">·</span>
          <a
            href="https://github.com/Th3-H4xx0r/IntelliStock/blob/main/LICENSE"
            target="_blank"
            rel="noopener"
            class="text-slate-400 hover:text-slate-100 transition-colors duration-150 ease-out"
          >MIT licensed</a>
        </p>
      </div>

      <a
        :href="GITHUB_URL"
        target="_blank"
        rel="noopener"
        class="ui-btn self-start sm:self-auto"
      >
        <svg viewBox="0 0 16 16" width="16" height="16" fill="currentColor" aria-hidden="true">
          <path d="M8 0C3.58 0 0 3.58 0 8c0 3.54 2.29 6.53 5.47 7.59.4.07.55-.17.55-.38 0-.19-.01-.82-.01-1.49-2.01.37-2.53-.49-2.69-.94-.09-.23-.48-.94-.82-1.13-.28-.15-.68-.52-.01-.53.63-.01 1.08.58 1.23.82.72 1.21 1.87.87 2.33.66.07-.52.28-.87.51-1.07-1.78-.2-3.64-.89-3.64-3.95 0-.87.31-1.59.82-2.15-.08-.2-.36-1.02.08-2.12 0 0 .67-.21 2.2.82.64-.18 1.32-.27 2-.27.68 0 1.36.09 2 .27 1.53-1.04 2.2-.82 2.2-.82.44 1.1.16 1.92.08 2.12.51.56.82 1.27.82 2.15 0 3.07-1.87 3.75-3.65 3.95.29.25.54.73.54 1.48 0 1.07-.01 1.93-.01 2.2 0 .21.15.46.55.38A8.01 8.01 0 0 0 16 8c0-4.42-3.58-8-8-8z"/>
        </svg>
        View on GitHub
      </a>
    </div>
  </footer>
</template>
