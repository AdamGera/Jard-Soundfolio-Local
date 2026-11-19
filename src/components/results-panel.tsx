import { lazy, Suspense, useCallback, useDeferredValue, useMemo, useEffect, useRef, useState, type ReactNode } from "react"
import { AudioLines, CalendarDays, Check, ChevronUp, Clock3, Copy, RotateCw } from "lucide-react"
import type { SavedSession, Transcript } from "@/audio"
import { clock } from "@/speaker-activity"
import { useOverview } from "@/overview"
import { AudioPlayer, usePlayback } from "./audio-player"
import { OverviewLanguage, languageName } from "./overview-language"
import { transcriptMatches } from "@/transcript-search"
import { TranscriptFind } from "./transcript-find"
import { Button } from "./ui/button"
import { Tabs, TabsList, TabsTrigger, TabsContent } from "./ui/tabs"

const SpeakerAnalytics = lazy(() => import("./speaker-analytics").then(module => ({ default: module.SpeakerAnalytics })))
const TimedTranscript = lazy(() => import("./timed-transcript").then(module => ({ default: module.TimedTranscript })))
const SessionChat = lazy(() => import("./session-conversation"))
const NoteMarkdown = lazy(() => import("./note-markdown").then(module => ({ default: module.NoteMarkdown })))

type Props = {
  result: Transcript; session?: SavedSession; source: Blob | string | null
  tab: string; onTab: (tab: string) => void
  working: boolean; recording: boolean; emptyText: string; progress: ReactNode
  copied: boolean; onCopy: () => void
}
const date = new Intl.DateTimeFormat(undefined, { month: "short", day: "numeric", year: "numeric" })

