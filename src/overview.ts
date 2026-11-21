import { useCallback, useEffect, useMemo, useRef, useState } from "react"

export type Topic = { id: number; start: number; label: string; detail?: string }
export type OverviewVersion = { language: string; title: string; summary: string; markdown?: string; topics: Topic[] }
export type EditableOverview = OverviewVersion & { editRevision: string }
export type DraftTopic = { id?: number; start?: number; label: string; detail?: string }
const stageLabels = {
  analysis: "Analyzing transcript…",
  writing: "Writing overview…",
  title: "Writing title…",
  timeline: "Writing topic timeline…",
}

type Draft = { language: string; stage?: keyof typeof stageLabels; title?: string; summary?: string; markdown?: string; topics: DraftTopic[] }
export type WritingTab = "overview" | "timeline"
export type Overview = {
  editRevision?: string | null
  notesChanged?: boolean
  updatingPersonal?: boolean
  scope?: WritingTab | null
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
    const stage = typeof value.stage === "string" && Object.hasOwn(stageLabels, value.stage)
      ? value.stage as keyof typeof stageLabels : undefined
    return { language: value.language, stage, title: field(value.title), summary: field(value.summary), markdown: field(value.markdown), topics }
  } catch { return null }
}

export function useOverview(id?: string, initial?: Overview | null, visible = false, paused = false) {
  const [value, setValue] = useState(initial)
  const [refreshCount, refresh] = useState(0)
  const [preview, setPreview] = useState<{ target: string; draft: Draft } | null>(null)
  const [error, setError] = useState("")
  const [requesting, setRequesting] = useState(false)
  const [failedChoice, setFailedChoice] = useState<{ language: string; retry: boolean; regenerate: WritingTab | false; updatePersonal: boolean } | null>(null)
  const revision = useRef(0)
  const posts = useRef(Promise.resolve())
  const url = `/api/sessions/${id}/overview`
  const status = value?.status
  const target = value?.target
  useEffect(() => {
    if (!id || requesting || paused) return
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
        setFailedChoice(null)
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
    if (refreshCount || !status || status === "queued") timer = window.setTimeout(poll, status === "queued" ? 900 : 0)
    window.addEventListener("focus", poll)
    return () => {
      window.removeEventListener("focus", poll)
      disposed = true
      reading?.abort()
      stream?.close()
      window.clearTimeout(timer)
      cancelAnimationFrame(frame)
    }
  }, [id, url, requesting, status, target, visible, refreshCount, paused])

  const select = useCallback((language: string, retry = false, regenerate: WritingTab | false = false, updatePersonal = false) => {
    setPreview(null)
    setFailedChoice(null)
    const serial = ++revision.current
    setRequesting(true)
    setError("")
    posts.current = posts.current.then(async () => {
      if (serial !== revision.current) return
      try {
        const r = await fetch(url, { method: "POST", headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ language, retry, regenerate, updatePersonal }) })
        if (!r.ok) {
          if (r.status === 409) throw new Error((await r.json()).detail)
          throw new Error("Could not update the " + (regenerate || "overview") + ". Try again.")
        }
        const data: Overview = await r.json()
        if (serial === revision.current) setValue(data)
      } catch (reason) {
        if (serial === revision.current) {
          setFailedChoice({ language, retry, regenerate, updatePersonal })
          setError(reason instanceof Error ? reason.message : "Could not reach the local writer.")
        }
      } finally {
        if (serial === revision.current) setRequesting(false)
      }
    })
  }, [url])
  const saveEdit = async (version: EditableOverview, summary: string, markdown: string) => {
    ++revision.current
    setRequesting(true)
    try {
      const response = await fetch(url, {
        method: "PUT", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ language: version.language, revision: version.editRevision, summary, markdown }),
      })
      if (!response.ok) {
        const error = await response.json().catch(() => null)
        throw new Error(typeof error?.detail === "string" ? error.detail : "Could not save the overview. Try again.")
      }
      const saved: Overview = await response.json()
      setValue(saved)
      setError("")
      setFailedChoice(null)
    } finally {
      setRequesting(false)
    }
  }
  const retry = () => failedChoice ? select(failedChoice.language, failedChoice.retry, failedChoice.regenerate, failedChoice.updatePersonal)
    : select(value?.preference ?? "auto", true)
  const version = useMemo(() => displayVersion(value?.version), [value?.version])
  const regenerate = (tab: WritingTab) => select(value?.preference ?? "auto", false, tab)
  const draft = visible && status === "queued" && !requesting && preview?.target === target ? preview?.draft : null
  const updatePersonal = () => select(value?.preference ?? "auto", false, false, true)
  const refreshNotes = useCallback(() => refresh(count => count + 1), [])
  return { draft, saveEdit, updatePersonal, refreshNotes, errorTab: failedChoice?.regenerate || null, stageLabel: draft?.stage && stageLabels[draft.stage], value, version, error, select, retry, regenerate, requesting }
}
