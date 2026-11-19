class SoundfolioCapture extends AudioWorkletProcessor {
  constructor() {
    super()
    this.buffer = new Float32Array(Math.round(sampleRate / 12))
    this.used = 0
  }

  send(samples) {
    this.port.postMessage(samples, [samples.buffer])
  }

  process(inputs) {
    const samples = inputs[0]?.[0]
    if (!samples) return true

    let offset = 0
    while (offset < samples.length) {
      const count = Math.min(samples.length - offset, this.buffer.length - this.used)
      this.buffer.set(samples.subarray(offset, offset + count), this.used)
      this.used += count
      offset += count
      if (this.used === this.buffer.length) {
        const length = this.buffer.length
        this.send(this.buffer)
        this.buffer = new Float32Array(length)
        this.used = 0
      }
    }
    return true
  }
}

registerProcessor("soundfolio-capture", SoundfolioCapture)
