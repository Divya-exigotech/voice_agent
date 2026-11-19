import type { OutcomeProposal, ReviewFlag, TranscriptTurn } from "@/lib/types";

import { AGENT_SAMPLE_RATE, StreamResampler, floatToPcm16, pcm16ToBytes } from "./pcm";
import { PcmPlayer } from "./player";

/**
 * Browser side of the voice session: microphone -> backend WebSocket -> Foundry
 * voice agent, and agent audio back to the speakers. Audio on the wire is PCM16
 * little-endian, mono, 24 kHz. Transcripts are produced and stored server-side;
 * the backend pushes snapshots to the browser.
 */

export interface TranscriptUpdate {
  transcript: TranscriptTurn[];
  proposed_outcome: OutcomeProposal | null;
  review_flags?: ReviewFlag[];
}

export interface VoiceCallbacks {
  onTranscript: (update: TranscriptUpdate) => void;
  onUserSpeaking: (speaking: boolean) => void;
  onAgentSpeaking: (speaking: boolean) => void;
  /** The voice path failed (agent disconnected, socket closed, server error). */
  onError: (message: string) => void;
}

export type MicrophoneProblem = "denied" | "unavailable" | "unsupported";

export class MicrophoneError extends Error {
  constructor(
    readonly problem: MicrophoneProblem,
    message: string,
  ) {
    super(message);
    this.name = "MicrophoneError";
  }
}

/** Ask for microphone access. Call only after the operator presses Start Agent. */
export async function requestMicrophone(): Promise<MediaStream> {
  if (typeof navigator === "undefined" || !navigator.mediaDevices?.getUserMedia) {
    throw new MicrophoneError(
      "unsupported",
      "This browser cannot access a microphone here. Use a current browser on https or localhost.",
    );
  }
  try {
    return await navigator.mediaDevices.getUserMedia({
      audio: { channelCount: 1, echoCancellation: true, noiseSuppression: true, autoGainControl: true },
    });
  } catch (err) {
    const name = err instanceof DOMException ? err.name : "";
    if (name === "NotAllowedError" || name === "SecurityError") {
      throw new MicrophoneError(
        "denied",
        "Microphone permission was denied. Allow microphone access for this site and try again.",
      );
    }
    if (name === "NotReadableError") {
      throw new MicrophoneError("unavailable", "The microphone is in use by another application.");
    }
    throw new MicrophoneError("unavailable", "No usable microphone was found.");
  }
}

/** Backend voice WebSocket URL (the browser connects to FastAPI directly). */
export function voiceSocketUrl(sessionId: string): string {
  const configured = process.env.NEXT_PUBLIC_VOICE_WS_URL;
  const base =
    configured ||
    `${window.location.protocol === "https:" ? "wss" : "ws"}://${window.location.hostname}:8000`;
  return `${base.replace(/\/$/, "")}/api/sessions/${encodeURIComponent(sessionId)}/voice`;
}

const READY_TIMEOUT_MS = 60_000;

export class VoiceSession {
  private ws: WebSocket | null = null;
  private ctx: AudioContext | null = null;
  private source: MediaStreamAudioSourceNode | null = null;
  private capture: AudioWorkletNode | null = null;
  private player: PcmPlayer | null = null;
  private resampler: StreamResampler | null = null;
  private muted = false;
  private stopped = false;

  constructor(
    private readonly stream: MediaStream,
    private readonly callbacks: VoiceCallbacks,
  ) {}

