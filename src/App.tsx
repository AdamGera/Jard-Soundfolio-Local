import { useEffect, useEffectEvent, useRef, useState, type ChangeEvent } from "react"
import { ChevronDown, FolderOpen, Mic, Plus, Square, UsersRound } from "lucide-react"

import { captureSources, desktopBridge, recordingPermissions, RECORDING_MODES, savedRecordingMode, type CaptureSource, type DesktopCaptureEvent, type RecordingDraft, type RecordingMode, type RecordingPermissions } from "./desktop"
import { RecordingAccessDialog } from "@/components/recording-permissions"
import { readSessionRoute, writeSessionRoute } from "./session-route"
import { clock, speakerLabel } from "./speaker-activity"
import { QuitButton } from "@/components/quit-button"
import { SessionLibrary } from "@/components/session-library"
import { ResultsPanel } from "@/components/results-panel"
import { Downloads, type DownloadItem } from "@/components/downloads"
import { LanguagePicker, type Language } from "@/components/language-picker"
import { Button } from "@/components/ui/button"
import { Progress } from "@/components/ui/progress"
import {
  EMPTY_TRANSCRIPT,
  livePreview,
  MAX_SECONDS,
  MissingSessionError,
  readSession,
  openMicrophone,
  transcribeMedia,
  type Microphone,
  type SavedSession,
} from "./audio"

type ModelStatus = "loading" | "ready" | "error"
type Phase = "idle" | "starting" | "recording" | "transcribing"
type ProcessingAudio = { seconds: number; duration: number | null }
type Session = {
  microphone: Microphone
  languages: string[]
  diarization: boolean
  id?: string
  preview?: ReturnType<typeof livePreview>
}

function message(error: unknown) {
  if (error instanceof DOMException && (error.name === "NotAllowedError" || error.name === "SecurityError")) {
    return "Microphone access was denied. Allow it in your browser or open an audio file."
  }
  if (error instanceof DOMException && (error.name === "NotFoundError" || error.name === "NotReadableError")) {
    return "The microphone is unavailable. Check your input device or open an audio file."
  }
  return error instanceof Error ? error.message : "Something went wrong. Try again."
}

export default function App() {
  const [quitting, setQuitting] = useState(false)
  return quitting
    ? <main className="quit-screen" role="status"><h1>Jard Soundfolio Local is shutting down.</h1><p>{desktopBridge() ? "You can close this window." : "You can close this tab."}</p></main>
    : <Workspace onQuit={() => setQuitting(true)} />
}

