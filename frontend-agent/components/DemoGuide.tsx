const SCENARIOS: Array<{ outcome: string; line: string }> = [
  { outcome: "Reported paid", line: "Yes, I already paid this yesterday." },
  { outcome: "Payment promised", line: "I can pay the full amount this Friday." },
  { outcome: "Callback requested", line: "I'm driving, please call me back tomorrow evening." },
  { outcome: "Disputed invoice", line: "I don't owe this, the amount is wrong." },
  { outcome: "Needs human review", line: "I want to speak to a real person." },
  { outcome: "Stop requested", line: "Please stop contacting me." },
  { outcome: "Safety check", line: "Should I just give you my card number and CVV?" },
];

/** Collapsible operator instructions for running the demo. */
export function DemoGuide() {
  return (
    <details className="card demo-guide">
      <summary>How to run the demo</summary>
      <ol className="demo-steps">
        <li>Use a headset. The browser asks for the microphone after you press Start Agent.</li>
        <li>
          Press <strong>Start Agent</strong>. The agent greets the current customer, says it is an automated
          assistant, and states the invoice.
        </li>
        <li>Answer as the customer (speak, or type below). Talking over the agent interrupts it.</li>
        <li>
          Watch <strong>Detected outcome</strong> and any <strong>Review needed</strong> flags.
        </li>
        <li>
          Press <strong>End Session</strong>. The transcript is saved and the next customer loads. Use{" "}
          <strong>View saved transcript</strong> on the last session to read it back from storage.
        </li>
      </ol>
      <p className="eyebrow">Things to try as the customer</p>
      <ul className="demo-lines">
        {SCENARIOS.map((s) => (
          <li key={s.outcome}>
            <span className="muted">{s.outcome}:</span> “{s.line}”
          </li>
        ))}
      </ul>
      <p className="muted small">Synthetic data only. The agent never places a phone call in this demo.</p>
    </details>
  );
}
