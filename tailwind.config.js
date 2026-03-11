/** @type {import('tailwindcss').Config} */
export default {
  content: [
    './index.html',
    './src/**/*.{ts,tsx,js,jsx}',
  ],
  theme: {
    extend: {
      colors: {
        "glass-ink": "rgba(0,0,0,0.38)",
      },
      boxShadow: {
        soft: '0 10px 30px rgba(0,0,0,0.12)',
      },
      backdropBlur: {
        xs: '2px',
      },
    },
  },
  plugins: [],
};
