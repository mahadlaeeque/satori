/** @type {import('tailwindcss').Config} */
export default {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  darkMode: "class",
  theme: {
    extend: {
      colors: {
        satori: {
          green: "#7dc243",
          teal:  "#0a9396",
          navy:  "#1f2d3d",
          ink:   "#0b1220",
          paper: "#0f172a",
          card:  "#1e293b",
          subtle:"#293548",
          muted: "#94a3b8",
        },
      },
      fontFamily: {
        sans:    ["Inter", "ui-sans-serif", "system-ui", "sans-serif"],
        serif:   ["Lora", "ui-serif", "Georgia", "serif"],
        display: ["Lora", "ui-serif", "Georgia", "serif"],
        mono:    ["ui-monospace", "monospace"],
      },
      letterSpacing: {
        tighter2: "-0.025em",
      },
      boxShadow: {
        soft: "0 6px 20px rgba(15,23,42,0.06)",
        lift: "0 20px 60px rgba(15,23,42,0.18)",
      },
    },
  },
  plugins: [],
};
