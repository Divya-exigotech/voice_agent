// AudioWorklet: batches raw microphone samples (mono, AudioContext sample rate)
// and posts them to the main thread, which resamples to 24 kHz PCM16.
// Kept logic-free on purpose; conversion lives in lib/voice/pcm.ts (unit tested).

const BATCH_SAMPLES = 2048; // ~43 ms at 48 kHz

class PcmCaptureProcessor extends AudioWorkletProcessor {
  constructor() {
    super();
    this.buffer = new Float32Array(BATCH_SAMPLES);
    this.length = 0;
  }

  process(inputs) {
    const channel = inputs[0] && inputs[0][0];
    if (channel) {
      let offset = 0;
      while (offset < channel.length) {
        const take = Math.min(channel.length - offset, BATCH_SAMPLES - this.length);
        this.buffer.set(channel.subarray(offset, offset + take), this.length);
        this.length += take;
        offset += take;
        if (this.length === BATCH_SAMPLES) {
          const batch = this.buffer;
          this.port.postMessage(batch, [batch.buffer]);
          this.buffer = new Float32Array(BATCH_SAMPLES);
          this.length = 0;
        }
      }
    }
    return true; // keep processing
  }
}

registerProcessor("pcm-capture", PcmCaptureProcessor);