  /** Open audio + socket; resolves when the backend's agent session is ready. */
  async connect(sessionId: string): Promise<TranscriptUpdate> {
    const ctx = new AudioContext();
    this.ctx = ctx;
    await ctx.audioWorklet.addModule("/worklets/pcm-capture.js");
    if (ctx.state === "suspended") await ctx.resume();
    this.player = new PcmPlayer(ctx, this.callbacks.onAgentSpeaking);
    this.resampler = new StreamResampler(ctx.sampleRate, AGENT_SAMPLE_RATE);

    const ready = await this.openSocket(voiceSocketUrl(sessionId));

    // Start streaming the microphone only once the agent session is ready.
    this.source = ctx.createMediaStreamSource(this.stream);
    this.capture = new AudioWorkletNode(ctx, "pcm-capture");
    this.capture.port.onmessage = (event: MessageEvent<Float32Array>) => this.sendAudio(event.data);
    this.source.connect(this.capture);
    this.capture.connect(ctx.destination); // outputs silence; keeps the node pulled
    return ready;
  }

  setMuted(muted: boolean): void {
    this.muted = muted;
    for (const track of this.stream.getAudioTracks()) track.enabled = !muted;
  }

  async stop(): Promise<void> {
    if (this.stopped) return;
    this.stopped = true;
    const ws = this.ws;
    if (ws && ws.readyState === WebSocket.OPEN) {
      try {
        ws.send(JSON.stringify({ type: "stop" }));
      } catch {
        // ignore
      }
      ws.close(1000);
    }
    this.capture?.port.close();
    this.capture?.disconnect();
    this.source?.disconnect();
    for (const track of this.stream.getTracks()) track.stop();
    this.player?.flush();
    if (this.ctx && this.ctx.state !== "closed") await this.ctx.close();
  }

  private openSocket(url: string): Promise<TranscriptUpdate> {
    return new Promise((resolve, reject) => {
      const ws = new WebSocket(url);
      ws.binaryType = "arraybuffer";
      this.ws = ws;
      let ready = false;
      const timer = setTimeout(() => {
        if (!ready) {
          reject(new Error("The voice agent did not become ready in time"));
          ws.close();
        }
      }, READY_TIMEOUT_MS);

      ws.onmessage = (event: MessageEvent) => {
        if (event.data instanceof ArrayBuffer) {
          this.player?.enqueue(event.data);
          return;
        }
        let message: { type?: string; [key: string]: unknown };
        try {
          message = JSON.parse(String(event.data));
        } catch {
          return;
        }
        switch (message.type) {
          case "ready":
            ready = true;
            clearTimeout(timer);
            this.callbacks.onTranscript(message as unknown as TranscriptUpdate);
            resolve(message as unknown as TranscriptUpdate);
            break;
          case "transcript":
            this.callbacks.onTranscript(message as unknown as TranscriptUpdate);
            break;
          case "speech_started":
            this.player?.flush(); // barge-in: stop the agent talking over the customer
            this.callbacks.onUserSpeaking(true);
            break;
          case "speech_stopped":
            this.callbacks.onUserSpeaking(false);
            break;
          case "agent_closed":
            this.fail(`The voice agent disconnected: ${String(message.reason ?? "unknown reason")}`);
            break;
          case "error":
            if (!ready) {
              clearTimeout(timer);
              reject(new Error(String(message.message ?? "Voice connection error")));
            } else {
              this.fail(String(message.message ?? "Voice connection error"));
            }
            break;
          default:
            break; // session_ended, response_done, ...
        }
      };

      ws.onclose = (event: CloseEvent) => {
        clearTimeout(timer);
        if (!ready) {
          reject(new Error(`Could not open the voice connection (code ${event.code})`));
        } else if (!this.stopped && event.code !== 1000) {
          this.fail(`The voice connection closed unexpectedly (code ${event.code})`);
        }
      };
    });
  }

  private sendAudio(frame: Float32Array): void {
    const ws = this.ws;
    if (this.muted || this.stopped || !ws || ws.readyState !== WebSocket.OPEN || !this.resampler) return;
    const pcm = floatToPcm16(this.resampler.process(frame));
    if (pcm.length > 0) ws.send(pcm16ToBytes(pcm));
  }

  private fail(message: string): void {
    if (this.stopped) return;
    this.player?.flush();
    this.callbacks.onError(message);
  }
}
