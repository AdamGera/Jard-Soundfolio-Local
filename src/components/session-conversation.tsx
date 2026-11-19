import { ArrowDown, ArrowUp, X, Maximize2, Minimize2, Square, MessageCircle, ChevronRight } from "lucide-react"
import { memo, useCallback, useEffect, useLayoutEffect, useRef, useState, useSyncExternalStore } from "react"
import { useSessionChat } from "@/session-chat"
import { Dialog } from "radix-ui"
import { Button } from "./ui/button"
import { NoteMarkdown } from "./note-markdown"

type Props = {
  chat: ReturnType<typeof useSessionChat>; expanded: boolean; mobile: boolean; ready: boolean
  cite: (seconds: number) => void; expand: () => void; close: () => void
}

function SessionConversation({ chat, expanded, mobile, ready, cite, expand, close }: Props) {
  const panel = useRef<HTMLDivElement>(null)
  const area = useRef<HTMLDivElement>(null)
  const composer = useRef<HTMLTextAreaElement>(null)
  const follow = useRef(true)
  const followingCitation = useRef(false)
  const revealCitation = useCallback((seconds: number) => { followingCitation.current = true; cite(seconds) }, [cite])
  const [away, setAway] = useState(false)
  const { messages, active, pending, loaded, historyError } = chat
  const last = messages.at(-1)
  const transportError = chat.error && !(last?.role === "assistant" && last.metadata?.status)
  useLayoutEffect(() => {
    if (follow.current && area.current) area.current.scrollTop = area.current.scrollHeight
  }, [messages, expanded, mobile])
  const measureScroll = useCallback(() => {
    const el = area.current
    if (!el) return
    follow.current = el.scrollHeight - el.scrollTop - el.clientHeight < 32
    setAway(!follow.current)
  }, [])
  useEffect(() => {
    const el = area.current
    if (!el) return
    const observer = new ResizeObserver(() => {
      if (follow.current) el.scrollTop = el.scrollHeight
      measureScroll()
    })
    observer.observe(el)
    return () => observer.disconnect()
  }, [measureScroll])
  function send(text: string) {
    follow.current = true
    setAway(false)
    chat.send(text)
  }
  return <Dialog.Content ref={panel} className="conversation" data-expanded={expanded}
    aria-describedby={undefined}
    onOpenAutoFocus={event => { event.preventDefault(); (mobile ? panel.current : composer.current)?.focus({ preventScroll: true }) }}
    onCloseAutoFocus={event => { if (followingCitation.current) event.preventDefault() }}
    onInteractOutside={event => event.preventDefault()}>
    <header className="conversation-heading">
      <Dialog.Title><MessageCircle aria-hidden="true" />Chat</Dialog.Title>
      <div>
        {!mobile && <Button size="icon-sm" variant="ghost" aria-label={expanded ? "Reduce chat" : "Expand chat"}
          title={expanded ? "Reduce chat" : "Expand chat"} onClick={expand}>{expanded ? <Minimize2 /> : <Maximize2 />}</Button>}
        <Button size="icon-sm" variant="ghost" aria-label="Close chat" title="Close chat" onClick={close}><X /></Button>
      </div>
    </header>
    <div className="conversation-thread" data-more-below={away}>
      <div className="conversation-scroll" ref={area} role="log" tabIndex={0} aria-label="Chat messages" aria-live="off"
        onScroll={measureScroll}>
        {!historyError && !loaded && <p className="note-status" role="status">Opening conversation…</p>}
        {messages.map(m => <article className={`chat-turn ${m.role}`} key={m.id} aria-label={m.role === "user" ? "Your question" : "Answer"}>
          {m.parts.filter(p => p.type === "text").map((p,i) => m.role === "user"
            ? <p key={i} dir="auto">{p.text}</p>
            : <NoteMarkdown key={i} text={p.text} cite={revealCitation} ready={ready} />)}
          {m.metadata?.status === "stopped" && <small>Stopped</small>}
          {m.metadata?.status === "error" && <small>{m.metadata.error || "Answer interrupted."} {m.id === messages.at(-1)?.id && !pending &&
            <Button size="xs" variant="ghost" onClick={() => void chat.regenerate()}>Retry</Button>}</small>}
        </article>)}
        {chat.status === "submitted" && <p className="note-status" role="status"><i className="thinking-dot" /> Reading transcript…</p>}
        {(historyError || transportError) && <p className="chat-error" role="alert">
          {historyError ? "Could not load this conversation." : "The answer could not be completed."}
          <Button size="xs" variant="ghost" onClick={historyError ? chat.reload : () => void chat.regenerate()}>Retry</Button>
        </p>}
      </div>
      {away && <Button className="jump-latest" size="icon-xs" variant="outline" aria-label="Jump to latest message"
        onClick={() => { follow.current = true; if (area.current) area.current.scrollTop = area.current.scrollHeight; setAway(false) }}><ArrowDown /></Button>}
    </div>
    <footer className="conversation-footer">
      <div className="chat-starters" role="group" aria-label="Suggested questions">
        {["What did we decide?", "What was left open?"].map(text => <Button key={text} size="sm" variant="outline"
          disabled={pending || historyError} onClick={() => send(text)}>{text}</Button>)}
      </div>
      <form className="chat-composer" onSubmit={e => { e.preventDefault(); send(chat.draft) }}>
        <textarea ref={composer} placeholder="Ask anything about this recording" aria-label="Ask about this recording"
          rows={1} maxLength={4000} value={chat.draft} onChange={e => chat.setDraft(e.target.value)} dir="auto"
          onKeyDown={e => { if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) { e.preventDefault(); send(chat.draft) } }} />
        {active ? <Button type="button" size="icon-sm" aria-label="Stop answer" title="Stop answer" onClick={() => void chat.stop()}><Square fill="currentColor" /></Button>
          : <Button type="submit" size="icon-sm" disabled={!loaded || pending || historyError || !chat.draft.trim()} aria-label="Send question" title="Send question"><ArrowUp /></Button>}
      </form>
    </footer>
  </Dialog.Content>
}

