import { ChevronDown, ChevronUp, Search, X } from "lucide-react"
import { useEffect, useRef } from "react"
import { Button } from "./ui/button"

type Props = {
  open: boolean; onOpen: (open: boolean) => void
  query: string; onQuery: (query: string) => void
  count: number; index: number; step: (distance: number) => void
}

export function TranscriptFind({ open, onOpen, query, onQuery, count, index, step }: Props) {
  const input = useRef<HTMLInputElement>(null)
  const trigger = useRef<HTMLButtonElement>(null)
  useEffect(() => {
    if (open) input.current?.focus({ preventScroll: true })
  }, [open])
  function close() {
    onOpen(false)
    requestAnimationFrame(() => trigger.current?.focus({ preventScroll: true }))
  }
  return open ? <div className="transcript-find" role="search" aria-label="Find in transcript"
    onKeyDown={event => {
      if (event.key === "Escape") { event.preventDefault(); event.stopPropagation(); close() }
    }}>
    <Search aria-hidden="true" />
    <input ref={input} type="text" dir="auto" spellCheck={false} autoComplete="off"
      placeholder="Find in transcript" aria-label="Search transcript" value={query}
      onChange={event => onQuery(event.target.value)}
      onKeyDown={event => {
        if (event.key === "Enter" && !event.nativeEvent.isComposing) {
          event.preventDefault()
          step(event.shiftKey ? -1 : 1)
        }
      }} />
    {query.trim() && <span role="status" aria-label={count ? `Match ${index + 1} of ${count}` : "No matches"}>
      {count ? `${index + 1}/${count}` : "0"}</span>}
    <Button size="icon-xs" variant="ghost" disabled={count < 2} aria-label="Previous match" title="Previous match" onClick={() => step(-1)}><ChevronUp /></Button>
    <Button size="icon-xs" variant="ghost" disabled={count < 2} aria-label="Next match" title="Next match" onClick={() => step(1)}><ChevronDown /></Button>
    <Button size="icon-xs" variant="ghost" aria-label="Close search" title="Close search" onClick={close}><X /></Button>
  </div> : <Button ref={trigger} size="icon-sm" variant="ghost" className="find-trigger" aria-expanded={false}
    aria-label="Search transcript" title="Search transcript" onClick={() => onOpen(true)}><Search /></Button>
}
