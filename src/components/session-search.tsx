import { useEffect, useId, useRef, useState, type KeyboardEvent } from "react"
import { AudioLines, Plus, Search } from "lucide-react"
import { Dialog } from "radix-ui"
import { useSessionList } from "@/session-list"
import { clock } from "@/speaker-activity"
import "@/session-search.css"

type SearchProps = {
  open: boolean
  onOpenChange: (open: boolean) => void
  onSelect: (id: string) => void
  onNew: () => void
}

const date = new Intl.DateTimeFormat(undefined, { month: "short", day: "numeric", year: "numeric" })
const newSession = "new-session"

export function SessionSearch({ open, onOpenChange, onSelect, onNew }: SearchProps) {
  return <Dialog.Root open={open} onOpenChange={onOpenChange}>
    <Dialog.Portal>
      <Dialog.Overlay className="session-search-overlay" />
      <SearchContent onChoose={id => {
        onOpenChange(false)
        if (id === newSession) onNew()
        else onSelect(id)
      }} />
    </Dialog.Portal>
  </Dialog.Root>
}

function SearchContent({ onChoose }: { onChoose: (id: string) => void }) {
  const [query, setQuery] = useState("")
  const [highlight, setHighlight] = useState("")
  const input = useRef<HTMLInputElement>(null)
  const list = useId()
  const { rows, loading, error, refresh } = useSessionList(true, query)
  const recent = !query.trim()
  const visible = recent ? rows.slice(0, 8) : rows
  const enabled = visible.filter(row => row.status !== "processing").map(row => row.id)
  if (recent) enabled.unshift(newSession)
  const active = enabled.includes(highlight) ? highlight : enabled[0]

  useEffect(() => {
    if (active) document.getElementById(list + active)?.scrollIntoView({ block: "nearest" })
  }, [active, list])

  function navigate(event: KeyboardEvent<HTMLInputElement>) {
    if (event.nativeEvent.isComposing || !active) return
    if (event.key === "Enter") {
      event.preventDefault()
      onChoose(active)
    } else if (event.key === "ArrowDown" || event.key === "ArrowUp") {
      event.preventDefault()
      const step = event.key === "ArrowDown" ? 1 : -1
      setHighlight(enabled[(enabled.indexOf(active) + step + enabled.length) % enabled.length])
    }
  }

  function option(id: string, disabled = false) {
    return {
      id: list + id,
      role: "option",
      "aria-selected": active === id,
      "aria-disabled": disabled || undefined,
      onPointerMove: () => { if (!disabled) setHighlight(id) },
      onMouseDown: (event: React.MouseEvent) => event.preventDefault(),
      onClick: () => { if (!disabled) onChoose(id) },
    }
  }

  return <Dialog.Content className="session-search-dialog" aria-describedby={undefined}
    onOpenAutoFocus={event => { event.preventDefault(); input.current?.focus() }}
    onCloseAutoFocus={event => event.preventDefault()}>
    <Dialog.Title className="sr-only">Search sessions</Dialog.Title>
    <div className="session-search-input">
      <Search aria-hidden="true" />
      <input ref={input} role="combobox" aria-autocomplete="list" aria-expanded="true"
        aria-label="Search sessions" aria-controls={list} aria-activedescendant={active ? list + active : undefined}
        placeholder="Search sessions…" autoComplete="off" spellCheck={false} value={query}
        onChange={event => { setHighlight(""); setQuery(event.target.value) }} onKeyDown={navigate} />
      <Dialog.Close className="session-search-escape" aria-label="Close search">esc</Dialog.Close>
    </div>
    <div className="session-search-results">
      <div id={list} role="listbox" aria-label="Sessions" aria-busy={loading}>
        {recent && <div className="session-search-option" {...option(newSession)}>
          <Plus aria-hidden="true" /><span>New session</span>
        </div>}
        {visible.length > 0 && <div role="group" aria-label={recent ? "Recent sessions" : "Matching sessions"}>
          {recent && <div aria-hidden="true" className="session-search-label">Recent sessions</div>}
          {visible.map(row => <div key={row.id} className="session-search-option" {...option(row.id, row.status === "processing")}>
            <AudioLines aria-hidden="true" />
            <span dir="auto" className="session-search-name">{row.name}</span>
            <small>{row.status === "processing" ? "Processing" :
              [date.format(new Date(row.created)), row.duration == null ? "" : clock(row.duration)].filter(Boolean).join(" · ")}</small>
          </div>)}
        </div>}
      </div>
      {error ? <div className="session-search-message" role="alert">{error} <button onClick={refresh}>Retry</button></div> :
        (loading || !visible.length) && <p className="session-search-message" role="status">
          {loading ? "Searching…" : recent ? "No recent sessions." : "No matching sessions."}
        </p>}
    </div>
  </Dialog.Content>
}
