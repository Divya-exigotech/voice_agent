import type { QueueState } from "@/lib/types";

export function SequenceProgress({ queue }: { queue: QueueState }) {
  const done = queue.current_index;
  const percent = queue.total ? Math.round((done / queue.total) * 100) : 0;

  return (
    <section className="card progress" aria-label="Queue progress">
      <div className="progress-header">
        <strong>
          {queue.total === 0
            ? "No customers queued"
            : queue.completed
            ? `All ${queue.total} customers completed`
            : `Customer ${queue.position} of ${queue.total}`}
        </strong>
        <span className="muted">{queue.remaining} remaining</span>
      </div>
      <ol className="steps" aria-hidden="true">
        {Array.from({ length: queue.total }, (_, i) => (
          <li
            key={i}
            className={`step ${i < done ? "step-done" : i === queue.current_index && !queue.completed ? "step-current" : ""}`}
          >
            {i < done ? "✓" : i + 1}
          </li>
        ))}
      </ol>
      <div
        className="progress-track"
        role="progressbar"
        aria-valuemin={0}
        aria-valuemax={queue.total}
        aria-valuenow={done}
        aria-label={`${done} of ${queue.total} processed`}
      >
        <div className="progress-fill" style={{ width: `${percent}%` }} />
      </div>
    </section>
  );
}
