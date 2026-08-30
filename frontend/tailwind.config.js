/** @type {import('tailwindcss').Config} */
export default {
  content: ['./index.html', './src/**/*.{ts,tsx}'],
  theme: {
    extend: {
      colors: {
        severity: {
          high: '#b3261e',
          medium: '#b56a00',
          low: '#4a4a4a',
        },
      },
    },
  },
  plugins: [],
}
