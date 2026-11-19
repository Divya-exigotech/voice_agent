export interface TranscriptTurn {
  turn: number
  speaker: 'agent' | 'user'
  text: string
  timestamp: string
}

export type MicState = 'off' | 'on' | 'muted' | 'denied'

/** JSON messages sent by the backend over the voice WebSocket. */
export type ServerMessage =
  | { type: 'status'; state: string }
  | { type: 'clear_playback' }
  | ({ type: 'transcript' } & TranscriptTurn)
  | { type: 'error'; message: string }
