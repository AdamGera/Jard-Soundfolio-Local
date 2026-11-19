import { useState } from "react"
import { FileAudio, FileJson, Mic } from "lucide-react"

import { desktopBridge } from "@/desktop"
import type { SavedSession, Transcript } from "@/audio"
import { Button } from "@/components/ui/button"

export type DownloadItem = {
  id: string
  name: string
  languageHints: readonly string[]
  audio?: Blob
  audioUrl?: string
  audioName?: string
  recording?: boolean
  transcript?: Transcript
  saved?: SavedSession
}

function save(blob: Blob, name: string) {
  const url = URL.createObjectURL(blob)
  const link = document.createElement("a")
  link.href = url
  link.download = name
  document.body.append(link)
  link.click()
  link.remove()
  // Safari can consume a download URL asynchronously after the click.
  window.setTimeout(() => URL.revokeObjectURL(url), 60_000)
}

function downloadAudio(audio: Blob, name: string) {
  const format = audio.type.split(";")[0]
  const extension: Record<string, string> = {
    "audio/aac": "aac",
    "audio/mp4": "m4a",
    "audio/mpeg": "mp3",
    "audio/ogg": "ogg",
    "audio/wav": "wav",
    "audio/webm": "webm",
    "video/mp4": "mp4",
    "video/webm": "webm",
  }
  save(audio, `${name}.${extension[format] ?? "audio"}`)
}

function downloadTranscript(item: DownloadItem) {
  const json = JSON.stringify({
    ...(item.saved && {
      id: item.id, created: item.saved.created, audioUrl: item.saved.audioUrl, audioPath: item.saved.audioPath, recording: item.saved.recording,
      live: item.saved.live, diarization: item.saved.diarization, model: item.saved.model,
      modelRevision: item.saved.modelRevision, speakerModel: item.saved.speakerModel, speakerModelRevision: item.saved.speakerModelRevision,
    }),
    source: item.audioName || item.name,
    languageHints: item.languageHints,
    ...item.transcript,
  }, null, 2)
  const stem = item.recording ? item.name : item.name.replace(/\.[^.]+$/, "") || item.name
  save(new Blob([json + "\n"], { type: "application/json" }), `${stem}.json`)
}

export function Downloads({ item, compact = false }: { item: DownloadItem | null; compact?: boolean }) {
  const [exportError, setExportError] = useState("")
  if (!item) return null
  const bridge = item.saved ? desktopBridge() : undefined
  const sessionId = item.id
  async function exportSaved(kind: "audio" | "json") {
    setExportError("")
    try {
      await bridge!.request("export", { id: sessionId, kind })
    } catch (reason) {
      setExportError(reason instanceof Error ? reason.message : "Could not save the export.")
    }
  }

  return <section className={compact ? "compact-downloads" : "downloads"} aria-label="Session downloads">
    <div className={compact ? undefined : "current-file"}>
      {!compact && <><span className="download-source" aria-hidden="true">{item.recording ? <Mic /> : <FileAudio />}</span>
      <span className="download-name" title={item.audioName || item.name} dir="auto">{item.audioName || item.name}</span></>}
      <div className="download-actions">
        {bridge && item.audioUrl ? <Button variant="ghost" size="icon-sm" aria-label={`Download audio for ${item.name}`}
          title="Download audio" onClick={() => void exportSaved("audio")}><FileAudio /></Button> : item.audioUrl ? <Button asChild variant="ghost" size="icon-sm">
          <a href={item.audioUrl} download={item.audioName || item.name} aria-label={`Download audio for ${item.name}`} title="Download audio"><FileAudio /></a>
        </Button> : item.audio && <Button variant="ghost" size="icon-sm" aria-label={`Download audio for ${item.name}`}
          title="Download audio" onClick={() => downloadAudio(item.audio!, item.name)}><FileAudio /></Button>}
        {item.transcript && <Button variant="ghost" size="icon-sm" aria-label={`Download JSON transcript for ${item.name}`}
          title="Download transcript as JSON" onClick={() => bridge ? void exportSaved("json") : downloadTranscript(item)}><FileJson /></Button>}
      </div>
    </div>
    {exportError && <p role="alert" className="feedback">{exportError}</p>}
  </section>
}
