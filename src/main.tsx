import { StrictMode } from "react"
import { createRoot } from "react-dom/client"

import "./index.css"
import App from "./App.tsx"

const colorScheme = window.matchMedia("(prefers-color-scheme: dark)")
function syncTheme() {
  document.documentElement.classList.toggle("dark", colorScheme.matches)
  const icon = document.querySelector<HTMLLinkElement>('link[rel="icon"]')
  if (icon) icon.href = colorScheme.matches ? "/jard-dark.png" : "/jard-light.png"
}
syncTheme()
colorScheme.addEventListener("change", syncTheme)

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <App />
  </StrictMode>
)
