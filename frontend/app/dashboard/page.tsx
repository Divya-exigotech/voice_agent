import { Dashboard } from "@/components/Dashboard";

export default function DashboardPage() {
  return (
    <main className="page">
      <header className="page-header">
        <h1>Payment Follow-up Agent</h1>
        <p className="muted">
          Browser voice demo · customers are processed in file order · synthetic data only
        </p>
      </header>
      <Dashboard />
    </main>
  );
}
