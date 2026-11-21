import { useEffect, useRef, useState } from "react"
import type { DictationPreferences, DictationSoundAnimation } from "@/system-dictation"
import { useInterfaceLanguage } from "@/interface-language"
import schedule from "../../desktop/assets/dictation-sounds/preview.json"
import framesURL from "@/assets/dictation-preview.png"

const pixelWidth = schedule.width * schedule.scale
const pixelHeight = schedule.height * schedule.scale
const previews = new Map(schedule.previews.map((preview, index) => [
  preview.cue, {
    ...preview,
    offset: schedule.previews.slice(0, index).reduce((sum, prior) => sum + prior.frames, 0),
  },
]))
const stillOffset = schedule.previews.reduce((sum, preview) => sum + preview.frames, 0)
const stillFrames = new Map(schedule.stillFrames.map(({ cue }, index) => [cue, stillOffset + index]))
const labels: Record<string, string> = { start: "Listening", stop: "Processing", done: "Done", error: "Failed" }
let frames: Promise<HTMLImageElement> | undefined

function loadFrames() {
  if (!frames) {
    const image = new Image()
    image.src = framesURL
    frames = image.decode().then(() => image).catch(error => {
      frames = undefined
      throw error
    })
  }
  return frames
}

export function preloadDictationSoundPreview() {
  void loadFrames().catch(() => {})
}

export function DictationSoundPreview({ animation, placement }: {
  animation: DictationSoundAnimation
  placement: DictationPreferences["placement"]
}) {
  const { t } = useInterfaceLanguage()
  const canvas = useRef<HTMLCanvasElement>(null)
  const [phase, setPhase] = useState("")
  const { cue, startedAt } = animation

  useEffect(() => {
    const preview = previews.get(cue)
    const context = canvas.current?.getContext("2d")
    if (!preview || !context || !Number.isFinite(startedAt)) return
    const motion = matchMedia("(prefers-reduced-motion: reduce)")
    const clock = performance.now() - Math.max(0, Date.now() - startedAt)
    let image: HTMLImageElement | undefined
    let stopped = false
    let lastTile = -1
    let lastPhase = ""
    let nextFrame = 0
    void loadFrames().then(decoded => { if (!stopped) image = decoded }).catch(() => {})
    const draw = (now: number) => {
      const frame = Math.max(0, Math.floor((now - clock) * schedule.fps / 1000))
      if (frame >= preview.frames) {
        context.clearRect(0, 0, pixelWidth, pixelHeight)
        setPhase("")
        return
      }
      const beat = preview.beats.findLast(item => item.frame <= frame && item.cue !== "leave")
      const stage = beat?.cue ?? ""
      const tile = motion.matches ? stillFrames.get(stage) : preview.offset + frame
      if (stage !== lastPhase) {
        lastPhase = stage
        setPhase(stage)
      }
      if (image && tile !== undefined && tile !== lastTile) {
        context.clearRect(0, 0, pixelWidth, pixelHeight)
        context.drawImage(image, tile % schedule.columns * pixelWidth,
          Math.floor(tile / schedule.columns) * pixelHeight, pixelWidth, pixelHeight,
          0, 0, pixelWidth, pixelHeight)
        lastTile = tile
      }
      nextFrame = requestAnimationFrame(draw)
    }
    nextFrame = requestAnimationFrame(draw)
    return () => {
      stopped = true
      cancelAnimationFrame(nextFrame)
      context.clearRect(0, 0, pixelWidth, pixelHeight)
    }
  }, [cue, startedAt])

  return <div className="dictation-sound-preview" data-placement={placement} data-phase={phase}>
    <canvas ref={canvas} width={pixelWidth} height={pixelHeight} aria-hidden="true" />
    <span className="sr-only" role="status">{phase ? t(labels[phase]) : ""}</span>
  </div>
}
