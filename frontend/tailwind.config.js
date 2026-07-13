/** @type {import('tailwindcss').Config} */
export default {
  content: ['./index.html', './src/**/*.{ts,tsx}'],
  theme: {
    extend: {
      screens: {
        // Samsung Z Fold 6 unfolded screen width is ~762px.
        // Lowering md slightly to 760px ensures it triggers the responsive layout.
        'md': '760px',
      },
    },
  },
  plugins: [],
};
