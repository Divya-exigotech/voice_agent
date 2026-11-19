import { useEffect, useRef, useState } from 'react'
import { useQueryClient } from '@tanstack/react-query'
import { createSession, finalizeSession } from '@/api/queue'
import { TranscriptPanel } from '@/components/TranscriptPanel'
import { QUEUE_KEY } from '@/hooks/useQueue'
import { VoiceClient, VoiceStartError } from '@/lib/voiceClient'
import type { QueueState, SessionUiState } from '@/types/queue'
import type { MicState, TranscriptTurn } from '@/types/voice'

export function VoiceSessionPanel({ queue }: { queue: QueueState }) {
  const queryClient = useQueryClient()
  const [client] = useState(() => new VoiceClient())
  const [uiState, setUiState] = useState<SessionUiState>('idle')
  const [mic, setMic] = useState<MicState>('off')
  const [sessionId, setSessionId] = useState<string | null>(null)
  const [turns, setTurns] = useState<TranscriptTurn[]>([])
  const [error, setError] = useState<string | null>(null)
  const uiStateRef = useRef(uiState)
  uiStateRef.current = uiState

  useEffect(() => () => client.stop(), [client])

  async function start() {
    setError(null)
    setTurns([])
    setUiState('connecting')
    // Reuse the open session after a failed start (e.g. mic denied) so no customer is skipped.
    let id = sessionId
    if (id === null) {
      try {
        id = (await createSession()).session_id
        setSessionId(id)
      } catch {
        setUiState('failed')
        setError('Could not start the session.')
        return
      }
    }
    try {
      await client.start(id, {
        onTranscript: (turn) => setTurns((previous) => [...previous, turn]),
        onError: (message) => setError(message),
        onClosed: () => {
          setMic('off')
          if (uiStateRef.current === 'active') {
            setUiState('failed')
            setError((previous) => previous ?? 'The voice connection closed.')
          }
        },
      })
      setMic('on')
      setUiState('active')
    } catch (caught) {
      const denied = caught instanceof VoiceStartError && caught.micDenied
      setMic(denied ? 'denied' : 'off')
      setError(caught instanceof VoiceStartError ? caught.message : 'Could not start the voice session.')
      setUiState('failed')
    }
  }

  async function end() {
    if (!sessionId) return
    setUiState('ending')
    client.stop()
    setMic('off')
    try {
      const next = await finalizeSession(sessionId)
      queryClient.setQueryData(QUEUE_KEY, next)
      setSessionId(null)
      setUiState('idle')
    } catch {
      setUiState('failed')
      setError('Could not finalize the session. Try ending it again.')
    }
  }

  function toggleMute() {
    const muted = mic !== 'muted'
    client.setMuted(muted)
    setMic(muted ? 'muted' : 'on')
  }

  const busy = uiState === 'connecting' || uiState === 'ending'
  // After a failed start the open session is reused, so Start Agent doubles as "retry".
  const canStart =
    !queue.all_completed &&
    (uiState === 'idle' || uiState === 'completed' || uiState === 'failed')
  const canEnd = uiState === 'active' || (uiState === 'failed' && sessionId !== null)

  return (
    <section className="space-y-4">
      <div className="flex gap-6 text-sm text-slate-600">
        <p>
          Session state: <span className="font-medium">{uiState}</span>
        </p>
        <p>
          Mic: <span className="font-medium">{mic}</span>
        </p>
      </div>
      {error && (
        <p role="alert" className="text-sm text-red-600">
          {error}
        </p>
      )}
      <div className="flex flex-wrap gap-3">
        <button
          type="button"
          onClick={start}
          disabled={!canStart || busy}
          className="rounded-lg bg-indigo-600 px-6 py-3 font-semibold text-white disabled:opacity-40"
        >
          Start Agent
        </button>
        <button
          type="button"
          onClick={end}
          disabled={!canEnd || busy}
          className="rounded-lg border border-slate-300 px-6 py-3 font-semibold text-slate-800 disabled:opacity-40"
        >
          End Session
        </button>
        <button
          type="button"
          onClick={toggleMute}
          disabled={uiState !== 'active'}
          className="rounded-lg border border-slate-300 px-4 py-3 text-slate-800 disabled:opacity-40"
        >
          {mic === 'muted' ? 'Unmute' : 'Mute'}
        </button>
      </div>
      <TranscriptPanel turns={turns} />
    </section>
  )
}
