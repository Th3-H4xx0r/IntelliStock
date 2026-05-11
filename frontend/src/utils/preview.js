// Preview mode — when VITE_PREVIEW_MODE is set at build time, the SPA
// only ever shows the landing page. Login / dashboard / onboarding
// routes are blocked at the router; the navbar's auth controls swap
// out for a single "View on GitHub" link.
//
// Toggled via the `VITE_PREVIEW_MODE` build arg in docker-compose.yml
// (and in `.env` for `install.sh` / `install.ps1`). Set to `true` or
// `1` to enable.
//
// This lives in a tiny module so router, navbar, and landing page can
// all check the same flag without duplicating the parsing logic.

const RAW = import.meta.env.VITE_PREVIEW_MODE
export const isPreviewMode = () => RAW === 'true' || RAW === '1'

// Repo URL for the "View on GitHub" CTA. Centralised so changing the
// repo location is a one-file edit.
export const GITHUB_URL = 'https://github.com/Th3-H4xx0r/IntelliStock-V4'
