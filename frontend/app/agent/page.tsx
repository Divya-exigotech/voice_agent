import { Dashboard } from "@/components/Dashboard";
import { AppShell } from "@/components/shell/AppShell";

export default function AgentPage() {
  return (
    <AppShell
      title="Agent"
      subtitle="Start and end sessions · the live transcript appears alongside the controls"
    >
      <Dashboard />
    </AppShell>
  );
}
