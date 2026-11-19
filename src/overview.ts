import { useCallback, useEffect, useMemo, useRef, useState } from "react"

export type Topic = { id: number; start: number; label: string; detail?: string }
export type OverviewVersion = { language: string; title: string; summary: string; markdown?: string; topics: Topic[] }
export type DraftTopic = { id?: number; start?: number; label: string; detail?: string }
type Draft = { language: string; title?: string; summary?: string; markdown?: string; topics: DraftTopic[] }
export type Overview = {
  preference: string; autoLanguage: string; target: string
  status: "empty" | "queued" | "ready" | "error" | "blocked"
  version: OverviewVersion | null; error: string | null
}

function prose(text: string) {
  return text.replace(/[ \t]*\[P\d+(?:\s*[,;]\s*P?\d+)*\]/g, "")
}

function displayVersion(version?: OverviewVersion | null) {
  if (!version) return null
  return {
    ...version,
    title: prose(version.title), summary: prose(version.summary), markdown: version.markdown && prose(version.markdown),
    topics: version.topics.map(topic => ({ ...topic, label: prose(topic.label), detail: topic.detail && prose(topic.detail) })),
  }
}

function parseDraft(data: string, target: string): Draft | null {
  try {
    const value = JSON.parse(data)
    if (typeof value?.language !== "string" || (target !== "und" && value.language !== target)) return null
    const field = (text: unknown) => typeof text === "string" ? prose(text).replace(/\[P\d*$/, "") : undefined
    const topics: DraftTopic[] = Array.isArray(value.topics) ? value.topics.flatMap((topic: Partial<DraftTopic> | null) => {
      const label = field(topic?.label)
      if (!label) return []
      return [{ label, detail: field(topic?.detail), id: typeof topic?.id === "number" ? topic.id : undefined,
        start: typeof topic?.start === "number" && Number.isFinite(topic.start) && topic.start >= 0 ? topic.start : undefined }]
    }) : []
    return { language: value.language, title: field(value.title), summary: field(value.summary), markdown: field(value.markdown), topics }
  } catch { return null }
}

export function useOverview(id?: string, initial?: Overview | null, visible = false) {
  const [value, setValue] = useState(initial)
  const [preview, setPreview] = useState<{ target: string; draft: Draft } | null>(null)
  const [error, setError] = useState("")
  const [requesting, setRequesting] = useState(false)
  const requestedLanguage = useRef<string | undefined>(undefined)
  const revision = useRef(0)
  const posts = useRef(Promise.resolve())
  const url = `/api/sessions/${id}/overview`
  const status = value?.status
  const target = value?.target
  useEffect(() => {
    if (!id || requesting) return
    let reading: AbortController | undefined
    let stream: EventSource | undefined
    let timer = 0
    let frame = 0
    let disposed = false
    const before = revision.current
    const current = () => !disposed && before === revision.current
    function connect(language: string) {
      if (stream || !visible || !language || !window.EventSource) return
      const events = new EventSource(`${url}/stream?language=${encodeURIComponent(language)}`)
      stream = events
      let next: Draft | null
      events.addEventListener("draft", (event: MessageEvent<string>) => {
        if (!current() || events !== stream) return
        const parsed = parseDraft(event.data, language)
        if (!parsed) return
        next = parsed
        if (!frame) frame = requestAnimationFrame(() => {
          frame = 0
          if (current() && next) setPreview({ target: language, draft: next })
        })
      })
      events.addEventListener("end", () => {
        if (!current() || events !== stream) return
        events.close()
        stream = undefined
        void poll()
      })
      // EventSource reconnects to the read-only snapshot feed. Polling also covers a disconnected writer.
    }
    async function poll() {
      window.clearTimeout(timer)
      reading?.abort()
      const request = new AbortController()
      reading = request
      try {
        const r = await fetch(url, { signal: request.signal, cache: "no-store" })
        if (!r.ok) throw Error()
        const data: Overview = await r.json()
        if (request.signal.aborted || !current()) return
        setValue(data)
        setError("")
        if (data.status !== "queued") { setPreview(null); return }
        if (target === data.target) connect(data.target)
      } catch {
        if (request.signal.aborted || !current()) return
        setError("Could not refresh the overview.")
      }
      timer = window.setTimeout(poll, stream?.readyState === 1 ? 4500 : 1400)
    }
    if (status === "queued" && target) connect(target)
    if (!status || status === "queued") timer = window.setTimeout(poll, status ? 900 : 0)
    window.addEventListener("focus", poll)
    return () => {
      window.removeEventListener("focus", poll)
      disposed = true
      reading?.abort()
      stream?.close()
      window.clearTimeout(timer)
      cancelAnimationFrame(frame)
    }
  }, [id, url, requesting, status, target, visible])

  const select = useCallback((language: string, retry = false, regenerate = false) => {
    setPreview(null)
    requestedLanguage.current = language
    const serial = ++revision.current
    setRequesting(true)
    setError("")
    posts.current = posts.current.then(async () => {
      if (serial !== revision.current) return
      try {
        const r = await fetch(url, { method: "POST", headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ language, retry, regenerate }) })
        if (!r.ok) throw Error()
        const data: Overview = await r.json()
        if (serial === revision.current) setValue(data)
      } catch {
        if (serial === revision.current) setError("Could not update the overview. Try again.")
      } finally {
        if (serial === revision.current) setRequesting(false)
      }
    })
  }, [url])
  const retry = () => select(requestedLanguage.current ?? value?.preference ?? "auto", true)
  const version = useMemo(() => displayVersion(value?.version), [value?.version])
  const regenerate = () => select(value?.preference ?? "auto", false, true)
  const draft = visible && status === "queued" && !requesting && preview?.target === target ? preview?.draft : null
  return { draft, value, version, error, select, retry, regenerate, requesting }
}
