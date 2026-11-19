import { SessionsView } from "@/components/SessionsView";
import { AppShell } from "@/components/shell/AppShell";

export default function SessionsPage() {
  return (
    <AppShell title="Sessions" subtitle="Past sessions and their saved transcripts">
      <SessionsView />
    </AppShell>
  );
}
