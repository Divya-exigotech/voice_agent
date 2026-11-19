import { describe, expect, it } from "vitest";

import {
  StreamResampler,
  floatToPcm16,
  pcm16ToBytes,
  pcm16ToFloat,
} from "@/lib/voice/pcm";

function ramp(length: number): Float32Array {
  return Float32Array.from({ length }, (_, i) => i / length);
}

describe("StreamResampler", () => {
  it("downsamples 48 kHz to 24 kHz by taking every second sample of a ramp", () => {
    const input = ramp(480);
    const out = new StreamResampler(48_000, 24_000).process(input);
    expect(out.length).toBe(240);
    expect(out[0]).toBeCloseTo(input[0]);
    expect(out[10]).toBeCloseTo(input[20]);
  });

  it("produces the same output whether audio arrives in one chunk or many", () => {
    const input = ramp(4410);
    const whole = new StreamResampler(44_100, 24_000).process(input);

    const chunked = new StreamResampler(44_100, 24_000);
    const parts: number[] = [];
    for (let i = 0; i < input.length; i += 128) {
      parts.push(...chunked.process(input.subarray(i, i + 128)));
    }
    expect(parts.length).toBe(whole.length);
    parts.forEach((value, i) => expect(value).toBeCloseTo(whole[i], 6));
  });

  it("upsamples 16 kHz to 24 kHz with interpolated values", () => {
    const resampler = new StreamResampler(16_000, 24_000);
    const out = resampler.process(Float32Array.from([0, 0.3, 0.6, 0.9]));
    // The sample at the chunk boundary is emitted with the next chunk.
    expect(Array.from(out).map((v) => +v.toFixed(3))).toEqual([0, 0.2, 0.4, 0.6, 0.8]);
    const next = resampler.process(Float32Array.from([1.2, 1.5]));
    expect(Array.from(next).map((v) => +v.toFixed(3))).toEqual([1, 1.2, 1.4]);
  });

  it("passes audio through when the rates match", () => {
    const input = Float32Array.from([0.1, -0.2]);
    expect(Array.from(new StreamResampler(24_000, 24_000).process(input))).toEqual(Array.from(input));
  });
});

describe("PCM16 conversion", () => {
  it("clamps and scales floats to int16", () => {
    expect(Array.from(floatToPcm16(Float32Array.from([0, 1, -1, 2, -2, 0.5])))).toEqual([
      0, 32767, -32768, 32767, -32768, 16384,
    ]);
  });

  it("round-trips through little-endian bytes", () => {
    const samples = Int16Array.from([0, 1, -1, 32767, -32768]);
    const bytes = pcm16ToBytes(samples);
    expect(new Uint8Array(bytes).slice(0, 6)).toEqual(Uint8Array.from([0, 0, 1, 0, 0xff, 0xff]));
    const floats = pcm16ToFloat(bytes);
    expect(floats[1]).toBeCloseTo(1 / 32768);
    expect(floats[4]).toBe(-1);
    expect(Array.from(floatToPcm16(floats))).toEqual([0, 1, -1, 32767, -32768]);
  });

  it("ignores a trailing odd byte", () => {
    expect(pcm16ToFloat(new Uint8Array([0, 0, 1]).buffer).length).toBe(1);
  });
});
