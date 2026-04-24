import type { Config } from "tailwindcss";

export default {
  content: ["./app/**/*.{ts,tsx}", "./components/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        bg: "#0b0d10",
        panel: "#141820",
        border: "#222831",
        fg: "#e6e9ee",
        muted: "#8a93a0",
        accent: "#4ade80",
        danger: "#f87171",
        warn: "#fbbf24",
      },
    },
  },
  plugins: [],
} satisfies Config;
