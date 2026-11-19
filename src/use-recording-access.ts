import { useCallback, useEffect, useRef, useState } from "react"
import { captureSources, type CaptureSource, type DesktopBridge, type PermissionStatus, type RecordingMode, type RecordingPermissions } from "./desktop"

export function useRecordingAccess(native?: DesktopBridge) {
  const [permissions, setPermissions] = useState<RecordingPermissions>({})
  const [checked, setChecked] = useState(false)
  const revision = useRef({ mic: 0, system: 0 })
  const prompts = useRef<Partial<Record<CaptureSource, Promise<PermissionStatus>>>>({})
  const verifiedSystem = useRef<PermissionStatus | null>(null)

  const read = useCallback(async (source: CaptureSource, prompt: boolean) => {
    if (!native) throw Error("Recording permissions are available in the Mac app.")
    const pending = prompts.current[source]
    if (pending) return pending
    const current = ++revision.current[source]
    const query = native.request("permission", { source, prompt }).then(({ status }) => {
      // CoreGraphics can return a negative preflight for a source explicitly
      // verified by ScreenCaptureKit. Keep that verdict for this launch.
      let result = status
      if (source === "system" && !prompt && status === "unavailable" && verifiedSystem.current) {
        result = verifiedSystem.current
      }
      if (current === revision.current[source]) {
        if (source === "system") {
          if (prompt) verifiedSystem.current = status
          else if (status === "granted") verifiedSystem.current = null
        }
        setPermissions(previous => previous[source] === result ? previous : { ...previous, [source]: result })
      }
      return result
    })
    if (prompt) prompts.current[source] = query
    try { return await query }
    finally { if (prompts.current[source] === query) delete prompts.current[source] }
  }, [native])

  const check = useCallback(async (mode: RecordingMode) => {
    try {
      const rows = await Promise.all(captureSources(mode).map(async source => [source, await read(source, false)] as const))
      return Object.fromEntries(rows) as RecordingPermissions
    } finally { setChecked(true) }
  }, [read])

  useEffect(() => {
    if (!native) return
    const refresh = () => { void check("both").catch(() => { /* Manual inspection can retry a failed probe. */ }) }
    refresh()
    window.addEventListener("focus", refresh)
    return () => window.removeEventListener("focus", refresh)
  }, [check, native])

  function refused(source: CaptureSource) {
    revision.current[source]++
    if (source === "system") verifiedSystem.current = null
    setPermissions(previous => ({ ...previous, [source]: "denied" }))
  }

  return { permissions, checked, check, request: (source: CaptureSource) => read(source, true), refused }
}

export type RecordingAccess = ReturnType<typeof useRecordingAccess>
