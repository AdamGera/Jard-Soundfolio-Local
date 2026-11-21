import { useInterfaceLanguage } from "@/interface-language"
import { useEffect, useRef, useState } from "react"
import { AppWindow, Crop, ScreenShare, Smartphone, Square, X } from "lucide-react"
import { AlertDialog } from "radix-ui"
import type { DesktopAnnotation, DesktopAnnotationSource, DesktopBridge } from "@/desktop"
import { Button } from "./ui/button"

export function DesktopAnnotationControls({ native, busy, languages }: { native: DesktopBridge; busy: boolean; languages: string[] }) {
  const { t } = useInterfaceLanguage()
  const [annotation, setAnnotation] = useState<DesktopAnnotation>()
  const [pending, setPending] = useState(false)
  const [error, setError] = useState("")
  const [discarding, setDiscarding] = useState(false)
  const revision = useRef(0)
  useEffect(() => {
    let active = true
    const observed = revision.current
    void native.request("annotation", { action: "status" }).then(result => {
      if (active && observed === revision.current) setAnnotation(result)
    }).catch(() => {})
    const receive = (event: WindowEventMap["native-sdk:jard-annotation"]) => {
      revision.current++
      setAnnotation(event.detail)
      if (event.detail.phase !== "recording") setDiscarding(false)
    }
    window.addEventListener("native-sdk:jard-annotation", receive)
    return () => { active = false; window.removeEventListener("native-sdk:jard-annotation", receive) }
  }, [native])

  async function act(action: "start" | "stop" | "cancel" | "reveal", source?: DesktopAnnotationSource) {
    if (action === "start" && annotation?.managed) return
    setError("")
    setPending(true)
    const observed = revision.current
    try {
      const result = await native.request("annotation", { action, languages, source })
      if (observed === revision.current) setAnnotation(result)
    } catch (failure) { setError(failure instanceof Error ? failure.message : "Could not open screen annotation.") }
    finally { setPending(false) }
  }

  if (!annotation?.phase) return null
  const choosing = annotation.phase === "picking" || annotation.phase === "selecting" || annotation.phase === "starting"
  const finalizing = annotation.phase === "saving" || annotation.phase === "cancelling"
  return <section className="dictation-settings" aria-labelledby="annotation-title">
    <div className="dictation-settings-header">
      <div><h2 id="annotation-title">Screen Annotation</h2>
        <p>Record part of your Mac screen or a wired iPhone or iPad, with drawings and transcribed narration.</p>
        {!annotation.managed && <p>With Accessibility enabled, press <kbd>⌥⇧S</kbd> to choose a display. While recording, tap it to save or hold it to draw.</p>}</div>
      {annotation.busy ? <div className="dictation-settings-actions">
        <Button size="sm" variant="outline" disabled={pending || finalizing} onClick={() => void act(choosing ? "cancel" : "stop")}>
          {choosing ? <><X /> {t("Cancel")}</> : <><Square /> {annotation.phase === "saving" ? t("Saving…") : finalizing ? "Discarding…" : "Stop & Save"}</>}
        </Button>
        {annotation.phase === "recording" && <Button size="sm" variant="ghost" disabled={pending}
          onClick={() => setDiscarding(true)}>{t("Discard…")}</Button>}
      </div> : <div className="dictation-settings-actions">
        <Button size="sm" variant="outline" disabled={pending || busy || annotation.managed} onClick={() => void act("start", "display")}>
          <ScreenShare /> Choose Display…
        </Button>
        <Button size="sm" variant="outline" disabled={pending || busy || annotation.managed} onClick={() => void act("start", "window")}>
          <AppWindow /> Choose Window…
        </Button>
        <Button size="sm" variant="outline" disabled={pending || busy || annotation.managed} onClick={() => void act("start", "area")}>
          <Crop /> Choose Area…
        </Button>
        <Button size="sm" variant="outline" disabled={pending || busy || annotation.managed} onClick={() => void act("start", "phone")}>
          <Smartphone /> Wired iPhone or iPad
        </Button>
      </div>}
    </div>
    {(error || annotation.message) && <p role="status">{error || annotation.message}</p>}
    {annotation.recoverable && <Button size="sm" variant="ghost" onClick={() => void act("reveal")}>Show Recording in Finder</Button>}
    <AlertDialog.Root open={discarding} onOpenChange={setDiscarding}>
      <AlertDialog.Portal>
        <AlertDialog.Overlay className="note-language-scrim" />
        <AlertDialog.Content className="note-language-confirm">
          <AlertDialog.Title>Discard this annotation?</AlertDialog.Title>
          <AlertDialog.Description>The current recording and drawings will be deleted.</AlertDialog.Description>
          <div className="note-language-actions">
            <AlertDialog.Cancel asChild><Button variant="ghost" size="sm">{t("Keep recording")}</Button></AlertDialog.Cancel>
            <AlertDialog.Action asChild><Button variant="destructive" size="sm" onClick={() => void act("cancel")}>{t("Discard")}</Button></AlertDialog.Action>
          </div>
        </AlertDialog.Content>
      </AlertDialog.Portal>
    </AlertDialog.Root>
  </section>
}
