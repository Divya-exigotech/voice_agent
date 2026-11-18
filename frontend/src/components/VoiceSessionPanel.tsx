import { useState } from 'react'
import { useQueryClient } from '@tanstack/react-query'
import { createSession, finalizeSession } from '@/api/queue'
import { QUEUE_KEY } from '@/hooks/useQueue'
import type { QueueState, SessionUiState } from '@/types/queue'

/**
 * M1 placeholder: no voice yet. Start/End only drive the session lifecycle
 * and the sequential queue. Voice, transcripts and Blob arrive in M3/M4.
 */
export function VoiceSessionPanel({ queue }: { queue: QueueState }) {
  const queryClient = useQueryClient()
  const [uiState, setUiState] = useState<SessionUiState>('idle')
  const [sessionId, setSessionId] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)

  async function start() {
    setError(null)
    setUiState('connecting')
    try {
      const session = await createSession()
      setSessionId(session.session_id)
      setUiState('active')
    } catch {
      setUiState('failed')
      setError('Could not start the session.')
    }
  }

  async function end() {
    if (!sessionId) return
    setUiState('ending')
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

  const busy = uiState === 'connecting' || uiState === 'ending'
  const canStart = !queue.all_completed && (uiState === 'idle' || uiState === 'completed')
  const canEnd = uiState === 'active' || (uiState === 'failed' && sessionId !== null)

  return (
    <section className="space-y-4">
      <p className="text-sm text-slate-600">
        Session state: <span className="font-medium">{uiState}</span>
      </p>
      {error && <p className="text-sm text-red-600">{error}</p>}
      <div className="flex gap-3">
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
      </div>
    </section>
  )
}
