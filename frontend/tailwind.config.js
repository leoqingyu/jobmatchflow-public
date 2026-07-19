/** @type {import('tailwindcss').Config} */
export default {
  content: ["./index.html", "./src/**/*.{js,ts,jsx,tsx}"],
  theme: {
    extend: {
      fontFamily: {
        sans: ['"Inter"', "system-ui", "sans-serif"],
        display: ['"Inter"', "system-ui", "sans-serif"],
      },
      colors: {
        canvas: "#f8fafc",
        surface: "#ffffff",
        ink: "#0f172a",
        muted: "#64748b",
        line: "#e2e8f0",
        sidebar: "#0f172a",
        accent: {
          DEFAULT: "#4f46e5",
          hover: "#4338ca",
          soft: "#eef2ff",
        },
        stage: {
          applied: "#0ea5e9",
          interview: "#f59e0b",
          offer: "#10b981",
          rejected: "#f43f5e",
        },
      },
    },
  },
  plugins: [],
};
