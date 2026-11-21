import { StrictMode } from "react"
import { createRoot } from "react-dom/client"

import "./index.css"
import App from "./App.tsx"
import { desktopBridge } from "./desktop"
import { InterfaceLanguage, useDesktopInterfaceLanguage } from "./interface-language"

const colorScheme = window.matchMedia("(prefers-color-scheme: dark)")
function syncTheme() {
  document.documentElement.classList.toggle("dark", colorScheme.matches)
}
syncTheme()
colorScheme.addEventListener("change", syncTheme)

function LocalizedApp() {
  const language = useDesktopInterfaceLanguage(desktopBridge())
  return <InterfaceLanguage.Provider value={language}><App /></InterfaceLanguage.Provider>
}

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <LocalizedApp />
  </StrictMode>
)
