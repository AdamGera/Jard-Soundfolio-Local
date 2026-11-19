import type { SpeakerSegment } from "./audio"

export type RecordingMode = "both" | "mic" | "system"
export type CaptureSource = Exclude<RecordingMode, "both">
export type PermissionStatus = "granted" | "denied" | "restricted" | "unavailable"
export type RecordingPermissions = Partial<Record<CaptureSource, PermissionStatus>>

export function captureSources(mode: RecordingMode): CaptureSource[] {
  return mode === "both" ? ["mic", "system"] : [mode]
}

export const RECORDING_MODES: Record<RecordingMode, string> = {
  both: "Microphone + Mac audio",
  mic: "Microphone only",
  system: "Mac audio only",
}

type WindowChrome = { controlsEnd: number; controlsCenterY: number; fullscreen: boolean }

function applyWindowChrome(chrome: WindowChrome) {
  if (!chrome || !Number.isFinite(chrome.controlsEnd) || !Number.isFinite(chrome.controlsCenterY)) return
  const style = document.documentElement.style
  style.setProperty("--window-controls-end", Math.max(8, chrome.controlsEnd) + "px")
  style.setProperty("--window-controls-center-y", Math.max(15, chrome.controlsCenterY) + "px")
}

type CaptureOptions = { diarization: boolean; languages: string[] }
export type RecordingDraft = { id: string; seconds: number }
type ActiveCapture = { mode: RecordingMode; live: boolean; seconds?: number; text?: string; segments?: SpeakerSegment[] }
type Requests = {
  permission: { source: CaptureSource; prompt: boolean }
  openPrivacy: { source: CaptureSource }
  revealApp: undefined
  devices: undefined
  record: CaptureOptions & { mode: RecordingMode; live: boolean }
  stop: undefined
  live: { enabled: boolean }
  openAudio: CaptureOptions
  recover: CaptureOptions & { id: string }
  discard: { id: string }
  export: { id: string; kind: "audio" | "json" }
}
type Responses = {
  permission: { status: PermissionStatus }
  openPrivacy: unknown
  revealApp: unknown
  devices: { microphone: string; capture?: ActiveCapture | null; drafts?: RecordingDraft[] }
  record: unknown
  stop: unknown
  live: unknown
  openAudio: { id: string } | null
  recover: { id: string }
  discard: unknown
  export: unknown
}
export type DesktopBridge = {
  request<A extends keyof Requests>(action: A, ...args: Requests[A] extends undefined ? [] : [Requests[A]]): Promise<Responses[A]>
}

export type DesktopCaptureEvent =
  | { event: "started"; mode: RecordingMode; microphone: string }
  | { event: "levels"; seconds: number; mic: number; system: number; microphone?: string }
  | { event: "sourceChanged"; microphone: string; message: string }
  | { event: "preview"; text: string; segments?: SpeakerSegment[] }
  | { event: "finished"; reason?: string }
  | { event: "accepted"; id: string }
  | { event: "warning"; message: string }
  | { event: "error"; message: string; permission?: CaptureSource }

declare global {
  interface Window {
    jardDesktop?: DesktopBridge
    zero?: { invoke<R>(command: string, payload: unknown): Promise<R> }
  }
  interface WindowEventMap { "jard-capture": CustomEvent<DesktopCaptureEvent> }
}

// Native SDK installs its bridge at document start. A regular browser has no host transport.
if (typeof window.zero?.invoke === "function" && !window.jardDesktop) {
  const host = window.zero
  window.addEventListener("native-sdk:jard-chrome", event => {
    applyWindowChrome((event as CustomEvent<WindowChrome>).detail)
  })
  void host.invoke<WindowChrome>("jard.chrome", {}).then(applyWindowChrome).catch(() => {
    // The CSS safe area also works while the host is measuring a newly opened window.
  })
  window.jardDesktop = {
    request<A extends keyof Requests>(action: A, ...args: Requests[A] extends undefined ? [] : [Requests[A]]) {
      return host.invoke<Responses[A]>("jard." + action, args[0] ?? {})
    },
  }
  window.addEventListener("native-sdk:jard-capture", event => {
    window.dispatchEvent(new CustomEvent("jard-capture", { detail: (event as CustomEvent<DesktopCaptureEvent>).detail }))
  })
}

if (typeof window.jardDesktop?.request === "function") {
  document.documentElement.dataset.nativeWindow = "true"
}

/** The browser and bundled app use exactly the same React screens. */
export function desktopBridge() {
  return typeof window.jardDesktop?.request === "function" ? window.jardDesktop : undefined
}

/** Check every selected source without starting capture. Only Record or an
 * explicit permission button may initiate a macOS prompt. */
export async function recordingPermissions(native: DesktopBridge, mode: RecordingMode): Promise<RecordingPermissions> {
  const entries = await Promise.all(captureSources(mode).map(async source => {
    const { status } = await native.request("permission", { source, prompt: false })
    return [source, status] as const
  }))
  return Object.fromEntries(entries)
}

export function savedRecordingMode(): RecordingMode {
  try {
    const value = localStorage.getItem("jard.recordingMode")
    if (value === "mic" || value === "system") return value
  } catch { /* A private storage profile can still record. */ }
  return "both"
}
