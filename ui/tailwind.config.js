/** @type {import('tailwindcss').Config} */
export default {
  content: ['./index.html', './src/**/*.{ts,tsx}'],
  theme: {
    extend: {
      colors: {
        // Slate + indigo, modeled on Optimoz's clean monochrome look.
        // Avoid AI-gradient-mesh aesthetic on purpose.
        ink: { 900: '#0f172a', 700: '#334155', 500: '#64748b', 300: '#cbd5e1' },
        accent: { 600: '#4f46e5', 500: '#6366f1' },
        ok: '#16a34a',
        warn: '#d97706',
        bad: '#dc2626',
      },
      fontFamily: { sans: ['Inter', 'ui-sans-serif', 'system-ui'] },
    },
  },
  plugins: [],
};