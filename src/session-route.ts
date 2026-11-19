import { desktopBridge } from "./desktop"

const sessionId = /^[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}$/i
const tabs = new Set(["overview", "timeline", "transcript", "analytics"])

const desktopLocation = "jard.lastSession"

// Restore the last document for Finder launches. An explicit URL always wins, and
// regular browser tabs continue to use their own URL and history independently.
if (desktopBridge() && !new URLSearchParams(window.location.search).has("session")) {
  try {
    const saved = JSON.parse(localStorage.getItem(desktopLocation) ?? "null") as { id: string; tab: string } | null
    if (saved && sessionId.test(saved.id) && tabs.has(saved.tab)) {
      const url = new URL(window.location.href)
      url.searchParams.set("session", saved.id)
      url.searchParams.set("tab", saved.tab)
      history.replaceState(null, "", url)
    }
  } catch { /* An old or unavailable preference opens the recorder. */ }
}

export function readSessionRoute() {
  const params = new URLSearchParams(window.location.search)
  const id = params.get("session")
  const tab = params.get("tab") ?? "overview"
  return { id: id?.toLowerCase(), invalid: id !== null && !sessionId.test(id), tab: tabs.has(tab) ? tab : "overview" }
}

export function writeSessionRoute(id: string | null, tab = "overview", replace = false) {
  const url = new URL(window.location.href)
  if (id) { url.searchParams.set("session", id); url.searchParams.set("tab", tab) }
  else { url.searchParams.delete("session"); url.searchParams.delete("tab") }
  if (desktopBridge()) {
    try {
      if (id) localStorage.setItem(desktopLocation, JSON.stringify({ id, tab }))
      else localStorage.removeItem(desktopLocation)
    } catch { /* Session URLs remain usable without preferences. */ }
  }
  if (url.href === window.location.href) return
  if (replace) history.replaceState(null, "", url)
  else history.pushState(null, "", url)
}
