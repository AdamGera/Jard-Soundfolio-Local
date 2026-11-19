import { useCallback, useEffect, useState } from "react"
import type { SavedSession } from "./audio"

export type SessionSummary = Pick<SavedSession, "id" | "name" | "created" | "status"> & {
  duration: number | null
  pendingOverview?: number
}

export function useSessionList(open: boolean, query: string, refreshKey: string | number = "") {
  const [result, setResult] = useState<{ query: string; rows: SessionSummary[]; error: string } | null>(null)
  const [version, setVersion] = useState(0)
  const refresh = useCallback(() => setVersion(value => value + 1), [])

  useEffect(() => {
    if (!open) return
    const abort = new AbortController()
    let timer: number
    async function load() {
      try {
        const response = await fetch("/api/sessions?q=" + encodeURIComponent(query.trim()), {
          signal: abort.signal, cache: "no-store",
        })
        if (!response.ok) throw new Error()
        const sessions: SessionSummary[] = await response.json()
        if (abort.signal.aborted) return
        setResult({ query, rows: sessions, error: "" })
        if (sessions.some(row => row.status === "processing" || row.pendingOverview)) {
          timer = window.setTimeout(load, 1800)
        }
      } catch {
        if (!abort.signal.aborted) {
          setResult({ query, rows: [], error: "Could not load sessions." })
        }
      }
    }
    timer = window.setTimeout(load, query.trim() ? 100 : 0)
    window.addEventListener("focus", refresh)
    return () => {
      abort.abort()
      window.clearTimeout(timer)
      window.removeEventListener("focus", refresh)
    }
  }, [open, query, refreshKey, refresh, version])

  const current = result?.query === query ? result : null
  return { rows: current?.rows ?? [], loading: !current, error: current?.error ?? "", refresh }
}
