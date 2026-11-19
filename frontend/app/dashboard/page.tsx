import { Overview } from "@/components/Overview";
import { AppShell } from "@/components/shell/AppShell";

export default function DashboardPage() {
  return (
    <AppShell title="Dashboard" subtitle="Payment follow-up overview">
      <Overview />
    </AppShell>
  );
}
