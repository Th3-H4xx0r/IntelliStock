// Shared open state for the app-wide symbol search (components/SymbolSearch.vue).
// AppShell's triggers set it; the one palette AppShell mounts reads it.
import { ref } from 'vue'

export const symbolSearchOpen = ref(false)

export function openSymbolSearch() {
  symbolSearchOpen.value = true
}

/** `⌘K` on Apple devices, `Ctrl K` elsewhere. */
export const symbolSearchShortcut =
  typeof navigator !== 'undefined' && /Mac|iPhone|iPad/.test(navigator.platform || navigator.userAgent || '')
    ? '⌘K'
    : 'Ctrl K'
