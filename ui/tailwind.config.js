import colors from 'tailwindcss/colors';

/** @type {import('tailwindcss').Config} */
export default {
  content: ['./index.html', './src/**/*.{ts,tsx}'],
  theme: {
    extend: {
      colors: {
        // Navy + teal, per EURSKEM_UI_DESIGN_SYSTEM.md's product palette.
        // `ink` stays a cool neutral (slate already reads close to the
        // spec's navy-tinted greys); `accent` moves from indigo to teal to
        // match the new primary-action colour without having to touch
        // every `bg-accent-*`/`text-accent-*` call site individually.
        //
        // Full palettes (not just the couple of shades each component
        // happens to use) so `bg-accent-50`/`text-ink-400`/etc. actually
        // compile instead of silently producing no CSS.
        ink: colors.slate,
        accent: colors.teal,
        // Matches --status-success/--status-warning/--status-error in
        // globals.css exactly, so the flat Tailwind utility and the CSS
        // custom property never drift into two different shades of "ok".
        ok: '#128467',
        warn: '#a9650b',
        bad: '#c63b3b',
        // New execution-status semantics (see globals.css --status-*).
        // 'skipped' reuses the same tone as 'cancelled' â both read as
        // "this node didn't run", and the design system doesn't define a
        // separate skipped token.
        running: '#1689b5',
        paused: '#93620d',
        cancelled: '#5f747e',
        skipped: '#5f747e',
        brand: {
          soft: '#eaf8f6',
          softer: '#f2fbfa',
        },
      },
      fontFamily: { sans: ['Inter', 'ui-sans-serif', 'system-ui'] },
      boxShadow: {
        panel: '0 12px 36px rgba(6, 28, 42, 0.12)',
      },
    },
  },
  plugins: [],
};
