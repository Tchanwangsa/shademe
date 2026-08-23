/** @type {import('tailwindcss').Config} */
module.exports = {
  content: ['./App.tsx', './src/**/*.{ts,tsx}'],
  presets: [require('nativewind/preset')],
  theme: {
    extend: {
      colors: {
        // Surfaces. Named by depth rather than by shade so the dark variants below
        // can invert the ramp without the names lying.
        ink: { DEFAULT: '#12140F', muted: '#5C5F55', soft: '#8A8D82' },
        paper: { DEFAULT: '#FFFFFF', sunken: '#F4F4F1', raised: '#FAFAF8' },
        night: { DEFAULT: '#141613', raised: '#1E211C', sunken: '#0D0F0C' },
        line: { DEFAULT: '#E4E4DF', dark: '#2C2F29' },

        // The three states a metre of walking can be in. These are the only colours
        // in the app that carry meaning, so nothing else is allowed near them.
        sun: { DEFAULT: '#C97A16', bg: '#FDF1DE', fg: '#7A4708', dark: '#E9A44B' },
        shade: { DEFAULT: '#1D9E75', bg: '#E1F5EC', fg: '#0B5540', dark: '#5DCAA5' },
        indoor: { DEFAULT: '#3B82C4', bg: '#E4EEF8', fg: '#1B4E7D', dark: '#7FB2E0' },

        route: { cool: '#1D9E75', direct: '#8A8D82' },
      },
    },
  },
  plugins: [],
};
