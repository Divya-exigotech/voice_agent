"use client";

import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";

const SIGN_OUT_TIMEOUT_MS = 10_000;

type SessionUser = {
  name: string;
  email: string;
};

type SessionResponse = {
  user: SessionUser | null;
};

function getInitials(name: string): string {
  return name
    .trim()
    .split(/\s+/)
    .slice(0, 2)
    .map((part) => part[0]?.toUpperCase() ?? "")
    .join("");
}

export function SignOutButton() {
  const router = useRouter();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [account, setAccount] = useState<SessionUser | null>(null);

  useEffect(() => {
    const controller = new AbortController();

    async function loadAccount() {
      try {
        const response = await fetch("/api/auth/session", {
          cache: "no-store",
          signal: controller.signal,
        });
        if (!response.ok) {
          throw new Error("Unable to load account details.");
        }
        const session: SessionResponse = await response.json();
        setAccount(session.user);
      } catch (accountError) {
        if (!controller.signal.aborted) {
          setError(
            accountError instanceof Error
              ? accountError.message
              : "Unable to load account details.",
          );
        }
      }
    }

    void loadAccount();
    return () => controller.abort();
  }, []);

  async function signOut() {
    if (busy) return;

    setBusy(true);
    setError("");
    const controller = new AbortController();
    const timeoutId = window.setTimeout(
      () => controller.abort(),
      SIGN_OUT_TIMEOUT_MS,
    );

    try {
      const response = await fetch("/api/auth/logout", {
        method: "POST",
        signal: controller.signal,
      });
      if (!response.ok) {
        throw new Error("Sign-out failed.");
      }
      setBusy(false);
      router.replace("/");
      router.refresh();
    } catch (signOutError) {
      setError(
        signOutError instanceof DOMException &&
          signOutError.name === "AbortError"
          ? "Sign-out timed out. Check your connection and try again."
          : "Sign out failed. Please try again.",
      );
    } finally {
      window.clearTimeout(timeoutId);
      setBusy(false);
    }
  }

  return (
    <div className="sidebar-account">
      <div className="sidebar-account-avatar" aria-hidden="true">
        {account ? getInitials(account.name) : "U"}
      </div>
      <div className="sidebar-account-details">
        <span className="sidebar-account-name">
          {account?.name ?? "Your account"}
        </span>
        <span className="sidebar-account-email">
          {account?.email ?? " "}
        </span>
      </div>
      <button
        className="sign-out-button"
        type="button"
        aria-label={busy ? "Signing out" : "Sign out"}
        title="Sign out"
        onClick={signOut}
        disabled={busy}
      >
        <svg
          width="18"
          height="18"
          viewBox="0 0 24 24"
          fill="none"
          stroke="currentColor"
          strokeWidth="1.8"
          strokeLinecap="round"
          strokeLinejoin="round"
          aria-hidden="true"
        >
          <path d="M10 17l5-5-5-5M15 12H3" />
          <path d="M12 3h6a2 2 0 0 1 2 2v14a2 2 0 0 1-2 2h-6" />
        </svg>
      </button>
      {error && <span className="sidebar-account-error" role="alert">{error}</span>}
    </div>
  );
}
