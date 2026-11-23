import { CallActionsView } from "@/components/monitoring/MonitoringViews";
import { AppShell } from "@/components/shell/AppShell";

export default function CallActionsPage() {
  return (
    <AppShell title="Call actions" subtitle="Actions and outcomes recorded on calls">
      <CallActionsView />
    </AppShell>
  );
}