export function ResultsPanel({ result, session, source, tab, onTab, working, recording, emptyText, progress, copied, onCopy }: Props) {
  const player = usePlayback(source, result.durationSeconds)
  const complete = session?.status === "complete"
  const overview = useOverview(complete ? session.id : undefined, session?.overview, tab === "overview" || tab === "timeline")
  const writing = overview.value
  const version = overview.version
  const [citation, setCitation] = useState<{ seconds: number } | null>(null)
  const [dock, setDock] = useState<"audio" | "chat" | "expanded" | null>(null)
  const audioTrigger = useRef<HTMLButtonElement>(null)
  const toggleChat = useCallback((open: boolean) => setDock(open ? "chat" : null), [])
  const resizeChat = useCallback(() => setDock(current => current === "expanded" ? "chat" : "expanded"), [])
  const [scrolled, setScrolled] = useState(false)
  const [findOpen, setFindOpen] = useState(false)
  const [query, setQuery] = useState("")
  const [matchIndex, setMatchIndex] = useState(0)
  const search = useDeferredValue(findOpen && tab === "transcript" ? query : "")
  const matches = useMemo(() => transcriptMatches(result, search), [result, search])
  const currentMatch = Math.min(matches.length - 1, matchIndex)
  function changeQuery(text: string) { setQuery(text); setMatchIndex(0) }
  function toggleFind(open: boolean) {
    setFindOpen(open)
    if (!open) changeQuery("")
  }
  function stepMatch(delta: number) {
    if (matches.length) setMatchIndex((currentMatch + delta + matches.length) % matches.length)
  }
  const area = useRef<HTMLDivElement>(null)
  const tabList = useRef<HTMLDivElement>(null)
  const changeTab = useCallback((value: string) => {
    setScrolled(false)
    setCitation(null)
    setFindOpen(false)
    setQuery("")
    setMatchIndex(0)
    onTab(value)
  }, [onTab])
  useEffect(() => {
    if (tab !== "transcript" || !result.text || recording || working) return
    function find(event: KeyboardEvent) {
      if (!(event.metaKey || event.ctrlKey) || event.key.toLowerCase() !== "f" || event.altKey || event.shiftKey) return
      if (document.querySelector('[role="dialog"], [role="listbox"]')) return
      event.preventDefault()
      setFindOpen(true)
      const field = document.querySelector<HTMLInputElement>('.transcript-find input')
      field?.focus()
      field?.select()
    }
    document.addEventListener("keydown", find)
    return () => document.removeEventListener("keydown", find)
  }, [tab, result.text, recording, working])
  const { seek } = player
  const cite = useCallback((seconds: number) => {
    setDock(null)
    changeTab("transcript")
    setCitation({ seconds })
    seek(seconds, true)
  }, [changeTab, seek])
  useEffect(() => {
    if (dock !== "audio") return
    const dismiss = (event: KeyboardEvent) => {
      if (event.key !== "Escape" || event.defaultPrevented) return
      setDock(null)
      audioTrigger.current?.focus({ preventScroll: true })
    }
    document.addEventListener("keydown", dismiss)
    return () => document.removeEventListener("keydown", dismiss)
  }, [dock])
  useEffect(() => {
    if (area.current && recording) area.current.scrollTop = area.current.scrollHeight
  }, [recording, result.text])
  useEffect(() => {
    const strip = tabList.current
    const active = strip?.querySelector<HTMLElement>('[data-state="active"]')
    if (!strip || !active || strip.scrollWidth === strip.clientWidth) return
    strip.scrollLeft += active.getBoundingClientRect().left - strip.getBoundingClientRect().left - (strip.clientWidth - active.offsetWidth) / 2
  }, [tab])
  const draft = overview.draft
  const note = draft?.summary || draft?.markdown ? draft : version
  const timeline = draft?.topics.length ? draft : version
  const showingPrevious = version && (tab === "timeline" ? timeline === version : note === version)
  const stage = draft?.topics.length ? "Writing topic timeline…" : draft?.markdown || draft?.summary ? "Writing summary…" :
    draft?.title ? "Writing title…" : null
  const chapter = timeline?.topics.findLastIndex(t => t.start != null && t.start <= player.time) ?? -1
  const status = overview.error || writing?.error
  const pending = writing?.status === "queued" || !writing
  const noteStatus = status || pending || writing?.status === "empty" ? <div className="note-state">
    <p className="note-status" role={status ? "alert" : "status"}>
      {!status && pending && <i className="thinking-dot" />}
      {status || (writing?.status === "empty" ? "No speech detected." :
        stage ?? (writing?.target && writing.target !== "und" ? `Creating ${languageName(writing.target)} overview…` : "Preparing overview…"))}
      {status && <Button size="xs" variant="ghost" onClick={overview.retry}>Retry</Button>}
    </p>
    {showingPrevious && <small>Showing saved {languageName(version.language)} {tab === "timeline" ? "timeline" : "overview"}</small>}
  </div> : null
  return <div className={complete ? "session-sheet" : undefined}>
    {complete && <header className="session-heading">
      <h1 dir="auto">{draft?.title || version?.title || session.name}</h1>
      <div className="session-meta">
        <span><CalendarDays aria-hidden="true" />{date.format(new Date(session.created))}</span>
        <span><Clock3 aria-hidden="true" /><bdi>{clock(result.durationSeconds ?? 0)}</bdi></span>
        {writing?.status !== "empty" && <OverviewLanguage detected={writing?.autoLanguage ?? ""}
          value={writing?.preference ?? "auto"} onSelect={overview.select} />}
        {writing && writing.status !== "empty" && <Button size="sm" variant="ghost" className="regenerate-note"
          aria-label="Regenerate overview and timeline" title="Regenerate overview and timeline"
          disabled={writing.status === "queued" || overview.requesting}
          onClick={overview.regenerate}><RotateCw aria-hidden="true" /><span>Regenerate</span></Button>}
      </div>
    </header>}
    <Tabs className="transcript-panel" data-finding={findOpen && tab === "transcript"} value={tab} onValueChange={changeTab}>
      <div className="panel-heading" data-scrolled={scrolled}>
        <TabsList ref={tabList} aria-label="Recording results">
          {complete && <TabsTrigger value="overview">Overview</TabsTrigger>}
          {complete && <TabsTrigger value="timeline">Timeline</TabsTrigger>}
          <TabsTrigger value="transcript">Transcript</TabsTrigger>
          <TabsTrigger value="analytics" disabled={working || recording}>Speaker analytics</TabsTrigger>
        </TabsList>
        {tab === "transcript" && result.text && !recording && !working &&
          <div className="transcript-tools">
          <TranscriptFind open={findOpen} onOpen={toggleFind} query={query} onQuery={changeQuery}
            count={matches.length} index={currentMatch} step={stepMatch} />
          <Button variant="ghost" className="copy-button" onClick={onCopy} aria-label={copied ? "Copied" : "Copy transcript"}>
            {copied ? <Check /> : <Copy />}<span>{copied ? "Copied" : "Copy"}</span>
          </Button></div>}
      </div>
      {complete && <TabsContent value="overview" className="note-body" onScroll={e => setScrolled(e.currentTarget.scrollTop > 2)}>
        {noteStatus}
        {note && <div className="overview-copy" dir="auto" lang={note.language}>
          {note.summary && <p className="note-lead">{note.summary}</p>}
          {note.markdown && <Suspense fallback={null}><NoteMarkdown text={note.markdown} /></Suspense>}
        </div>}
      </TabsContent>}
      {complete && <TabsContent value="timeline" className="note-body timeline-body" onScroll={e => setScrolled(e.currentTarget.scrollTop > 2)}>
        {noteStatus}
        {timeline && <ol className="topic-list" aria-label="Topic timeline" lang={timeline.language}>
          {timeline.topics.map((topic,i) => <li key={i}>
            <button disabled={!player.ready || topic.start == null} aria-current={i === chapter ? "time" : undefined}
              aria-label={topic.start == null ? topic.label : `Play ${topic.label} at ${clock(topic.start)}`}
              onClick={() => { if (topic.start != null) player.seek(topic.start, true) }}>
              <time dir="ltr">{topic.start != null ? clock(topic.start) : "…"}</time><span dir="auto"><strong>{topic.label}</strong>
                {topic.detail && <span>{topic.detail}</span>}</span>
            </button>
          </li>)}
        </ol>}
      </TabsContent>}
      <TabsContent value="transcript" className={result.text ? "transcript-body has-text" : "transcript-body"}
        ref={area} onScroll={e => setScrolled(e.currentTarget.scrollTop > 2)}>
        {result.text ? <Suspense fallback={null}><TimedTranscript result={result} time={player.time} ready={player.ready} seek={player.seek} citation={citation} matches={matches} activeMatch={currentMatch} /></Suspense> :
          <div className="empty-state"><span className="empty-icon" data-working={working} aria-hidden="true"><AudioLines /></span>
            {working ? progress : <p>{emptyText}</p>}
          </div>}
      </TabsContent>
      <TabsContent value="analytics" className="analytics-body" onScroll={e => setScrolled(e.currentTarget.scrollTop > 2)}>
        <Suspense fallback={null}><SpeakerAnalytics result={result} time={player.time} ready={player.ready} seek={player.seek} /></Suspense>
      </TabsContent>
      {working && result.text && <div className="processing-footer">{progress}</div>}
      {!complete && source && <AudioPlayer player={player} />}
    </Tabs>
    {complete && <footer className="session-dock" aria-label="Recording tools">
      {source && <>
        <div id="session-audio-player" className="audio-popover" hidden={dock !== "audio"}>
          <AudioPlayer player={player} />
        </div>
        <Button ref={audioTrigger} className="audio-launcher" variant="outline" aria-expanded={dock === "audio"}
          data-playing={player.advancing} aria-controls="session-audio-player" title={dock === "audio" ? "Close audio player" : "Open audio player"}
          aria-label={dock === "audio" ? "Close audio player" : "Open audio player"} onClick={() => setDock(dock === "audio" ? null : "audio")}>
          <span className="playback-wave" aria-hidden="true"><i /><i /><i /><i /><i /></span><ChevronUp aria-hidden="true" />
        </Button>
      </>}
      {result.text && <Suspense fallback={<button className="chat-launcher" disabled>Opening conversation…</button>}>
        <SessionChat id={session.id} open={dock === "chat" || dock === "expanded"} expanded={dock === "expanded"}
          onOpen={toggleChat} onExpand={resizeChat}
          ready={player.ready} cite={cite} />
      </Suspense>}
    </footer>}
  </div>
}
