import type { Config } from "tailwindcss";

const config: Config = {
  content: ["./app/**/*.{ts,tsx}", "./components/**/*.{ts,tsx}", "./lib/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        ink: "#07080A",
        graphite: "#101218",
        slate: "#171A22",
        line: "#252A36",
        text: "#E9ECF3",
        muted: "#99A1B3",
        signal: "#5FE3C0",
        beam: "#7AA2FF",
        warn: "#F2B34B",
        danger: "#FF6B6B",
        paper: "#F5F3EE",
      },
      fontFamily: {
        sans: ["var(--font-inter)", "Inter", "-apple-system", "Segoe UI", "sans-serif"],
        mono: ["var(--font-mono)", "JetBrains Mono", "ui-monospace", "monospace"],
      },
      letterSpacing: { tightest: "-0.04em" },
      boxShadow: {
        panel: "0 1px 0 0 rgba(233,236,243,0.04) inset, 0 20px 60px -32px rgba(0,0,0,0.9)",
      },
      keyframes: {
        "pulse-ring": {
          "0%": { transform: "scale(0.9)", opacity: "0.7" },
          "70%": { transform: "scale(1.6)", opacity: "0" },
          "100%": { transform: "scale(1.6)", opacity: "0" },
        },
        "fade-up": {
          "0%": { opacity: "0", transform: "translateY(6px)" },
          "100%": { opacity: "1", transform: "translateY(0)" },
        },
      },
      animation: {
        "pulse-ring": "pulse-ring 2.4s cubic-bezier(0.4,0,0.6,1) infinite",
        "fade-up": "fade-up 0.35s ease-out both",
      },
    },
  },
  plugins: [],
};

export default config;
