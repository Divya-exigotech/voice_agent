/** Audio format used with the voice agent: PCM16 little-endian, mono, 24 kHz. */
export const AGENT_SAMPLE_RATE = 24_000;

/**
 * Streaming linear-interpolation resampler. Keeps its read position and the
 * previous sample between chunks, so chunked output matches one-shot output.
 */
export class StreamResampler {
  private readonly step: number;
  private position = 1; // read position in [previous sample, ...chunk]
  private previous = 0;

  constructor(
    readonly fromRate: number,
    readonly toRate: number,
  ) {
    if (fromRate <= 0 || toRate <= 0) throw new Error("Sample rates must be positive");
    this.step = fromRate / toRate;
  }

  process(input: Float32Array): Float32Array {
    if (this.fromRate === this.toRate) return input.slice();
    const n = input.length;
    if (n === 0) return new Float32Array(0);

    const out: number[] = [];
    let p = this.position;
    while (p < n) {
      const i = Math.floor(p);
      const a = i === 0 ? this.previous : input[i - 1];
      const b = input[i];
      out.push(a + (b - a) * (p - i));
      p += this.step;
    }
    this.position = p - n;
    this.previous = input[n - 1];
    return Float32Array.from(out);
  }
}

/** Float samples in [-1, 1] -> PCM16 (scaled by 32768, clamped; inverse of pcm16ToFloat). */
export function floatToPcm16(samples: Float32Array): Int16Array {
  const out = new Int16Array(samples.length);
  for (let i = 0; i < samples.length; i++) {
    out[i] = Math.max(-0x8000, Math.min(0x7fff, Math.round(samples[i] * 0x8000)));
  }
  return out;
}

/** PCM16 little-endian bytes -> float samples in [-1, 1). */
export function pcm16ToFloat(bytes: ArrayBuffer): Float32Array<ArrayBuffer> {
  const view = new DataView(bytes);
  const count = Math.floor(bytes.byteLength / 2);
  const out = new Float32Array(count);
  for (let i = 0; i < count; i++) {
    out[i] = view.getInt16(i * 2, true) / 0x8000;
  }
  return out;
}

/** PCM16 samples -> little-endian bytes (explicit, independent of platform endianness). */
export function pcm16ToBytes(samples: Int16Array): ArrayBuffer {
  const buffer = new ArrayBuffer(samples.length * 2);
  const view = new DataView(buffer);
  for (let i = 0; i < samples.length; i++) view.setInt16(i * 2, samples[i], true);
  return buffer;
}
