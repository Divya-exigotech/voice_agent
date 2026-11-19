import type { ServerMessage, TranscriptTurn } from '@/types/voice'

export interface VoiceHandlers {
  onTranscript: (turn: TranscriptTurn) => void
  onError: (message: string) => void
  onClosed: () => void
}

export class VoiceStartError extends Error {
  readonly micDenied: boolean

  constructor(message: string, micDenied = false) {
    super(message)
    this.micDenied = micDenied
  }
}

const MAX_BUFFERED_BYTES = 1_000_000

function voiceSocketUrl(sessionId: string): string {
  const scheme = window.location.protocol === 'https:' ? 'wss' : 'ws'
  const base = import.meta.env.VITE_API_BASE_URL ?? '/api/v1'
  return `${scheme}://${window.location.host}${base}/ws/sessions/${sessionId}`
}

function openSocket(url: string): Promise<WebSocket> {
  return new Promise((resolve, reject) => {
    const socket = new WebSocket(url)
    socket.binaryType = 'arraybuffer'
    socket.onopen = () => resolve(socket)
    socket.onerror = () => reject(new VoiceStartError('Could not reach the voice service.'))
  })
}

/** Browser side of a voice session: mic -> WebSocket -> agent, agent audio -> speakers. */
export class VoiceClient {
  private socket: WebSocket | null = null
  private context: AudioContext | null = null
  private stream: MediaStream | null = null

  async start(sessionId: string, handlers: VoiceHandlers): Promise<void> {
    // The microphone prompt only appears here, after the user pressed Start Agent.
    try {
      this.stream = await navigator.mediaDevices.getUserMedia({
        audio: { channelCount: 1, echoCancellation: true, noiseSuppression: true },
      })
    } catch (error) {
      const denied = error instanceof DOMException && error.name === 'NotAllowedError'
      throw new VoiceStartError(
        denied
          ? 'Microphone permission was denied. Allow it in the browser and try again.'
          : 'No usable microphone was found.',
        denied,
      )
    }

    try {
      const context = new AudioContext()
      this.context = context
      await context.audioWorklet.addModule('/voice-worklets.js')
      await context.resume()

      const socket = await openSocket(voiceSocketUrl(sessionId))
      this.socket = socket

      const playback = new AudioWorkletNode(context, 'pcm-playback', {
        numberOfInputs: 0,
        outputChannelCount: [1],
      })
      playback.connect(context.destination)

      const capture = new AudioWorkletNode(context, 'pcm-capture')
      const silent = context.createGain()
      silent.gain.value = 0 // keep the capture node pulled by the graph without echoing the mic
      context.createMediaStreamSource(this.stream).connect(capture)
      capture.connect(silent).connect(context.destination)
      capture.port.onmessage = (event: MessageEvent<ArrayBuffer>) => {
        if (socket.readyState === WebSocket.OPEN && socket.bufferedAmount < MAX_BUFFERED_BYTES) {
          socket.send(event.data)
        }
      }

      socket.onmessage = (event: MessageEvent<ArrayBuffer | string>) => {
        if (typeof event.data !== 'string') {
          playback.port.postMessage({ type: 'audio', buffer: event.data }, [event.data])
          return
        }
        const message = JSON.parse(event.data) as ServerMessage
        if (message.type === 'transcript') handlers.onTranscript(message)
        else if (message.type === 'clear_playback') playback.port.postMessage({ type: 'clear' })
        else if (message.type === 'error') handlers.onError(message.message)
      }
      socket.onclose = () => {
        this.release()
        handlers.onClosed()
      }
    } catch (error) {
      this.release()
      throw error instanceof VoiceStartError
        ? error
        : new VoiceStartError('Could not start the audio session.')
    }
  }

  setMuted(muted: boolean): void {
    this.stream?.getAudioTracks().forEach((track) => {
      track.enabled = !muted
    })
  }

  /** Ask the backend to finish, then release the microphone and audio graph. */
  stop(): void {
    if (this.socket?.readyState === WebSocket.OPEN) {
      this.socket.send(JSON.stringify({ type: 'end' }))
      this.socket.close(1000)
    }
    this.release()
  }

  private release(): void {
    this.stream?.getTracks().forEach((track) => track.stop())
    this.stream = null
    void this.context?.close().catch(() => undefined)
    this.context = null
    this.socket = null
  }
}
