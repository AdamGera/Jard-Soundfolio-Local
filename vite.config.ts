import { resolve } from "node:path"
import tailwindcss from "@tailwindcss/vite"
import react from "@vitejs/plugin-react"
import { defineConfig } from "vite"

// https://vite.dev/config/
export default defineConfig({
  // Older running servers use the same Finder launcher as this checkout’s welcome page.
  define: { "import.meta.env.JARD_WELCOME_PAGE": JSON.stringify(process.env.JARD_WELCOME_PAGE || "") },
  plugins: [react(), tailwindcss()],
  resolve: {
    alias: {
      "@": resolve(import.meta.dirname, "./src"),
    },
  },
})
