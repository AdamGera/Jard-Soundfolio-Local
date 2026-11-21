import { useLayoutEffect, useRef, type ReactNode } from "react"

export function LiveTranscriptScroll({ className, following, hasText, children }: {
  className: string; following: boolean; hasText: boolean; children: ReactNode
}) {
  const viewport = useRef<HTMLDivElement>(null)
  const content = useRef<HTMLDivElement>(null)

  useLayoutEffect(() => {
    const area = viewport.current
    const body = content.current
    if (!following || !area || !body) return
    let atEnd = true
    let end = 0

    const layout = () => {
      if (!area.clientHeight) return
      end = Math.max(0, area.scrollHeight - area.clientHeight)
      if (atEnd) area.scrollTop = end
    }
    const scrolled = () => {
      if (area.clientHeight) atEnd = end - area.scrollTop <= 2
    }

    const observer = new ResizeObserver(layout)
    observer.observe(area)
    observer.observe(body)
    area.addEventListener("scroll", scrolled, { passive: true })
    layout()
    return () => {
      observer.disconnect()
      area.removeEventListener("scroll", scrolled)
    }
  }, [following])

  return <div className={className} ref={viewport} data-live-transcript tabIndex={hasText || following ? 0 : undefined}>
    <div ref={content} className="live-transcript-content">{children}</div>
  </div>
}
