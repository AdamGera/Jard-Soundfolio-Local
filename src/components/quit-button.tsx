import { useState } from "react"
import { AlertDialog } from "radix-ui"
import { Power } from "lucide-react"
import { Button } from "./ui/button"

export function QuitButton({ recording, onQuit }: { recording: boolean; onQuit: () => void }) {
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState("")

  async function quit() {
    setBusy(true)
    setError("")
    try {
      const response = await fetch("/api/quit", { method: "POST", headers: { "X-Jard-Action": "quit" } })
      if (response.status === 405 || response.status === 404) {
        // An already-running older server can serve the new UI before its next restart.
        if (!import.meta.env.JARD_WELCOME_PAGE) throw new Error("Quit the older server using its Quit Jard Soundfolio Local.command in Finder.")
        window.location.href = "jard-soundfolio-local://quit?page=" + encodeURIComponent(import.meta.env.JARD_WELCOME_PAGE)
        for (let attempt = 0; attempt < 12; attempt++) {
          await new Promise(resolve => setTimeout(resolve, 500))
          try {
            await fetch("/api/status", { cache: "no-store", signal: AbortSignal.timeout(1000) })
          } catch (reason) {
            if (reason instanceof TypeError) { onQuit(); return }
          }
        }
        throw new Error("Jard Soundfolio Local is still running. Allow the browser to open Jard Soundfolio Local Launcher, then try again.")
      }
      if (!response.ok) throw new Error("Jard Soundfolio Local could not quit. Try again.")
      onQuit()
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Could not reach Jard Soundfolio Local.")
      setBusy(false)
    }
  }

  return <AlertDialog.Root onOpenChange={() => setError("")}>
    <AlertDialog.Trigger asChild>
      <Button size="icon-sm" variant="ghost" title="Quit Jard Soundfolio Local" aria-label="Quit Jard Soundfolio Local"><Power /></Button>
    </AlertDialog.Trigger>
    <AlertDialog.Portal>
      <AlertDialog.Overlay className="note-language-scrim" />
      <AlertDialog.Content className="note-language-confirm" onEscapeKeyDown={event => { if (busy) event.preventDefault() }}>
        <AlertDialog.Title>Are you sure?</AlertDialog.Title>
        <AlertDialog.Description>
          {recording ? "Quitting will discard the current recording." : "Quit Jard Soundfolio Local and stop local processing? Your saved sessions will be here next time."}
        </AlertDialog.Description>
        {error && <p role="alert">{error}</p>}
        <div className="note-language-actions">
          <AlertDialog.Cancel asChild><Button size="sm" variant="outline" disabled={busy}>Cancel</Button></AlertDialog.Cancel>
          <Button size="sm" disabled={busy} onClick={() => void quit()}>{busy ? "Quitting…" : "Okay"}</Button>
        </div>
      </AlertDialog.Content>
    </AlertDialog.Portal>
  </AlertDialog.Root>
}
