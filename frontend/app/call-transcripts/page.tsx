import { CallTranscriptsView } from "@/components/monitoring/MonitoringViews";
import { AppShell } from "@/components/shell/AppShell";

export default function CallTranscriptsPage() {
  return (
    <AppShell title="Call transcripts" subtitle="Every spoken turn, saved to the database live">
      <CallTranscriptsView />
    </AppShell>
  );
}
