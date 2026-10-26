import type { Config } from "tailwindcss";

export default {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      fontFamily: {
        sans: ["Inter", "system-ui", "-apple-system", "Segoe UI", "Roboto", "sans-serif"],
      },
      colors: {
        brand: {
          50: "#eef4ff",
          100: "#dbe6fe",
          500: "#4f6bed",
          600: "#3b55d9",
          700: "#3043b0",
        },
      },
    },
  },
  plugins: [],
} satisfies Config;
