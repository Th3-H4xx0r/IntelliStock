/** @type {import('tailwindcss').Config} */
export default {
  darkMode: 'class',
  content: ['./index.html', './src/**/*.{vue,js,ts,jsx,tsx}'],
  theme: {
    extend: {
      colors: {
        // Palette mirrors the landing page (LandingView.vue): near-black
        // base with a faint purple cast, violet primary, and lavender-tinted
        // surfaces and borders. Single source of truth — every
        // `text-primary` / `bg-primary` / `border-primary` / `bg-surface`
        // utility picks this up automatically.
        primary: '#a78bfa',
        'background-light': '#f5f8f8',
        'background-dark': '#04040c',
        surface: '#0f0a1c',
        'border-subtle': '#231a3d',
      },
      fontFamily: {
        display: ['Inter', 'sans-serif'],
      },
    },
  },
  plugins: [],
}
