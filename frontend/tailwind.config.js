import colors from 'tailwindcss/colors'

/** @type {import('tailwindcss').Config} */
export default {
  darkMode: 'class',
  content: ['./index.html', './src/**/*.{vue,js,ts,jsx,tsx}'],
  theme: {
    extend: {
      colors: {
        // App palette: one graphite ramp, no hue in the chrome. Green and
        // red carry gains and losses; nothing else on screen has a colour.
        //   background-dark  page             #0f0f10
        //   surface          card             #161617
        //   raised           active row/input #1e1e20
        //   border-subtle    hairline (~8% of primary text on the page)
        // `primary` is the off-white used for primary buttons, selection
        // and emphasis — `bg-primary text-background-dark` is the primary
        // button everywhere.
        primary: '#ededed',
        'background-light': '#fafafa',
        'background-dark': '#0f0f10',
        sidebar: '#0b0b0c',
        surface: '#161617',
        raised: '#1e1e20',
        'border-subtle': '#232326',
        gain: '#4cc38a',
        loss: '#e5484d',
        // Marketing-only violet (landing page). Never used inside the app.
        brand: '#a78bfa',
        // `slate` is blue-tinted and fights the graphite surfaces, so the
        // whole app's slate-* utilities resolve to the neutral zinc ramp.
        slate: colors.zinc,
      },
      fontFamily: {
        display: ['Inter', 'sans-serif'],
      },
      transitionTimingFunction: {
        out: 'cubic-bezier(0.22, 1, 0.36, 1)',
      },
    },
  },
  plugins: [],
}
