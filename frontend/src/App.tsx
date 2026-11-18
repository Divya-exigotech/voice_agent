import { CustomerCard } from '@/components/CustomerCard'
import { VoiceSessionPanel } from '@/components/VoiceSessionPanel'
import { useQueue } from '@/hooks/useQueue'

function App() {
  const { data: queue, isLoading, isError } = useQueue()

  return (
    <main className="mx-auto min-h-screen max-w-2xl space-y-6 bg-slate-50 p-8">
      <h1 className="text-2xl font-semibold text-slate-800">Payment Follow-up Agent</h1>

      {isLoading && <p className="text-slate-600">Loading customers…</p>}
      {isError && <p className="text-red-600">Could not load the customer queue.</p>}

      {queue && (
        <>
          <p className="text-sm font-medium text-slate-700">
            {queue.all_completed
              ? 'All customers completed'
              : `Customer ${queue.current_position} of ${queue.total} · ${queue.remaining} remaining`}
          </p>
          {queue.current_customer && <CustomerCard customer={queue.current_customer} />}
          <VoiceSessionPanel queue={queue} />
        </>
      )}
    </main>
  )
}

export default App
