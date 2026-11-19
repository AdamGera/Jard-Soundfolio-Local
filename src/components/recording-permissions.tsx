import { useEffect, useEffectEvent, useRef, useState } from "react"
import { AudioLines, Check, Mic } from "lucide-react"
import { Dialog } from "radix-ui"
import { captureSources, recordingPermissions, type CaptureSource, type DesktopBridge, type RecordingMode, type RecordingPermissions } from "@/desktop"
import { Button } from "./ui/button"

const names = { mic: "Microphone", system: "Mac audio" }
const panes = { mic: "Microphone", system: "Screen & System Audio Recording" }
const statuses = { granted: "Allowed", restricted: "Restricted by macOS", denied: "Access needed", unavailable: "Access needed" }
const resetMicrophone = "/usr/bin/tccutil reset Microphone local.jardaudio.desktop"

type Props = {
  native: DesktopBridge; mode: RecordingMode; permissions: RecordingPermissions; starting: boolean
  refusedSource: CaptureSource | null; inspection: boolean
  onChange: (value: RecordingPermissions) => void; onClose: () => void; onRetry: () => void
}

export function RecordingAccessDialog({ native, mode, permissions, refusedSource, inspection, starting, onChange, onClose, onRetry }: Props) {
  const sources = captureSources(mode)
  const granted = sources.every(source => permissions[source] === "granted")
  const [pending, setPending] = useState(false)
  const [message, setMessage] = useState("")
  const active = useRef(true)
  const changing = useRef(false)
  const cancel = useRef<HTMLButtonElement>(null)
  const busy = starting || pending

  async function check() {
    if (starting || changing.current) return
    changing.current = true
    setPending(true)
    try {
      const result = await recordingPermissions(native, mode)
      if (active.current) {
        onChange(result)
        setMessage(sources.every(s => result[s] === "granted") ?
          (refusedSource ? "System Settings reports access is enabled. Quit and reopen Jard if macOS still refuses capture." : "Access is ready.") :
          (result.system === "unavailable" ? "Choose Verify Mac audio access to check with macOS." : "Check the macOS settings for the sources marked above."))
      }
    } catch { if (active.current) setMessage("Could not check access. Try again.") }
    finally { changing.current = false; if (active.current) setPending(false) }
  }
  const onFocus = useEffectEvent(() => { void check() })
  useEffect(() => {
    active.current = true
    const focus = () => onFocus()
    window.addEventListener("focus", focus)
    return () => { active.current = false; window.removeEventListener("focus", focus) }
  }, [])
  useEffect(() => {
    if (!refusedSource) return
    let cancelled = false
    // A helper can be refused even when the foreground app reports access.
    // Fetch the status of the other selected sources while keeping that error visible.
    void recordingPermissions(native, mode).then(statuses => {
      if (!cancelled) onChange(statuses)
    }).catch(() => { /* The original refusal still provides the recovery actions. */ })
    return () => { cancelled = true }
  }, [refusedSource, native, mode, onChange])

  async function request(source: CaptureSource) {
    if (busy || changing.current) return
    changing.current = true
    setPending(true)
    try {
      const { status } = await native.request("permission", { source, prompt: true })
      if (active.current) {
        onChange({ ...permissions, [source]: status })
        setMessage(status === "granted" ? names[source] + " access is ready." : "If macOS skips the prompt, use the privacy settings for this source.")
      }
    } catch { if (active.current) setMessage("Could not request access. Try again.") }
    finally { changing.current = false; if (active.current) setPending(false) }
  }

  return <Dialog.Root open onOpenChange={open => { if (!open && !busy) onClose() }}>
    <Dialog.Portal>
      <Dialog.Overlay className="session-search-overlay" />
      <Dialog.Content className="recording-access-dialog"
        onOpenAutoFocus={event => { event.preventDefault(); cancel.current?.focus() }}
        onCloseAutoFocus={event => { event.preventDefault(); document.querySelector<HTMLButtonElement>(inspection ? ".native-permission-link" : ".record-button")?.focus() }}>
        <Dialog.Title>{inspection ? "Recording access" : "Allow recording on this Mac"}</Dialog.Title>
        <Dialog.Description>{refusedSource ?
          "macOS refused " + (refusedSource === "system" ? "Mac audio capture" : "microphone capture") + ". Check Jard Soundfolio Local in Privacy & Security, then retry." :
          (inspection ? "Check macOS access for your selected audio sources." : "Check access for the audio sources you selected.")}</Dialog.Description>
        {(refusedSource || sources.some(source => permissions[source] === "denied")) &&
          <p>If Jard is already on in System Settings, quit Jard with ⌘Q, reopen it from Applications, then retry.</p>}
        <div className="recording-access-sources">{sources.map(source => {
          const status = permissions[source] ?? "unavailable"
          const Icon = source === "mic" ? Mic : AudioLines
          return <div key={source} className="recording-access-row">
            <Icon aria-hidden="true" /><div><strong>{names[source]}</strong>
              <small>{source === "mic" ? "Your voice from the selected input" : "Sound from calls and other apps"}</small></div>
            {status === "granted" && refusedSource !== source ? <span className="recording-access-allowed"><Check aria-hidden="true" />Allowed</span> :
              <div className="recording-access-action"><small>{refusedSource === source ? "Capture refused" : source === "system" && status === "unavailable" ? "Unconfirmed" : statuses[status]}</small>
                {(refusedSource === source || status === "denied" || status === "restricted") ?
                  <Button variant="outline" size="sm" disabled={busy} aria-label={"Open " + panes[source] + " settings"}
                    onClick={() => void native.request("openPrivacy", { source }).catch(() => setMessage("Open System Settings → Privacy & Security → " + panes[source] + "."))}>Open Settings</Button> :
                  <Button variant="outline" size="sm" disabled={busy} onClick={() => void request(source)}>{source === "system" ? "Verify Mac audio access" : "Request access"}</Button>}
              </div>}
          </div>
        })}</div>
        {(!granted || refusedSource) && <details className="recording-access-help">
          <summary>Jard isn’t listed in System Settings</summary>
          <p>Open Jard Soundfolio Local.app from Finder and press Record to register its request. If macOS asks you to quit and reopen Jard, do that before checking again.</p>
          {sources.includes("mic") && <p>Under Privacy &amp; Security → Microphone, macOS lists apps after they request access. Turn on Jard Soundfolio Local. A restricted Mac may need an administrator.</p>}
          {permissions.mic === "denied" && <div className="recording-access-reset">
            <p>If Jard is still missing from Microphone after reopening, copy this command, quit Jard, then run it in Terminal. It clears only Jard’s microphone decision. Reopen Jard and press Record for a fresh macOS prompt.</p>
            <code>{resetMicrophone}</code>
            <button type="button" onClick={() => void navigator.clipboard.writeText(resetMicrophone)
              .then(() => setMessage("Microphone recovery command copied."))
              .catch(() => setMessage("Select and copy the command shown above."))}>Copy recovery command</button>
          </div>}
          {sources.includes("system") && <><p>Under Privacy &amp; Security → Screen &amp; System Audio Recording, turn on Jard. If it is missing, use the add (+) button when available and select the app shown in Finder.</p>
            <div className="recording-access-links">
              <button type="button" disabled={busy} onClick={() => void native.request("revealApp").catch(() => setMessage("Locate Jard Soundfolio Local.app in Finder."))}>Show this app in Finder</button>
              {(permissions.system === "denied" || refusedSource === "system") && <button type="button" disabled={busy} onClick={() => void request("system")}>Request Mac audio access again</button>}
            </div></>}
        </details>}
        <p role="status" className="recording-access-status">{starting ? "Respond to the macOS permission request." : message}</p>
        <div className="recording-access-footer"><Button ref={cancel} variant="ghost" size="sm" disabled={busy} onClick={onClose}>Cancel</Button>
          <Button size="sm" disabled={busy} onClick={() => granted ? (inspection ? onClose() : onRetry()) : void check()}>{granted ? (inspection ? "Done" : "Retry recording") : "Check access again"}</Button></div>
      </Dialog.Content>
    </Dialog.Portal>
  </Dialog.Root>
}
