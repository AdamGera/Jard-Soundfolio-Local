const sessionId = /^[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}$/i
const tabs = new Set(["overview", "timeline", "transcript", "analytics"])

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
  if (url.href === window.location.href) return
  if (replace) history.replaceState(null, "", url)
  else history.pushState(null, "", url)
}
