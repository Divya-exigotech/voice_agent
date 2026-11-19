/* Audio worklets for the voice session. Plain JS: loaded by AudioContext.audioWorklet.
 *
 * pcm-capture  : mic (any context rate) -> 24 kHz mono PCM16 chunks of 50 ms
 * pcm-playback : 24 kHz mono PCM16 chunks -> context rate, with an instant "clear" for barge-in
 */

const TARGET_RATE = 24000
const CHUNK_SAMPLES = 1200 // 50 ms at 24 kHz

class PcmCaptureProcessor extends AudioWorkletProcessor {
  constructor() {
    super()
    this.ratio = sampleRate / TARGET_RATE // input samples per output sample
    this.position = 0
    this.tail = new Float32Array(0)
    this.chunk = new Int16Array(CHUNK_SAMPLES)
    this.filled = 0
  }

  process(inputs) {
    const channel = inputs[0] && inputs[0][0]
    if (!channel) return true

    const input = new Float32Array(this.tail.length + channel.length)
    input.set(this.tail)
    input.set(channel, this.tail.length)

    // Linear-interpolation resample to 24 kHz.
    while (this.position + 1 < input.length) {
      const index = Math.floor(this.position)
      const frac = this.position - index
      const sample = input[index] * (1 - frac) + input[index + 1] * frac
      const clamped = Math.max(-1, Math.min(1, sample))
      this.chunk[this.filled++] = clamped < 0 ? clamped * 0x8000 : clamped * 0x7fff
      if (this.filled === CHUNK_SAMPLES) {
        const out = this.chunk.slice().buffer
        this.port.postMessage(out, [out])
        this.filled = 0
      }
      this.position += this.ratio
    }

    const consumed = Math.floor(this.position)
    this.tail = input.slice(consumed)
    this.position -= consumed
    return true
  }
}

class PcmPlaybackProcessor extends AudioWorkletProcessor {
  constructor() {
    super()
    this.step = TARGET_RATE / sampleRate // source samples per output sample
    this.queue = []
    this.offset = 0
    this.port.onmessage = (event) => {
      const message = event.data
      if (message.type === 'clear') {
        this.queue = []
        this.offset = 0
      } else if (message.type === 'audio') {
        this.enqueue(new Int16Array(message.buffer))
      }
    }
  }

  enqueue(samples) {
    const length = Math.floor(samples.length / this.step)
    const out = new Float32Array(length)
    for (let i = 0; i < length; i++) {
      const src = i * this.step
      const index = Math.floor(src)
      const frac = src - index
      const next = samples[Math.min(index + 1, samples.length - 1)]
      out[i] = (samples[index] * (1 - frac) + next * frac) / 0x8000
    }
    this.queue.push(out)
  }

  process(_inputs, outputs) {
    const output = outputs[0][0]
    let written = 0
    while (written < output.length && this.queue.length > 0) {
      const head = this.queue[0]
      const take = Math.min(output.length - written, head.length - this.offset)
      output.set(head.subarray(this.offset, this.offset + take), written)
      written += take
      this.offset += take
      if (this.offset >= head.length) {
        this.queue.shift()
        this.offset = 0
      }
    }
    // Any remainder stays zero (silence).
    return true
  }
}

registerProcessor('pcm-capture', PcmCaptureProcessor)
registerProcessor('pcm-playback', PcmPlaybackProcessor)
