import { useEffect, useRef } from 'react'
import type { TranscriptTurn } from '@/types/voice'

export function TranscriptPanel({ turns }: { turns: TranscriptTurn[] }) {
  const endRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    endRef.current?.scrollIntoView({ block: 'end' })
  }, [turns.length])

  return (
    <section className="rounded-xl border border-slate-200 bg-white p-4">
      <h2 className="mb-3 text-sm font-semibold text-slate-700">Live transcript</h2>
      <div role="log" aria-live="polite" className="max-h-72 space-y-2 overflow-y-auto">
        {turns.length === 0 && <p className="text-sm text-slate-400">No turns yet.</p>}
        {turns.map((turn) => (
          <p key={turn.turn} className="text-sm text-slate-800">
            <span
              className={
                turn.speaker === 'agent' ? 'font-semibold text-indigo-700' : 'font-semibold text-emerald-700'
              }
            >
              {turn.speaker === 'agent' ? 'Agent' : 'You'}
            </span>
            <span className="ml-2 text-xs text-slate-400">
              {new Date(turn.timestamp).toLocaleTimeString()}
            </span>
            <br />
            {turn.text}
          </p>
        ))}
        <div ref={endRef} />
      </div>
    </section>
  )
}
