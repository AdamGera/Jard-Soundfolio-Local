// Analyse the audible media element in place, including after scrubbing or a
// decoder fallback. Memory use is independent of the recording's duration.
export function createPlaybackWaveform(element: HTMLAudioElement) {
  const context = new AudioContext()
  const analyser = context.createAnalyser()
  analyser.fftSize = 2048
  const samples = new Float32Array(analyser.fftSize)
  let input: MediaElementAudioSourceNode | undefined
  let closed = false

  async function connect() {
    if (context.state !== "running") await context.resume()
    if (closed || input) return
    // Until the context is running, leave the element's normal output intact.
    analyser.connect(context.destination)
    input = context.createMediaElementSource(element)
    input.connect(analyser)
  }

  function draw(bars: HTMLCollection) {
    if (!input || !bars.length) return
    analyser.getFloatTimeDomainData(samples)
    // Five consecutive RMS windows show the current speech envelope. Map dBFS
    // to the icon height so ordinary quiet speech is still distinguishable.
    for (let bar = 0; bar < bars.length; bar++) {
      const start = Math.floor(bar * samples.length / bars.length)
      const end = Math.floor((bar + 1) * samples.length / bars.length)
      let energy = 0
      for (let i = start; i < end; i++) energy += samples[i] * samples[i]
      const db = 10 * Math.log10(energy / (end - start))
      const level = Math.max(0.1, Math.min(1, (db + 60) / 60))
      const line = bars[bar] as HTMLElement
      line.style.setProperty("--level", level.toFixed(3))
    }
  }

  function close() {
    closed = true
    input?.disconnect()
    analyser.disconnect()
    void context.close().catch(() => {})
  }
  return { element, connect, draw, close }
}