function Workspace({ onQuit }: { onQuit: () => void }) {
  const [status, setStatus] = useState<ModelStatus>("loading")
  const [phase, setPhase] = useState<Phase>("idle")
  const [languageOptions, setLanguageOptions] = useState<Language[]>([])
  const [selectedLanguages, setSelectedLanguages] = useState<Language[]>([])
  const [diarization, setDiarization] = useState(true)
  const [speakerLabelsAvailable, setSpeakerLabelsAvailable] = useState(false)
  const [live, setLive] = useState(true)
  const [previewPaused, setPreviewPaused] = useState(false)
  const [tab, setTab] = useState("transcript")
  const [result, setResult] = useState(EMPTY_TRANSCRIPT)
  const { text: transcript, segments } = result
  const [feedback, setFeedback] = useState("")
  const [processed, setProcessed] = useState<ProcessingAudio | null>(null)
  const [elapsed, setElapsed] = useState(0)
  const [level, setLevel] = useState(0)
  const native = desktopBridge()
  const [permissionGuide, setPermissionGuide] = useState<RecordingPermissions | null>(null)
  const [inspectingAccess, setInspectingAccess] = useState(false)
  const [refusedSource, setRefusedSource] = useState<CaptureSource | null>(null)
  const [mode, setMode] = useState<RecordingMode>(savedRecordingMode)
  const [microphoneName, setMicrophoneName] = useState("Mac default microphone")
  const [systemLevel, setSystemLevel] = useState(0)
  const [choosingFile, setChoosingFile] = useState(false)
  const [recoveredAudio, setRecoveredAudio] = useState<RecordingDraft[]>([])
  const [discarding, setDiscarding] = useState<string | null>(null)
  const nativeCapture = useRef(false)
  const nativeStopping = useRef(false)
  const nativeSession = useRef<AbortController | null>(null)
  const deviceRevision = useRef(0)
  const liveRevision = useRef(0)
  const [copied, setCopied] = useState(false)
  const fileInput = useRef<HTMLInputElement>(null)
  const [current, setCurrent] = useState<DownloadItem | null>(null)
  const [restoring, setRestoring] = useState(() => !!readSessionRoute().id)
  const restoreRequest = useRef<AbortController | null>(null)
  const pendingNavigation = useRef(false)
  const session = useRef<Session | null>(null)
  const copyTimer = useRef<number>(undefined)

  useEffect(() => {
    const endpoint = `/api/clients/${crypto.randomUUID()}`
    let connection: EventSource | undefined
    const connect = () => {
      connection?.close()
      connection = new EventSource(endpoint + "/stream")
    }
    const disconnect = () => {
      connection?.close()
      void fetch(endpoint, { method: "DELETE", keepalive: true }).catch(() => {})
    }
    connect()
    window.addEventListener("pageshow", connect)
    window.addEventListener("pagehide", disconnect)
    return () => {
      window.removeEventListener("pageshow", connect)
      window.removeEventListener("pagehide", disconnect)
      disconnect()
    }
  }, [])

  useEffect(() => {
    const abort = new AbortController()
    let timer: number
    async function check() {
      try {
        const response = await fetch("/api/status", { cache: "no-store", signal: abort.signal })
        if (!response.ok) throw new Error("Jard Soundfolio Local is unavailable.")
        const update: { status: ModelStatus; languages?: Language[]; diarization?: boolean } = await response.json()
        if (abort.signal.aborted) return
        if (update.status === "ready" && update.languages) setLanguageOptions(update.languages)
        setSpeakerLabelsAvailable(update.diarization === true)
        setStatus(update.status)
        if (update.status !== "loading") return
      } catch {
        if (abort.signal.aborted) return
        setStatus("error")
      }
      timer = window.setTimeout(check, 650)
    }
    void check()

    function releaseMic() {
      session.current?.preview?.stop()
      session.current?.microphone.close()
    }
    window.addEventListener("pagehide", releaseMic)
    return () => {
      abort.abort()
      window.clearTimeout(timer)
      window.clearTimeout(copyTimer.current)
      window.removeEventListener("pagehide", releaseMic)
      releaseMic()
    }
  }, [])

  const recording = phase === "recording"
  const busy = phase === "starting" || phase === "transcribing"
  const canStart = status === "ready" && phase === "idle" && !restoring && !choosingFile
  const preparingModel = status === "loading"
  const processingSavedAudio = phase === "idle" && current?.saved?.status === "processing"
  const working = busy || restoring || preparingModel || processingSavedAudio

  function showSession(saved: SavedSession, wanted: string) {
    const tab = saved.status === "complete" ? wanted : "transcript"
    setCurrent({ ...saved, saved })
    setResult(saved.transcript)
    setTab(tab)
    setCopied(false)
    setFeedback(saved.transcript.interruption ?? "")
    return tab
  }

  function clearSession(feedback = "") {
    setCurrent(null)
    setResult(EMPTY_TRANSCRIPT)
    setTab("transcript")
    setCopied(false)
    setFeedback(feedback)
  }

  function openSaved(saved: SavedSession, reopening = true) {
    restoreRequest.current?.abort()
    setRestoring(false)
    const tab = showSession(saved, reopening ? "overview" : "transcript")
    writeSessionRoute(saved.id, tab)
  }

  async function openAcceptedSession(id: string) {
    nativeCapture.current = false
    nativeStopping.current = false
    nativeSession.current?.abort()
    const controller = new AbortController()
    nativeSession.current = controller
    setLevel(0)
    setSystemLevel(0)
    if (!pendingNavigation.current) writeSessionRoute(id, "transcript")
    try {
      const saved = await readSession(id, controller.signal)
      if (!controller.signal.aborted && !pendingNavigation.current) openSaved(saved, false)
    } catch (error) {
      if (!controller.signal.aborted) setFeedback(message(error))
    } finally {
      if (!controller.signal.aborted) setPhase("idle")
    }
  }

  const receiveCapture = useEffectEvent((event: DesktopCaptureEvent) => {
    deviceRevision.current++
    switch (event.event) {
      case "started":
        nativeCapture.current = true
        nativeStopping.current = false
        setMode(event.mode)
        setMicrophoneName(event.microphone)
        if (!pendingNavigation.current) writeSessionRoute(null)
        clearSession()
        setPreviewPaused(false)
        setElapsed(0)
        setLevel(0)
        setSystemLevel(0)
        setPhase("recording")
        break
      case "levels":
        if (!nativeCapture.current || nativeStopping.current) return
        setElapsed(Math.floor(event.seconds))
        setLevel(Math.max(0, Math.min(1, event.mic)))
        setSystemLevel(Math.max(0, Math.min(1, event.system)))
        if (event.microphone) setMicrophoneName(event.microphone)
        break
      case "sourceChanged":
        if (event.microphone) setMicrophoneName(event.microphone)
        setFeedback(event.message)
        break
      case "preview":
        if (nativeCapture.current && live && !nativeStopping.current) {
          setResult({ ...EMPTY_TRANSCRIPT, text: event.text, segments: event.segments ?? [] })
        }
        break
      case "finished":
        nativeStopping.current = true
        setPhase("transcribing")
        if (event.reason && event.reason !== "Stopped") setFeedback(event.reason)
        break
      case "accepted":
        void openAcceptedSession(event.id)
        break
      case "warning":
        setFeedback(event.message)
        break
      case "error":
        nativeCapture.current = false
        nativeStopping.current = false
        setPhase("idle")
        if (event.permission) {
          setInspectingAccess(false)
          setRefusedSource(event.permission)
          setPermissionGuide({ [event.permission]: "denied" })
          setFeedback("")
        } else setFeedback(event.message)
        break
    }
  })

  useEffect(() => {
    if (!native) return
    let disposed = false
    const receive = (e: WindowEventMap["jard-capture"]) => receiveCapture(e.detail)
    const sync = async () => {
      const revision = deviceRevision.current
      try {
        const devices = await native.request("devices")
        if (disposed || deviceRevision.current !== revision) return
        if (devices.microphone) setMicrophoneName(devices.microphone)
        setRecoveredAudio(devices.drafts ?? [])
        if (devices.capture && !nativeStopping.current) {
          nativeCapture.current = true
          setMode(devices.capture.mode)
          setLive(devices.capture.live)
          setElapsed(Math.floor(devices.capture.seconds ?? 0))
          if (devices.capture.text) setResult({ ...EMPTY_TRANSCRIPT, text: devices.capture.text, segments: devices.capture.segments ?? [] })
          setPhase("recording")
        }
      } catch (error) {
        if (!disposed && deviceRevision.current === revision) setFeedback(message(error))
      }
    }
    window.addEventListener("jard-capture", receive)
    window.addEventListener("focus", sync)
    void sync()
    return () => {
      disposed = true
      window.removeEventListener("jard-capture", receive)
      window.removeEventListener("focus", sync)
      nativeSession.current?.abort()
    }
  }, [native])

  const restore = useEffectEvent(async () => {
    restoreRequest.current?.abort()
    if (phase !== "idle") { pendingNavigation.current = true; return }
    pendingNavigation.current = false
    const route = readSessionRoute()
    if (!route.id || route.invalid) {
      setRestoring(false)
      clearSession(route.invalid ? "This session link is invalid." : "")
      writeSessionRoute(null, "overview", true)
      return
    }
    if (current?.saved?.id === route.id) {
      const tab = current.saved.status === "complete" ? route.tab : "transcript"
      setTab(tab)
      setRestoring(false)
      writeSessionRoute(route.id, tab, true)
      return
    }
    const controller = new AbortController()
    restoreRequest.current = controller
    clearSession()
    setRestoring(true)
    try {
      const saved = await readSession(route.id, controller.signal)
      if (controller.signal.aborted) return
      writeSessionRoute(saved.id, showSession(saved, route.tab), true)
    } catch (error) {
      if (controller.signal.aborted) return
      if (error instanceof MissingSessionError) writeSessionRoute(null, "overview", true)
      setFeedback(error instanceof MissingSessionError ? error.message : "Could not open this session. Reload to try again.")
    } finally {
      if (!controller.signal.aborted) setRestoring(false)
    }
  })

  useEffect(() => {
    const navigate = () => { void restore() }
    navigate()
    window.addEventListener("popstate", navigate)
    return () => { restoreRequest.current?.abort(); window.removeEventListener("popstate", navigate) }
  }, [])
  useEffect(() => {
    if (phase === "idle" && pendingNavigation.current) void restore()
  }, [phase])

  const selectedId = current?.saved?.id
  useEffect(() => {
    if (!selectedId || phase !== "idle") return
    const controller = new AbortController()
    async function checkSelection() {
      try {
        const response = await fetch("/api/sessions", { signal: controller.signal, cache: "no-store" })
        if (!response.ok) return
        const rows: Pick<SavedSession, "id">[] = await response.json()
        if (!controller.signal.aborted && !rows.some(row => row.id === selectedId) && readSessionRoute().id === selectedId) {
          clearSession("Session no longer exists.")
          writeSessionRoute(null, "overview", true)
        }
      } catch { /* Existing content stays available across a transient disconnect. */ }
    }
    window.addEventListener("focus", checkSelection)
    return () => { controller.abort(); window.removeEventListener("focus", checkSelection) }
  }, [selectedId, phase])

  // A refresh can rejoin an offline transcription after its audio has been accepted.
  // Poll the small session index and fetch the full transcript once it is ready.
  const processingId = current?.saved?.status === "processing" ? current.id : null
  useEffect(() => {
    if (!processingId || phase !== "idle") return
    const abort = new AbortController()
    let timer: number
    async function poll() {
      try {
        const response = await fetch("/api/sessions", { signal: abort.signal, cache: "no-store" })
        if (response.ok) {
          const rows: Pick<SavedSession, "id" | "status">[] = await response.json()
          const found = rows.find(row => row.id === processingId)
          if (found?.status !== "processing") {
            if (abort.signal.aborted || readSessionRoute().id !== processingId) return
            if (found) {
              const saved = await readSession(found.id, abort.signal)
              if (!abort.signal.aborted && readSessionRoute().id === saved.id) showSession(saved, "transcript")
            } else { clearSession("Session no longer exists."); writeSessionRoute(null, "overview", true) }
            return
          }
        }
      } catch { if (abort.signal.aborted) return }
      if (!abort.signal.aborted) timer = window.setTimeout(poll, 2500)
    }
    timer = window.setTimeout(poll, 1000)
    return () => { abort.abort(); window.clearTimeout(timer) }
  }, [phase, processingId])

  function changeTab(tab: string) {
    setTab(tab)
    if (phase === "idle" && current?.saved?.status === "complete") writeSessionRoute(current.id, tab)
  }

  async function finishAudio(
    audio: Blob, languages: readonly string[],
    options: { name: string; recording?: boolean; diarization: boolean; session?: string; live?: boolean },
  ) {
    const saved = await transcribeMedia(audio, languages, (seconds, duration) => setProcessed({ seconds, duration }), options,
      id => { if (!pendingNavigation.current) writeSessionRoute(id, "transcript") })
    if (pendingNavigation.current) return
    openSaved(saved, false)
    if (saved.status === "complete" && !saved.transcript.text) setFeedback("No speech detected. Try another recording or audio file.")
  }

  async function startRecording() {
    if (!canStart) return
    setFeedback("")
    setPreviewPaused(false)
    setTab("transcript")
    setPhase("starting")
    const languages = selectedLanguages.map(({ code }) => code)
    const withSpeakers = diarization && speakerLabelsAvailable
    const id = withSpeakers ? crypto.randomUUID() : undefined
    try {
      if (native) {
        setInspectingAccess(false)
        const statuses = await recordingPermissions(native, mode)
        const sources = captureSources(mode)
        if (sources.some(source => statuses[source] !== "granted")) {
          setPermissionGuide({ ...statuses })
          for (const source of sources) {
            if (statuses[source] !== "unavailable") continue
            const permission = await native.request("permission", { source, prompt: true })
            statuses[source] = permission.status
            setPermissionGuide({ ...statuses })
          }
          if (sources.some(source => statuses[source] !== "granted")) {
            setPhase("idle")
            return
          }
        }
        setPermissionGuide(null)
        setRefusedSource(null)
        nativeCapture.current = true
        nativeStopping.current = false
        await native.request("record", { mode, live, diarization: withSpeakers, languages })
        return
      }
      const preview = live ? livePreview(languages, id, setResult, () => setPreviewPaused(true)) : undefined
      const microphone = await openMicrophone((mic, samples) => {
        const seconds = mic.length / mic.sampleRate
        setElapsed(Math.floor(seconds))
        let energy = 0
        for (const sample of samples) energy += sample * sample
        setLevel(Math.min(1, Math.sqrt(energy / samples.length) * 5))
        if (seconds >= MAX_SECONDS) {
          void stopRecording()
          return
        }
        session.current?.preview?.update(mic)
      }, () => void stopRecording())
      if (!pendingNavigation.current) writeSessionRoute(null)
      session.current = { microphone, languages, preview, diarization: withSpeakers, id }
      setElapsed(0)
      setLevel(0)
      setCopied(false)
      setCurrent(null)
      setResult(EMPTY_TRANSCRIPT)
      setPhase("recording")
    } catch (error) {
      nativeCapture.current = false
      setPermissionGuide(null)
      setFeedback(message(error))
      setPhase("idle")
    }
  }

  function toggleLive() {
    if (native && nativeCapture.current) {
      const revision = ++liveRevision.current
      void native.request("live", { enabled: !live }).catch(error => {
        if (revision === liveRevision.current) { setLive(live); setFeedback(message(error)) }
      })
    }
    const active = session.current
    if (active && live) active.preview?.stop()
    if (active && !live) {
      active.preview = livePreview(active.languages, active.id, setResult, () => setPreviewPaused(true),
        active.microphone.length, result)
    }
    setPreviewPaused(false)
    setLive(!live)
  }

  async function stopRecording() {
    if (native) {
      if (!nativeCapture.current || nativeStopping.current) return
      nativeStopping.current = true
      setTab("transcript")
      setProcessed(null)
      setPhase("transcribing")
      try {
        await native.request("stop")
      } catch (error) {
        nativeStopping.current = false
        setPhase(nativeCapture.current ? "recording" : "idle")
        setFeedback(message(error))
      }
      return
    }
    const active = session.current
    if (!active) return
    session.current = null
    setTab("transcript")
    setProcessed(null)
    setPhase("transcribing")
    try {
      active.preview?.stop()
      const audio = await active.microphone.stop()
      const item: DownloadItem = {
        id: crypto.randomUUID(),
        name: `Recording ${new Date().toLocaleString(undefined, { month: "short", day: "numeric", hour: "numeric", minute: "2-digit", second: "2-digit" })}`,
        languageHints: active.languages, recording: true, audio: audio.size ? audio : undefined,
      }
      if (item.audio) setCurrent(item)
      await finishAudio(audio, active.languages, {
        name: item.name, recording: true, live: !!active.preview, diarization: active.diarization, session: active.id,
      })
    } catch (error) {
      setFeedback(message(error))
    } finally {
      setPhase("idle")
    }
  }

  async function chooseAudio() {
    if (!native) { fileInput.current?.click(); return }
    if (!canStart) return
    setChoosingFile(true)
    setFeedback("")
    try {
      const selected = await native.request("openAudio", {
        diarization: diarization && speakerLabelsAvailable,
        languages: selectedLanguages.map(language => language.code),
      })
      if (selected?.id) await openAcceptedSession(selected.id)
    } catch (error) {
      setFeedback(message(error))
    } finally {
      setChoosingFile(false)
    }
  }

  async function handleRecovery(id: string, discard: boolean) {
    if (!native || !canStart) return
    setChoosingFile(true)
    setFeedback("")
    try {
      if (discard) await native.request("discard", { id })
      else {
        const saved = await native.request("recover", {
          id, diarization: diarization && speakerLabelsAvailable, languages: selectedLanguages.map(l => l.code),
        })
        await openAcceptedSession(saved.id)
      }
      setRecoveredAudio(drafts => drafts.filter(d => d.id !== id))
      setDiscarding(null)
    } catch (error) {
      setFeedback(message(error))
    } finally {
      setChoosingFile(false)
    }
  }

  function changeMode(value: RecordingMode) {
    setPermissionGuide(null)
    setRefusedSource(null)
    setMode(value)
    try { localStorage.setItem("jard.recordingMode", value) } catch { /* Use this choice for this window. */ }
  }

  async function showRecordingAccess() {
    if (!native) return
    try {
      const statuses = await recordingPermissions(native, mode)
      setRefusedSource(null)
      setInspectingAccess(true)
      setPermissionGuide(statuses)
    } catch (error) { setFeedback(message(error)) }
  }

  async function openFile(event: ChangeEvent<HTMLInputElement>) {
    const input = event.currentTarget
    const file = input.files?.[0]
    input.value = ""
    if (!file || !canStart) return
    setFeedback("")
    setTab("transcript")
    setProcessed(null)
    setPhase("transcribing")
    const languages = selectedLanguages.map(({ code }) => code)
    try {
      setCopied(false)
      await finishAudio(file, languages, { name: file.name, diarization: diarization && speakerLabelsAvailable })
    } catch (error) {
      setFeedback(message(error))
    } finally {
      setPhase("idle")
    }
  }

  async function copyTranscript() {
    try {
      const text = segments.length
        ? segments.map((segment) => `${speakerLabel(segment.speaker)}\n${segment.text}`).join("\n\n")
        : transcript
      await navigator.clipboard.writeText(text)
      setCopied(true)
      window.clearTimeout(copyTimer.current)
      copyTimer.current = window.setTimeout(() => setCopied(false), 1600)
    } catch {
      setFeedback("Clipboard access is unavailable. You can select and copy the text.")
    }
  }

  const recordingActivity = live && !previewPaused ? "Live preview" : "Transcribes when you stop"
  const emptyText = recording
    ? live && !previewPaused ? "Listening for speech…" : "Your transcript will appear when you stop."
    : "Record or open an audio file to get started."
  const progressLabel = restoring ? "Opening session…" : processingSavedAudio ? "Transcribing audio…" : preparingModel ? "Loading local models…"
    : phase === "starting" ? "Waiting for microphone…"
      : processed ? "Transcribing audio…" : "Preparing audio…"
  const progressValue = phase === "transcribing" && processed?.duration && processed.seconds > 0
    ? Math.min(100, processed.seconds / processed.duration * 100)
    : null
  const processingIndicator = working && (
    <div className="processing-indicator">
      <div className="processing-caption">
        <span role="status">{progressLabel}</span>
        {processed && phase === "transcribing" && (
          <span className="processing-time" aria-hidden="true">
            {clock(processed.seconds)}{processed.duration ? ` / ${clock(processed.duration)}` : ""}
          </span>
        )}
      </div>
      <Progress className="processing-track" value={progressValue} aria-label={progressLabel} />
    </div>
  )

  const viewingSession = phase === "idle" && current?.saved?.status === "complete"
  function newSession() {
    restoreRequest.current?.abort()
    setRestoring(false)
    clearSession()
    writeSessionRoute(null)
    requestAnimationFrame(() => document.querySelector<HTMLButtonElement>(".record-button")?.focus())
  }

  return (
    <SessionLibrary busy={phase !== "idle" || restoring} currentId={current?.id}
      onHome={newSession} onSelect={openSaved} onDelete={id => {
        if (current?.id !== id && readSessionRoute().id !== id) return
        restoreRequest.current?.abort()
        setRestoring(false)
        clearSession()
        writeSessionRoute(null, "overview", true)
      }}>
    <div className="soundfolio-shell" data-session={viewingSession}>
      <header className="soundfolio-header">
        <div className="wordmark">
          <picture>
            <source media="(prefers-color-scheme: dark)" srcSet="/jard-dark.png" />
            <img className="wordmark-icon" src="/jard-light.png" width="40" height="40" alt="" />
          </picture>
          <span className="wordmark-text">Jard Soundfolio Local</span>
        </div>
        <nav className="workspace-actions" aria-label="Session actions">
          {viewingSession && <Downloads item={current} compact />}
          <Button size="sm" variant="ghost" disabled={phase !== "idle" || restoring} onClick={newSession} title="New session" aria-label="New session"><Plus /><span>New session</span></Button>
          <QuitButton recording={recording || phase === "starting"} onQuit={onQuit} />
        </nav>
      </header>

      <main className="workspace" data-session={viewingSession}>
        <ResultsPanel key={phase === "idle" && current ? current.id : "capture"}
          session={phase === "idle" ? current?.saved : undefined} result={result} source={phase === "idle" && current ? current.audioUrl ?? current.audio ?? null : null}
          tab={tab} onTab={changeTab} working={working} recording={recording}
          emptyText={emptyText} progress={processingIndicator} copied={copied} onCopy={() => void copyTranscript()} />

        {viewingSession && feedback && <p className="session-feedback feedback" role="alert">{feedback}</p>}
        {!viewingSession && <><div className="controls">
          <div className="control-dock" role="group" aria-label="Audio controls">
            <Button
              className="record-button"
              data-recording={recording}
              disabled={!recording && !canStart}
              onClick={() => void (recording ? stopRecording() : startRecording())}
            >
              {recording ? <Square fill="currentColor" /> : <Mic />}
              {recording ? "Stop" : "Record"}
            </Button>
            <Button
              variant="ghost"
              className="dock-button"
              disabled={!canStart}
              onClick={() => void chooseAudio()}
            >
              <FolderOpen />
              Open audio
            </Button>
            <span className="dock-divider" aria-hidden="true" />
            <Button
              variant="ghost"
              className="dock-button live-button"
              disabled={!recording && !canStart}
              aria-pressed={live}
              onClick={toggleLive}
            >
              <span className="live-dot" aria-hidden="true" />
              Live
            </Button>
            <input ref={fileInput} type="file" accept="audio/*,.mp3,.wav,.m4a" onChange={(event) => void openFile(event)} hidden />
          </div>
          <div className="activity" role="status">
            {recording && (
              <span className="capture-status">
                {(!native || mode !== "system") && <span className="capture-meter">
                  {native && <span>Mic</span>}<meter min={0} max={1} value={level} aria-label="Microphone level" />
                </span>}
                {native && mode !== "mic" && <span className="capture-meter">
                  <span>Mac audio</span><meter min={0} max={1} value={systemLevel} aria-label="Mac audio level" />
                </span>}
                <span className="timer" aria-hidden="true">{clock(elapsed)}</span>
              </span>
            )}
            {recording && <span>{recordingActivity}</span>}
            {canStart && !recording && <span>Your recordings, transcripts, and notes stay on your Mac.</span>}
          </div>
          {(feedback || status === "error") && (
            <p className="feedback" role="alert">
              {feedback || "Jard Soundfolio Local could not start. Check the launcher, then reload this page."}
            </p>
          )}
          <div className="transcription-options">
            <LanguagePicker
              languages={languageOptions}
              selected={selectedLanguages}
              disabled={!canStart || languageOptions.length === 0}
              onChange={setSelectedLanguages}
            />
            <Button
              variant="outline"
              size="sm"
              className="speaker-toggle"
              aria-pressed={diarization && speakerLabelsAvailable}
              disabled={!canStart || !speakerLabelsAvailable}
              onClick={() => setDiarization(!diarization)}
              title={speakerLabelsAvailable ? "Label who spoke" : "Speaker labeling is unavailable"}
            >
              <UsersRound />
              Speakers
            </Button>
          </div>
          {native && <div className="native-audio-source">
            <label className="native-source-picker">
              <Mic aria-hidden="true" />
              <select aria-label="Recording source" value={mode} disabled={phase !== "idle" || choosingFile}
                onChange={event => changeMode(event.target.value as RecordingMode)}>
                {Object.entries(RECORDING_MODES).map(([key, label]) => <option key={key} value={key}>{label}</option>)}
              </select>
              <ChevronDown aria-hidden="true" />
            </label>
            {mode !== "system" && <span className="native-input-name" title="Current input from macOS Sound settings">
              {microphoneName}
            </span>}
            <button className="native-permission-link" type="button" disabled={phase !== "idle" || choosingFile}
              onClick={() => void showRecordingAccess()}>Recording access</button>
          </div>}
          {!!recoveredAudio.length && <section className="native-recovery" aria-label="Interrupted recordings">
            {recoveredAudio.map(d => <div className="native-recovery-row" key={d.id}>
              {discarding === d.id ? <>
                <span>Permanently discard this {clock(d.seconds)} recording?</span>
                <Button size="sm" variant="ghost" disabled={choosingFile} onClick={() => setDiscarding(null)}>Cancel</Button>
                <Button size="sm" variant="destructive" disabled={!canStart} onClick={() => void handleRecovery(d.id, true)}>Discard recording</Button>
              </> : <>
                <span>Interrupted recording · {clock(d.seconds)}</span>
                <Button size="sm" variant="outline" disabled={!canStart} onClick={() => void handleRecovery(d.id, false)}>Recover</Button>
                <Button size="sm" variant="ghost" disabled={!canStart} onClick={() => setDiscarding(d.id)}>Discard…</Button>
              </>}
            </div>)}
          </section>}
        </div>
        <Downloads item={current} /></>}
      </main>
    </div>
    {native && permissionGuide && <RecordingAccessDialog native={native} mode={mode} permissions={permissionGuide} inspection={inspectingAccess}
      refusedSource={refusedSource} starting={phase === "starting"} onChange={setPermissionGuide}
      onClose={() => { setPermissionGuide(null); setRefusedSource(null) }} onRetry={() => void startRecording()} />}
    </SessionLibrary>
  )
}
