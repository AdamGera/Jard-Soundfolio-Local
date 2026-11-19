import { useEffect, useRef, useState } from "react"
import { Dialog } from "radix-ui"
import { AudioLines, Menu, Search, Trash2, X } from "lucide-react"
import { readSession, type SavedSession } from "@/audio"
import { clock } from "@/speaker-activity"
import { Button } from "./ui/button"

type Summary = Pick<SavedSession, "id" | "name" | "created" | "status"> & { duration: number | null; pendingOverview?: number }
const date = new Intl.DateTimeFormat(undefined, { month: "short", day: "numeric", year: "numeric", hour: "numeric", minute: "2-digit" })

export function SessionLibrary({ busy, currentId, onSelect, onDelete }: {
  busy: boolean; currentId?: string; onSelect: (session: SavedSession) => void; onDelete: (id: string) => void
}) {
  const [open, setOpen] = useState(false)
  const [query, setQuery] = useState("")
  const [rows, setRows] = useState<Summary[]>([])
  const [loading, setLoading] = useState(false)
  const [pending, setPending] = useState("")
  const [confirm, setConfirm] = useState("")
  const [error, setError] = useState("")
  const [version, setVersion] = useState(0)
  const opening = useRef<AbortController | null>(null)
  const search = useRef<HTMLInputElement>(null)
  function show(value: boolean) {
    if (!value && pending && confirm) return
    opening.current?.abort()
    setOpen(value)
    setConfirm("")
    setError("")
    setPending("")
    if (value) { setLoading(true); setRows([]); setQuery("") }
  }
  useEffect(() => {
    if (!open) return
    const abort = new AbortController()
    let timer: number
    async function refresh() {
      try {
        const response = await fetch(`/api/sessions?q=${encodeURIComponent(query.trim())}`, { signal: abort.signal, cache: "no-store" })
        if (!response.ok) throw new Error()
        const result: Summary[] = await response.json()
        if (abort.signal.aborted) return
        setRows(result)
        setLoading(false)
        if (busy || result.some(s => s.status === "processing" || !!s.pendingOverview)) timer = window.setTimeout(refresh, 1800)
      } catch {
        if (!abort.signal.aborted) { setLoading(false); setError("Could not load sessions.") }
      }
    }
    timer = window.setTimeout(refresh, query ? 120 : 0)
    return () => { abort.abort(); window.clearTimeout(timer) }
  }, [open, query, busy, version])

  async function select(id: string) {
    const abort = new AbortController()
    opening.current = abort
    setPending(id)
    setError("")
    try {
      const session = await readSession(id, abort.signal)
      if (abort.signal.aborted) return
      onSelect(session)
      show(false)
    } catch (reason) {
      if (!abort.signal.aborted) { setError(reason instanceof Error ? reason.message : "Could not open this session."); setPending("") }
    }
  }
  async function remove(id: string) {
    setPending(id)
    setError("")
    try {
      const res = await fetch(`/api/sessions/${id}`, { method: "DELETE" })
      if (!res.ok && res.status !== 404) throw new Error((await res.json()).detail)
      onDelete(id)
      setRows(items => items.filter(s => s.id !== id))
      setConfirm("")
      setVersion(n => n + 1)
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Could not delete this session.")
    } finally { setPending("") }
  }

  return <Dialog.Root open={open} onOpenChange={show}>
    <Dialog.Trigger asChild><Button variant="ghost" className="sessions-trigger"><Menu />Sessions</Button></Dialog.Trigger>
    <Dialog.Portal>
      <Dialog.Overlay className="library-overlay" />
      <Dialog.Content className="library-drawer" aria-describedby={undefined}
        onOpenAutoFocus={e => { e.preventDefault(); search.current?.focus() }}>
        <div className="library-heading"><Dialog.Title>Sessions</Dialog.Title>
          <Dialog.Close asChild><Button size="icon-sm" variant="ghost" aria-label="Close sessions"><X /></Button></Dialog.Close>
        </div>
        <label className="library-search"><Search aria-hidden="true" />
          <input ref={search} placeholder="Search sessions or transcripts" aria-label="Search sessions or transcripts"
            value={query} onChange={e => { setQuery(e.target.value); setConfirm(""); setError("") }} />
        </label>
        {error && <p role="alert" className="library-error">{error}</p>}
        <div className="library-list">
          {rows.length === 0 && !error && <p className="library-empty">{loading ? "Loading sessions…" : query ? "No matching sessions." : "Your recordings and uploads will appear here."}</p>}
          {rows.map(row => <div className="library-row" key={row.id} data-current={row.id === currentId}>
            {confirm === row.id ? <div className="delete-confirm" role="group" aria-label={`Confirm deletion of ${row.name}`}>
              <strong>Delete “{row.name}”?</strong>
              <p>Permanently removes this recording, transcript, overviews, speaker data, and chat.</p>
              <div><Button size="sm" variant="ghost" disabled={!!pending} onClick={() => setConfirm("")}>Cancel</Button>
                <Button size="sm" variant="destructive" disabled={!!pending} onClick={() => void remove(row.id)}>Delete permanently</Button></div>
            </div> : <>
              <button className="library-open" disabled={busy || !!pending || row.status === "processing"}
                onClick={() => void select(row.id)} aria-current={row.id === currentId ? "true" : undefined}>
                <span className="library-icon"><AudioLines /></span><span className="library-info"><strong dir="auto">{row.name}</strong>
                  <small>{date.format(new Date(row.created))}{row.duration != null && ` · ${clock(row.duration)}`}
                    {row.status === "processing" ? " · Processing" : row.status === "interrupted" ? " · Interrupted" : ""}</small></span>
              </button>
              <Button size="icon-sm" variant="ghost" className="library-trash" disabled={!!pending || row.status === "processing"}
                aria-label={`Delete ${row.name}`} onClick={() => setConfirm(row.id)}><Trash2 /></Button>
            </>}
          </div>)}
        </div>
      </Dialog.Content>
    </Dialog.Portal>
  </Dialog.Root>
}
