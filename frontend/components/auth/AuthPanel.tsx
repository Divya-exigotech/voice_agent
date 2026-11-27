"use client";

import { useRouter } from "next/navigation";
import { useState, useSyncExternalStore, type FormEvent } from "react";

type AuthMode = "login" | "register";

type AuthResponse = {
  access_token?: string;
  detail?: string;
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

export function AuthPanel() {
  const router = useRouter();
  const hydrated = useSyncExternalStore(
    subscribeToHydration,
    getHydratedSnapshot,
    getServerSnapshot,
  );
  const [mode, setMode] = useState<AuthMode>("login");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setBusy(true);
    setError("");

    const formData = new FormData(event.currentTarget);
    const payload: Record<string, string> = {
      email: String(formData.get("email") ?? ""),
      password: String(formData.get("password") ?? ""),
    };
    if (mode === "register") {
      payload.name = String(formData.get("name") ?? "");
    }

    try {
      const response = await fetch(`/api/auth/${mode}`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      const result: AuthResponse = await response.json();
      if (!response.ok) {
        throw new Error(result.detail ?? "Authentication failed. Please try again.");
      }
      if (!result.access_token) {
        throw new Error("The server did not return an access token.");
      }
      setBusy(false);
      router.replace("/dashboard");
      router.refresh();
    } catch (submitError) {
      setError(
        submitError instanceof Error
          ? submitError.message
          : "Unable to connect. Please try again.",
      );
      setBusy(false);
    }
  }

  function changeMode(nextMode: AuthMode) {
    setMode(nextMode);
    setError("");
  }

  const registering = mode === "register";

  if (!hydrated) {
    return <div className="auth-card auth-card-loading" aria-hidden="true" />;
  }

  return (
    <div className="auth-card">
      <p className="auth-product-label">Payment Follow-up Agent</p>
      <h2>{registering ? "Create your account" : "Welcome back"}</h2>
      <p className="auth-instructions">
        {registering
          ? "Register to securely access your workspace."
          : "Sign in to continue to your workspace."}
      </p>
      <div className="auth-mode-tabs" role="tablist" aria-label="Account access">
        <button
          type="button"
          role="tab"
          aria-selected={!registering}
          className={!registering ? "auth-mode-active" : ""}
          onClick={() => changeMode("login")}
        >
          Sign in
        </button>
        <button
          type="button"
          role="tab"
          aria-selected={registering}
          className={registering ? "auth-mode-active" : ""}
          onClick={() => changeMode("register")}
        >
          Register
        </button>
      </div>
      <form className="auth-form" onSubmit={submit}>
        {registering && (
          <label>
            Full name
            <input
              autoComplete="name"
              maxLength={100}
              name="name"
              placeholder="Your name"
              required
            />
          </label>
        )}
        <label>
          Email address
          <input
            autoComplete="email"
            maxLength={254}
            name="email"
            placeholder="you@company.com"
            required
            type="email"
          />
        </label>
        <label>
          Password
          <input
            autoComplete={registering ? "new-password" : "current-password"}
            maxLength={128}
            minLength={registering ? 12 : 1}
            name="password"
            placeholder={registering ? "At least 12 characters" : "Enter your password"}
            required
            type="password"
          />
        </label>
        {error && (
          <p className="auth-error" role="alert">
            {error}
          </p>
        )}
        <button className="auth-submit" type="submit" disabled={busy}>
          {busy
            ? registering
              ? "Creating account..."
              : "Signing in..."
            : registering
              ? "Create account"
              : "Sign in"}
        </button>
      </form>
      <div className="auth-divider" aria-hidden="true">
        <span />
        <span>SECURE ACCESS</span>
        <span />
      </div>
      <p className="auth-help">
        Your account is protected with a secure access token. Need help? Contact
        your administrator.
      </p>
      <footer className="auth-footer">
        <span>Privacy</span>
        <span>Terms</span>
        <a href="mailto:support@exigotech.com">Support</a>
      </footer>
    </div>
  );
}
