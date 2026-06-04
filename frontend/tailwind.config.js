/** @type {import('tailwindcss').Config} */
export default {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      fontFamily: {
        sans: ["Inter", "Microsoft YaHei UI", "Segoe UI", "sans-serif"],
      },
    },
  },
  plugins: [],
};

