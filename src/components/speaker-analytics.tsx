import { useMemo, useState, type CSSProperties } from "react"
import { ChartNoAxesGantt } from "lucide-react"
import type { Transcript } from "@/audio"
import { clock, speakerColor, speakerLabel } from "@/speaker-activity"

const percent = new Intl.NumberFormat(undefined, { style: "percent", maximumFractionDigits: 1 })

export function SpeakerAnalytics({ result, time, ready, seek }: {
  result: Transcript; time: number; ready: boolean; seek: (time: number) => void
}) {
  const [hover, setHover] = useState<number | null>(null)
  const rows = result.speakerActivity
  const duration = result.durationSeconds ?? 0
  const names = result.speakerNames
  const lanes = useMemo(() => rows?.map(row => (
          <div className="timeline-lane" key={row.speaker}>
            <span>{speakerLabel(row.speaker, names)}</span>
            <svg viewBox={`0 0 ${duration} 1`} preserveAspectRatio="none" aria-hidden="true">
              <path stroke={speakerColor(row.speaker)} strokeWidth="1" fill="none"
                d={row.intervals.map(span => `M${span.start} .5H${span.end}`).join("")} />
            </svg>
          </div>
        )), [rows, duration, names])
  const cursor = Math.min(duration, time)
  const reading = hover ?? cursor
  if (!rows?.length || !duration) {
    const message = rows ? "No speaker activity detected." : duration
      ? "Speaker analysis was off for this recording."
      : "Record or open audio with Speakers on to see who spoke when."
    return <div className="analytics-empty empty-state">
      <span className="empty-icon"><ChartNoAxesGantt /></span><p>{message}</p>
    </div>
  }
  const active = rows.filter(row =>
    row.intervals.some(span => span.start <= reading && reading < span.end))
  const atCursor = active.map(row => speakerLabel(row.speaker, names)).join(" + ") || "Silence"
  return (
    <div className="analytics">
      <div className="analytics-title"><h2>Speaker timeline</h2><span>{clock(duration)} {result.interruption ? "processed" : "total"}</span></div>
      <div className="timeline-plot">
        {lanes}
        <div className="timeline-overlay">
          <i className="timeline-playhead" style={{ left: `${cursor / duration * 100}%` }} />
          {hover !== null && <i className="timeline-hover" style={{ left: `${hover / duration * 100}%` }} />}
          <input type="range" min="0" max={duration} step="0.1" value={cursor} disabled={!ready}
            aria-label="Seek speaker timeline" aria-valuetext={clock(cursor)}
            onChange={event => { setHover(null); seek(event.currentTarget.valueAsNumber) }}
            onPointerLeave={() => setHover(null)}
            onPointerMove={event => {
              if (event.buttons || event.pointerType !== "mouse") { setHover(null); return }
              const box = event.currentTarget.getBoundingClientRect()
              setHover(Math.max(0, Math.min(1, (event.clientX - box.left) / box.width)) * duration)
            }} />
        </div>
      </div>
      <div className="timeline-axis"><span>0:00</span><span>{clock(duration / 2)}</span><span>{clock(duration)}</span></div>
      <div className="timeline-reading">{clock(reading)} · {atCursor}</div>
      <div className="share-heading"><h2>Speaking time</h2><span>Share of speaker time</span></div>
      <ul className="speaker-shares">
        {rows.map(row => (
          <li key={row.speaker} style={{ "--speaker-color": speakerColor(row.speaker) } as CSSProperties}>
            <span className="speaker-name"><i />{speakerLabel(row.speaker, names)}</span>
            <span className="share-track" aria-hidden="true"><i style={{ width: `${row.share * 100}%` }} /></span>
            <span>{row.seconds < 1 ? "<1s" : clock(row.seconds)}</span><strong>{percent.format(row.share)}</strong>
          </li>
        ))}
      </ul>
      <p className="analytics-note">Overlapping speech counts toward each active speaker’s time.</p>
    </div>
  )
}
