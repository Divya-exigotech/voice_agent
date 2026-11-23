import { CallEventsView } from "@/components/monitoring/MonitoringViews";
import { AppShell } from "@/components/shell/AppShell";

export default function CallEventsPage() {
  return (
    <AppShell title="Call events" subtitle="Call lifecycle events as they happen">
      <CallEventsView />
    </AppShell>
  );
}
