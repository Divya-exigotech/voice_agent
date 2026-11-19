import { AGENT_SAMPLE_RATE, pcm16ToFloat } from "./pcm";

/**
 * Plays agent PCM16 (24 kHz) chunks back-to-back on an AudioContext.
 * AudioBuffers are created at 24 kHz and the browser resamples to the
 * context rate. `flush()` stops everything immediately (barge-in).
 */
export class PcmPlayer {
  private nextStart = 0;
  private readonly sources = new Set<AudioBufferSourceNode>();
  private speaking = false;

  constructor(
    private readonly ctx: AudioContext,
    private readonly onSpeakingChange: (speaking: boolean) => void = () => {},
  ) {}

  enqueue(pcm: ArrayBuffer): void {
    const samples = pcm16ToFloat(pcm);
    if (samples.length === 0) return;

    const buffer = this.ctx.createBuffer(1, samples.length, AGENT_SAMPLE_RATE);
    buffer.copyToChannel(samples, 0);
    const source = this.ctx.createBufferSource();
    source.buffer = buffer;
    source.connect(this.ctx.destination);

    const startAt = Math.max(this.ctx.currentTime + 0.02, this.nextStart);
    source.start(startAt);
    this.nextStart = startAt + buffer.duration;
    this.sources.add(source);
    this.setSpeaking(true);

    source.onended = () => {
      this.sources.delete(source);
      if (this.sources.size === 0) this.setSpeaking(false);
    };
  }

  flush(): void {
    for (const source of this.sources) {
      try {
        source.onended = null;
        source.stop();
      } catch {
        // already stopped
      }
    }
    this.sources.clear();
    this.nextStart = 0;
    this.setSpeaking(false);
  }

  private setSpeaking(speaking: boolean): void {
    if (speaking !== this.speaking) {
      this.speaking = speaking;
      this.onSpeakingChange(speaking);
    }
  }
}
