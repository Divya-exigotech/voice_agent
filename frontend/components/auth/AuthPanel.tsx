"use client";

import { useState, useSyncExternalStore } from "react";

// The backend sends the browser to Microsoft and back; it redirects here with
// `?error=<code>` when sign-in does not complete.
const SIGN_IN_URL = "/api/auth/microsoft/login";

const ERROR_MESSAGES: Record<string, string> = {
  cancelled: "Sign-in was cancelled. Choose your Microsoft account to continue.",
  expired: "Your sign-in attempt expired. Please try again.",
  unavailable: "Microsoft sign-in is unavailable right now. Please try again shortly.",
  failed:
    "Microsoft sign-in failed. Use your organisation account, or contact your administrator.",
};

function subscribeToHydration(): () => void {
  return () => {};
}

function getHydratedSnapshot(): boolean {
  return true;
}

function getServerSnapshot(): boolean {
  return false;
}

function signInError(): string {
  const code = new URLSearchParams(window.location.search).get("error");
  if (!code) return "";
  return ERROR_MESSAGES[code] ?? ERROR_MESSAGES.failed;
}

function MicrosoftLogo() {
  return (
    <svg aria-hidden="true" width="18" height="18" viewBox="0 0 21 21">
      <rect x="1" y="1" width="9" height="9" fill="#f25022" />
      <rect x="11" y="1" width="9" height="9" fill="#7fba00" />
      <rect x="1" y="11" width="9" height="9" fill="#00a4ef" />
      <rect x="11" y="11" width="9" height="9" fill="#ffb900" />
    </svg>
  );
}

export function AuthPanel() {
  const hydrated = useSyncExternalStore(
    subscribeToHydration,
    getHydratedSnapshot,
    getServerSnapshot,
  );
  const [redirecting, setRedirecting] = useState(false);

  if (!hydrated) {
    return <div className="auth-card auth-card-loading" aria-hidden="true" />;
  }

  const error = signInError();

  return (
    <div className="auth-card">
      <p className="auth-product-label">Payment Follow-up Agent</p>
      <h2>Welcome back</h2>
      <p className="auth-instructions">
        Sign in with your Microsoft work account to continue to your workspace.
      </p>
      {error && !redirecting && (
        <p className="auth-error auth-sso-error" role="alert">
          {error}
        </p>
      )}
      <a
        className="auth-submit auth-microsoft"
        href={SIGN_IN_URL}
        aria-disabled={redirecting}
        onClick={(event) => {
          if (redirecting) event.preventDefault();
          setRedirecting(true);
        }}
      >
        <MicrosoftLogo />
        {redirecting ? "Redirecting to Microsoft..." : "Sign in with Microsoft"}
      </a>
      <div className="auth-divider" aria-hidden="true">
        <span />
        <span>SECURE ACCESS</span>
        <span />
      </div>
      <p className="auth-help">
        Access is limited to your organisation&apos;s Microsoft Entra ID accounts.
        Need help? Contact your administrator.
      </p>
      <footer className="auth-footer">
        <span>Privacy</span>
        <span>Terms</span>
        <a href="mailto:support@exigotech.com">Support</a>
      </footer>
    </div>
  );
}
