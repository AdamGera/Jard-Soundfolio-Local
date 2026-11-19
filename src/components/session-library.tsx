import { useEffect, useRef, useState, type ReactNode } from "react"
import { AlertDialog, Dialog, Tooltip } from "radix-ui"
import { ArrowLeft, ChevronDown, FileAudio2, House, PanelLeft, Search, Settings, Trash2 } from "lucide-react"
import { readSession, type SavedSession } from "@/audio"
import { useSessionList, type SessionSummary } from "@/session-list"
import { SessionSearch } from "./session-search"
import { Button } from "./ui/button"
import "@/sidebar.css"

const compactWindow = "(max-width: 800px)"
const date = new Intl.DateTimeFormat(undefined, { month: "short", day: "numeric", year: "numeric" })

type Props = {
  busy: boolean
  currentId?: string
  onHome: () => void
  onSelect: (session: SavedSession) => void
  onDelete: (id: string) => void
  settings?: ReactNode
  children: ReactNode
}

export function SessionLibrary({ busy, currentId, onHome, onSelect, onDelete, settings, children }: Props) {
  const [compact, setCompact] = useState(() => matchMedia(compactWindow).matches)
  const [pinned, setPinned] = useState(() => {
    try { return localStorage.getItem("jard.sidebar") !== "collapsed" } catch { return true }
  })
  const [overlay, setOverlay] = useState(false)
  const expanded = compact ? overlay : pinned
  const [showSessions, setShowSessions] = useState(true)
  const [searchOpen, setSearchOpen] = useState(false)
  const [settingsOpen, setSettingsOpen] = useState(false)
  const settingsTitle = useRef<HTMLHeadingElement>(null)
  const settingsButton = useRef<HTMLButtonElement>(null)
  const [selectionError, setSelectionError] = useState("")
  const [deleting, setDeleting] = useState<SessionSummary | null>(null)
  const [deletePending, setDeletePending] = useState(false)
  const [deleteError, setDeleteError] = useState("")
  const list = useSessionList(expanded && showSessions, "", currentId ?? "")
  const request = useRef<AbortController | null>(null)
  const searchButton = useRef<HTMLButtonElement>(null)
  const toggleButton = useRef<HTMLButtonElement>(null)
  const overlayToggleButton = useRef<HTMLButtonElement>(null)
  const deleteButton = useRef<HTMLButtonElement>(null)

  useEffect(() => {
    const media = matchMedia(compactWindow)
    function resize() { setCompact(media.matches); setOverlay(false) }
    media.addEventListener("change", resize)
    return () => media.removeEventListener("change", resize)
  }, [])
  useEffect(() => () => request.current?.abort(), [])
  useEffect(() => {
    if (busy) request.current?.abort()
  }, [busy])

  function toggle() {
    if (compact) { setOverlay(!overlay); return }
    setPinned(!pinned)
    try { localStorage.setItem("jard.sidebar", pinned ? "collapsed" : "expanded") } catch { /* optional preference */ }
  }
  function search() {
    if (busy || deleting) return
    setOverlay(false)
    setSelectionError("")
    setSearchOpen(true)
  }
  useEffect(() => {
    function keydown(event: KeyboardEvent) {
      if (event.defaultPrevented || !(event.metaKey || event.ctrlKey) || event.altKey || event.repeat || event.isComposing) return
      if (event.key.toLowerCase() === "k" && !event.shiftKey) {
        if (busy || deleting || document.querySelector('[role="alertdialog"]')) return
        event.preventDefault()
        setOverlay(false)
        setSelectionError("")
        setSearchOpen(true)
      }
    }
    window.addEventListener("keydown", keydown)
    return () => window.removeEventListener("keydown", keydown)
  }, [busy, deleting])

  function openSettings() {
    request.current?.abort()
    setSelectionError("")
    setOverlay(false)
    setSettingsOpen(true)
  }
  useEffect(() => {
    if (!settingsOpen) return
    settingsTitle.current?.focus()
    const close = () => setSettingsOpen(false)
    window.addEventListener("popstate", close)
    return () => window.removeEventListener("popstate", close)
  }, [settingsOpen])

  function home() {
    if (busy) return
    request.current?.abort()
    setOverlay(false)
    setSelectionError("")
    setSettingsOpen(false)
    onHome()
  }
  async function select(id: string) {
    if (busy) return
    request.current?.abort()
    setSelectionError("")
    const abort = new AbortController()
    request.current = abort
    try {
      const session = await readSession(id, abort.signal)
      if (abort.signal.aborted) return
      setOverlay(false)
      setSettingsOpen(false)
      onSelect(session)
    } catch (error) {
      if (!abort.signal.aborted) setSelectionError(error instanceof Error ? error.message : "Could not open session.")
    }
  }
  async function remove() {
    if (!deleting || deletePending) return
    setDeletePending(true)
    try {
      const response = await fetch("/api/sessions/" + deleting.id, { method: "DELETE" })
      if (!response.ok && response.status !== 404) throw new Error((await response.json()).detail)
      onDelete(deleting.id)
      deleteButton.current = null
      setDeleting(null)
      list.refresh()
    } catch (error) {
      setDeleteError(error instanceof Error ? error.message : "Could not delete session.")
    } finally { setDeletePending(false) }
  }

  function tools(inOverlay = false) {
    const sidebarLabel = expanded ? "Hide sidebar" : "Show sidebar"
    return <div className="sidebar-tools">
      <Tooltip.Root>
        <Tooltip.Trigger asChild>
          <button ref={inOverlay ? overlayToggleButton : toggleButton} type="button" onClick={toggle}
            aria-expanded={expanded} aria-controls="workspace-sidebar" aria-label={sidebarLabel}>
            <PanelLeft aria-hidden="true" />
          </button>
        </Tooltip.Trigger>
        <Tooltip.Portal><Tooltip.Content side="bottom" sideOffset={6} className="sidebar-tip">
          {sidebarLabel}
        </Tooltip.Content></Tooltip.Portal>
      </Tooltip.Root>
      <Tooltip.Root>
        <Tooltip.Trigger asChild>
          <button ref={inOverlay ? undefined : searchButton} type="button" className="sidebar-search-trigger"
            aria-label="Search sessions" aria-keyshortcuts="Meta+K Control+K" disabled={busy} onClick={search}>
            <Search aria-hidden="true" />
          </button>
        </Tooltip.Trigger>
        <Tooltip.Portal><Tooltip.Content side="bottom" sideOffset={6} className="sidebar-tip">
          Search <kbd>{/Mac|iPhone|iPad/.test(navigator.platform) ? "⌘ K" : "Ctrl K"}</kbd>
        </Tooltip.Content></Tooltip.Portal>
      </Tooltip.Root>
    </div>
  }
  const navigation = <nav id="workspace-sidebar" aria-label="Workspace" className="sidebar-navigation">
    <button className="sidebar-home" disabled={busy} onClick={home} aria-current={!settingsOpen && !currentId ? "page" : undefined}>
      <House aria-hidden="true" />Home
    </button>
    <button className="sidebar-section" aria-expanded={showSessions} aria-controls="sidebar-sessions"
      onClick={() => setShowSessions(!showSessions)}>
      Sessions<ChevronDown aria-hidden="true" />
    </button>
    <div className="sidebar-list" id="sidebar-sessions" hidden={!showSessions}>
      {list.error ? <button className="sidebar-retry" onClick={list.refresh}>Couldn’t load sessions. Retry</button>
        : !list.rows.length && <p>{list.loading ? "Loading sessions…" : "No saved sessions yet."}</p>}
      {list.rows.map(row => <div key={row.id} className="sidebar-row" data-current={!settingsOpen && row.id === currentId}>
        <button className="library-open" disabled={busy || row.status === "processing"}
          aria-current={!settingsOpen && row.id === currentId ? "page" : undefined} title={row.name + " · " + date.format(new Date(row.created))}
          onClick={() => void select(row.id)}>
          <FileAudio2 aria-hidden="true" /><span dir="auto">{row.name}</span>
          {row.status !== "complete" && <small>{row.status === "processing" ? "Processing" : "Interrupted"}</small>}
        </button>
        <button className="sidebar-delete" aria-label={"Delete " + row.name} title="Delete session"
          disabled={busy || row.status === "processing"} onClick={event => {
            deleteButton.current = event.currentTarget
            setDeleteError("")
            setDeleting(row)
          }}>
          <Trash2 aria-hidden="true" />
        </button>
      </div>)}
    </div>
    {settings && <button type="button" ref={settingsButton} className="sidebar-home sidebar-settings" disabled={busy}
      aria-current={settingsOpen ? "page" : undefined} onClick={openSettings}>
      <Settings aria-hidden="true" />Settings
    </button>}
  </nav>

  return <Tooltip.Provider delayDuration={360} skipDelayDuration={100}><div className="workspace-frame" data-sidebar={expanded && !compact}>
    {!compact && <aside className="desktop-sidebar" inert={!expanded} aria-hidden={!expanded}>{navigation}</aside>}
    <div className="main-navigation-tools">{tools()}</div>
    <Dialog.Root open={compact && overlay} onOpenChange={setOverlay}>
      <Dialog.Portal>
        <Dialog.Overlay className="sidebar-backdrop" />
        <Dialog.Content className="compact-sidebar" aria-describedby={undefined}
          onEscapeKeyDown={event => {
            if (!deleting) return
            event.preventDefault()
            if (!deletePending) setDeleting(null)
          }}
          onInteractOutside={event => { if (deleting) event.preventDefault() }}
          onCloseAutoFocus={event => { event.preventDefault(); if (settingsOpen) settingsTitle.current?.focus(); else if (!searchOpen) toggleButton.current?.focus() }}>
          <Dialog.Title className="sr-only">Workspace sidebar</Dialog.Title>
          {tools(true)}{navigation}
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
    <div className="workspace-content" hidden={settingsOpen}>{children}</div>
    {settingsOpen && <main className="workspace-settings">
      <button type="button" className="settings-back" onClick={() => {
        setSettingsOpen(false)
        const target = expanded && !compact ? settingsButton.current : toggleButton.current
        target?.focus()
      }}><ArrowLeft aria-hidden="true" />Back</button>
      <h1 tabIndex={-1} ref={settingsTitle}>Settings</h1>
      {settings}
    </main>}
    {selectionError && <div className="navigation-error" role="alert">{selectionError}
      <button onClick={() => setSelectionError("")}>Dismiss</button></div>}
    <SessionSearch open={searchOpen && !busy} onOpenChange={open => {
      setSearchOpen(open)
      if (!open) requestAnimationFrame(() => searchButton.current?.focus())
    }} onSelect={id => void select(id)} onNew={home} />
    <AlertDialog.Root open={!!deleting} onOpenChange={open => { if (!open && !deletePending) setDeleting(null) }}>
      <AlertDialog.Portal>
        <AlertDialog.Overlay className="note-language-scrim" />
        <AlertDialog.Content className="note-language-confirm" onEscapeKeyDown={event => { if (deletePending) event.preventDefault() }}
          onCloseAutoFocus={event => {
            event.preventDefault()
            const trigger = deleteButton.current
            const next = trigger?.isConnected ? trigger : overlayToggleButton.current ?? toggleButton.current
            next?.focus()
          }}>
          <AlertDialog.Title>Delete “{deleting?.name}”?</AlertDialog.Title>
          <AlertDialog.Description>This permanently removes the recording, transcript, overviews, speaker data, and chat.</AlertDialog.Description>
          {deleteError && <p role="alert">{deleteError}</p>}
          <div className="note-language-actions">
            <AlertDialog.Cancel asChild><Button variant="ghost" size="sm" disabled={deletePending}>Cancel</Button></AlertDialog.Cancel>
            <Button variant="destructive" size="sm" disabled={deletePending} onClick={() => void remove()}>Delete permanently</Button>
          </div>
        </AlertDialog.Content>
      </AlertDialog.Portal>
    </AlertDialog.Root>
  </div></Tooltip.Provider>
}
