import { LatencyView } from "@/components/monitoring/LatencyView";
import { AppShell } from "@/components/shell/AppShell";

export default function LatencyPage() {
  return (
    <AppShell title="Latency" subtitle="Voice reply, database and API timings, updated live">
      <LatencyView />
    </AppShell>
  );
}
