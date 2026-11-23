import { AuditLogsView } from "@/components/monitoring/MonitoringViews";
import { AppShell } from "@/components/shell/AppShell";

export default function AuditLogsPage() {
  return (
    <AppShell title="Audit logs" subtitle="Who did what, most recent first">
      <AuditLogsView />
    </AppShell>
  );
}
