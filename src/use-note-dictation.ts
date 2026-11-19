import { useCallback, useEffect, useEffectEvent, useRef, useState } from "react"
import { livePreview, MAX_SECONDS, openMicrophone, transcribeDictation, type Microphone } from "./audio"
import { desktopBridge } from "./desktop"

type Take = {
  id: string
  controller: AbortController
  mic?: Microphone
  preview?: ReturnType<typeof livePreview>
  stage?: string
  blob?: Blob
  stopping?: boolean
}

export function useNoteDictation(languages: string[], insert: (text: string) => void, onBusy: (busy: boolean) => void) {
  const native = desktopBridge()
  const current = useRef<Take | null>(null)
  const callbacks = useRef({ insert, onBusy })
  useEffect(() => { callbacks.current = { insert, onBusy } }, [insert, onBusy])
  const [phase, setPhase] = useState<"idle" | "starting" | "recording" | "transcribing" | "retry">("idle")
  const [preview, setPreview] = useState("")
  const [notice, setNotice] = useState("")

  const release = useCallback(() => {
    const take = current.current
    current.current = null
    if (!take) return
    take.controller.abort()
    take.preview?.stop()
    take.mic?.close()
    callbacks.current.onBusy(false)
    if (native) {
      const request = take.stage
        ? { action: "discard" as const, id: take.stage }
        : { action: "cancel" as const, id: take.id }
      void native.request("dictation", request).catch(() => {})
    }
  }, [native])

  function cancel() {
    release()
    setPhase("idle")
    setPreview("")
    setNotice("")
  }

  async function finalize(take: Take) {
    if (current.current !== take) return
    setPhase("transcribing")
    setNotice("")
    try {
      const result = take.stage
        ? await native!.request("dictation", { action: "transcribe", id: take.stage, languages })
        : await transcribeDictation(take.blob!, languages, take.controller.signal)
      if (current.current !== take) return
      current.current = null
      callbacks.current.onBusy(false)
      setPreview("")
      setPhase("idle")
      if (result?.text.trim()) callbacks.current.insert(result.text.trim())
      else setNotice("No speech detected. Try dictating again.")
    } catch (error) {
      if (current.current !== take) return
      setNotice(error instanceof Error ? error.message : "Could not transcribe dictation.")
      setPhase("retry")
    }
  }

  async function stop() {
    const take = current.current
    if (!take || take.stopping) return
    take.stopping = true
    setPhase("transcribing")
    take.preview?.stop()
    try {
      if (native) {
        await native.request("dictation", { action: "stop", id: take.id })
      } else if (take.mic) {
        take.blob = await take.mic.stop()
        take.mic = undefined
        await finalize(take)
      }
    } catch (error) {
      if (current.current !== take) return
      release()
      setPhase("idle")
      setPreview("")
      setNotice(error instanceof Error ? error.message : "Could not stop dictation.")
    }
  }

  async function start() {
    if (current.current) return
    const take: Take = { id: crypto.randomUUID(), controller: new AbortController() }
    current.current = take
    setPhase("starting")
    setNotice("")
    callbacks.current.onBusy(true)
    try {
      if (native) {
        const permission = await native.request("permission", { source: "mic", prompt: true })
        if (current.current !== take) return
        if (permission.status !== "granted") {
          throw new Error("Allow microphone access in macOS System Settings to dictate.")
        }
        await native.request("record", { mode: "mic", live: true, diarization: false, languages, dictation: take.id })
        if (current.current !== take) await native.request("dictation", { action: "cancel", id: take.id })
      } else {
        take.preview = livePreview(languages, undefined, value => {
          if (current.current === take && !take.stopping) setPreview(value.text)
        }, () => setNotice("Live preview is delayed. Dictation is still recording."))
        const mic = await openMicrophone(microphone => {
          take.preview?.update(microphone)
          if (microphone.length >= microphone.sampleRate * MAX_SECONDS) void stop()
        }, () => void stop())
        if (current.current !== take) { mic.close(); return }
        take.mic = mic
        setPhase("recording")
      }
    } catch (error) {
      if (current.current !== take) return
      release()
      setPhase("idle")
      setNotice(error instanceof DOMException && error.name === "NotAllowedError"
        ? "Allow microphone access in your browser to dictate."
        : error instanceof Error ? error.message : "Could not start dictation.")
    }
  }

  const receive = useEffectEvent(({ detail }: WindowEventMap["jard-capture"]) => {
    const take = current.current
    if (!take || detail.dictation !== take.id) return
    switch (detail.event) {
      case "started": setPhase("recording"); break
      case "preview": if (!take.stopping) setPreview(detail.text); break
      case "warning": setNotice(detail.message); break
      case "levels": if (detail.seconds >= MAX_SECONDS) void stop(); break
      case "dictationReady": take.stage = detail.id; void finalize(take); break
      case "error": release(); setPhase("idle"); setPreview(""); setNotice(detail.message); break
    }
  })
  useEffect(() => {
    if (!native) return
    const listener = (event: WindowEventMap["jard-capture"]) => receive(event)
    window.addEventListener("jard-capture", listener)
    return () => window.removeEventListener("jard-capture", listener)
  }, [native])

  useEffect(() => {
    window.addEventListener("pagehide", release)
    return () => {
      window.removeEventListener("pagehide", release)
      release()
    }
  }, [release])

  return { phase, preview, notice, start, stop, cancel, retry: () => {
    if (current.current) void finalize(current.current)
  } }
}
