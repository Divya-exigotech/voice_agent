import { AuthPanel } from "@/components/auth/AuthPanel";

export default function HomePage() {
  return (
    <main className="auth-page">
      <section className="auth-brand-panel" aria-label="Project information">
        <div className="auth-brand">
          {/* eslint-disable-next-line @next/next/no-img-element */}
          <img src="/brand/exigo-logo.svg" alt="Exigo Tech" />
        </div>
        <div className="auth-brand-copy">
          <p className="auth-eyebrow">PAYMENT FOLLOW-UP AGENT</p>
          <h1>Your gateway to smarter customer conversations.</h1>
          <p className="auth-description">
            Manage customer follow-ups, payment commitments, and live
            conversation insights in one secure workspace.
          </p>
        </div>
        <p className="auth-copyright">© Exigo Tech. All rights reserved.</p>
      </section>
      <section className="auth-content-panel" aria-label="Account access">
        <AuthPanel />
      </section>
    </main>
  );
}
