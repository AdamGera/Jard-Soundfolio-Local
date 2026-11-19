import { useEffect, useEffectEvent, useRef, useState, type ChangeEvent } from "react"
import { FolderOpen, Mic, Plus, Square, UsersRound } from "lucide-react"

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
    ? <main className="quit-screen" role="status"><h1>Jard Soundfolio Local is shutting down.</h1><p>You can close this tab.</p></main>
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
  const [copied, setCopied] = useState(false)
  const fileInput = useRef<HTMLInputElement>(null)
  const [current, setCurrent] = useState<DownloadItem | null>(null)
  const [restoring, setRestoring] = useState(() => !!readSessionRoute().id)
  const restoreRequest = useRef<AbortController | null>(null)
  const pendingNavigation = useRef(false)
  const session = useRef<Session | null>(null)
  const copyTimer = useRef<number>(undefined)

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
  const canStart = status === "ready" && phase === "idle" && !restoring
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
      setFeedback(message(error))
      setPhase("idle")
    }
  }

  function toggleLive() {
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
    <div className="soundfolio-shell" data-session={viewingSession}>
      <header className="soundfolio-header">
        <div className="wordmark">
          <picture>
            <source media="(prefers-color-scheme: dark)" srcSet="/jard-dark.png" />
            <img className="wordmark-icon" src="/jard-light.png" width="40" height="40" alt="" />
          </picture>
          Jard Soundfolio Local
        </div>
        <nav className="workspace-actions" aria-label="Session actions">
          {viewingSession && <>
            <Downloads item={current} compact />
            <Button size="sm" variant="ghost" onClick={newSession} title="New session" aria-label="New session"><Plus /><span>New session</span></Button>
          </>}
        <SessionLibrary busy={phase !== "idle" || restoring} currentId={current?.id} onSelect={openSaved} onDelete={id => {
          if (current?.id !== id && readSessionRoute().id !== id) return
          restoreRequest.current?.abort()
          setRestoring(false)
          clearSession()
          writeSessionRoute(null, "overview", true)
        }} />
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
              onClick={() => fileInput.current?.click()}
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
                <meter min={0} max={1} value={level} aria-label="Microphone level" />
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
        </div>
        <Downloads item={current} /></>}
      </main>
    </div>
  )
}
