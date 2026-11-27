import { CallsView } from "@/components/monitoring/MonitoringViews";
import { AppShell } from "@/components/shell/AppShell";

export default function CallsPage() {
  return (
    <AppShell title="Calls" subtitle="Every call, updated live from the database">
      <CallsView />
    </AppShell>
  );
}
