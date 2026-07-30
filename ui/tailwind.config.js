import colors from 'tailwindcss/colors';

/** @type {import('tailwindcss').Config} */
export default {
  content: ['./index.html', './src/**/*.{ts,tsx}'],
  theme: {
    extend: {
      colors: {
        // Slate + indigo, modeled on Eurskem's clean monochrome look.
        // Avoid AI-gradient-mesh aesthetic on purpose.
        //
        // Full palettes (not just the couple of shades each component
        // happens to use) so `bg-accent-50`/`text-ink-400`/etc. actually
        // compile instead of silently producing no CSS.
        ink: colors.slate,
        accent: colors.indigo,
        // Darkened one step past the raw Tailwind 600s so white text on a
        // solid ok/warn badge clears WCAG AA (4.5:1) instead of ~3.2:1.
        ok: '#15803d',
        warn: '#b45309',
        bad: '#dc2626',
      },
      fontFamily: { sans: ['DM Sans', 'Inter', 'ui-sans-serif', 'system-ui'] },
    },
  },
  plugins: [],
};