const phone = window.matchMedia("(max-width: 600px)")
const subscribeToSize = (notify: () => void) => {
  phone.addEventListener("change", notify)
  return () => phone.removeEventListener("change", notify)
}

export default memo(function SessionChat({ id, open, expanded, onOpen, onExpand, ready, cite }: {
  id: string; open: boolean; expanded: boolean; onOpen: (open: boolean) => void
  onExpand: () => void; ready: boolean; cite: (seconds: number) => void
}) {
  const chat = useSessionChat(id)
  const mobile = useSyncExternalStore(subscribeToSize, () => phone.matches)
  useEffect(() => {
    const viewport = window.visualViewport
    if (!mobile || !open || !viewport) return
    const style = document.documentElement.style
    function resize() {
      style.setProperty("--chat-height", viewport!.height + "px")
      style.setProperty("--chat-top", viewport!.offsetTop + "px")
    }
    resize()
    viewport.addEventListener("resize", resize)
    viewport.addEventListener("scroll", resize)
    return () => {
      viewport.removeEventListener("resize", resize)
      viewport.removeEventListener("scroll", resize)
      style.removeProperty("--chat-height")
      style.removeProperty("--chat-top")
    }
  }, [mobile, open])
  return <Dialog.Root modal={mobile} open={open} onOpenChange={onOpen}>
    <Dialog.Trigger asChild>
      <button className="chat-launcher"><MessageCircle aria-hidden="true" />
        {chat.messages.length ? "Continue chat" : "Ask about this recording"}<ChevronRight aria-hidden="true" /></button>
    </Dialog.Trigger>
    <Dialog.Portal>
      <SessionConversation chat={chat} expanded={expanded} mobile={mobile} ready={ready}
        close={() => onOpen(false)} expand={onExpand} cite={cite} />
    </Dialog.Portal>
  </Dialog.Root>
})
