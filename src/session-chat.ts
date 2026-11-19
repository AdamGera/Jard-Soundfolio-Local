import { useChat } from "@ai-sdk/react"
import { TextStreamChatTransport, type UIMessage } from "ai"
import { useEffect, useEffectEvent, useMemo, useRef, useState } from "react"

export type ChatMessage = UIMessage<{ status?: string; error?: string }>

export function useSessionChat(id?: string) {
  const endpoint = `/api/sessions/${id}/chat`
  const [draft, updateDraft] = useState("")
  const queued = useRef<string | null>(null)
  const [loaded, setLoaded] = useState(false)
  const [historyError, setHistoryError] = useState(false)
  const [refresh, setRefresh] = useState(0)
  const transport = useMemo(() => new TextStreamChatTransport<ChatMessage>({
    api: endpoint,
    prepareSendMessagesRequest({ messages }) {
      const question = messages.findLast(m => m.role === "user")
      if (!question) throw Error("Write a question first.")
      return { body: { id: question.id, question: question.parts.filter(p => p.type === "text").map(p => p.text).join("") } }
    },
  }), [endpoint])
  const chat = useChat<ChatMessage>({
    id, transport, throttle: 45,
    onFinish: () => setRefresh(v => v + 1),
  })
  const { setMessages, status } = chat
  const active = status === "submitted" || status === "streaming"
  function submit(question: string) {
    queued.current = null
    updateDraft("")
    chat.clearError()
    void chat.sendMessage({ text: question })
  }
  useEffect(() => {
    const reload = () => setRefresh(value => value + 1)
    window.addEventListener("focus", reload)
    return () => window.removeEventListener("focus", reload)
  }, [])

  const submitQueued = useEffectEvent(submit)

  useEffect(() => {
    if (!id || active) return
    const controller = new AbortController()
    let timer: number
    async function read() {
      try {
        const r = await fetch(endpoint, { cache: "no-store", signal: controller.signal })
        if (!r.ok) throw Error()
        const history: ChatMessage[] = await r.json()
        if (controller.signal.aborted) return
        // A connection failure can happen before the server saves the question.
        setMessages(current => {
          const question = status === "error" ? current.findLast(m => m.role === "user") : undefined
          return question && !history.some(m => m.id === question.id) ? [...history, question] : history
        })
        setLoaded(true)
        setHistoryError(false)
        if (history.some(m => m.metadata?.status === "streaming")) timer = window.setTimeout(read, 650)
        else if (queued.current) submitQueued(queued.current)
      } catch {
        if (!controller.signal.aborted) setHistoryError(true)
      }
    }
    void read()
    return () => { controller.abort(); window.clearTimeout(timer) }
  }, [id, status, active, endpoint, setMessages, refresh])

  const pending = active || chat.messages.some(m => m.metadata?.status === "streaming")
  function setDraft(text: string) {
    queued.current = null
    updateDraft(text)
  }
  function send(text: string) {
    const question = text.trim()
    if (!question || pending || historyError) return
    // Honor a starter tapped while the existing local conversation is still being read.
    if (!loaded) { queued.current = question; updateDraft(question); return }
    submit(question)
  }

  return { ...chat, active, pending, loaded, historyError, draft, setDraft, send, reload: () => setRefresh(v => v + 1) }
}
