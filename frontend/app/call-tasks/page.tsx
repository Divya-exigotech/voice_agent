import { CallTasksView } from "@/components/monitoring/MonitoringViews";
import { AppShell } from "@/components/shell/AppShell";

export default function CallTasksPage() {
  return (
    <AppShell title="Call tasks" subtitle="The call queue and its status, updated live">
      <CallTasksView />
    </AppShell>
  );
}
