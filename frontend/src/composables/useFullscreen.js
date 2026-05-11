import { ref } from 'vue'

// Module-level shared ref. Imported by both AppShell (which renders the
// sidebar) and pages that want to hide the chrome (LiveTradingView).
// We can't use provide/inject because pages render <AppShell> as their
// root — the page is the *parent* of AppShell, so inject in the page
// would never see AppShell's provide.
export const fullscreenMode = ref(false)
