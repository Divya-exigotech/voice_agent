"use client";

import { useEffect, useState, type ReactNode } from "react";

import { Nav } from "./Nav";

const KEY = "sidebar-collapsed";

export function AppShell({
  title,
  subtitle,
  children,
}: {
  title: string;
  subtitle: string;
  children: ReactNode;
}) {
  const [collapsed, setCollapsed] = useState(false);

  // Restore the saved preference after mount (storage may be unavailable).
  useEffect(() => {
    try {
      // eslint-disable-next-line react-hooks/set-state-in-effect
      if (localStorage.getItem(KEY) === "1") setCollapsed(true);
    } catch {
      // ignore
    }
  }, []);

  function toggle() {
    setCollapsed((c) => {
      try {
        localStorage.setItem(KEY, c ? "0" : "1");
      } catch {
        // ignore
      }
      return !c;
    });
  }

  return (
    <div className={`shell${collapsed ? " shell-collapsed" : ""}`}>
      <aside className="sidebar" aria-label="Primary">
        <div className="sidebar-brand">
          {/* eslint-disable-next-line @next/next/no-img-element */}
          <img src="/brand/exigo-logo.svg" alt="Exigo Tech" className="logo-full" />
          {/* eslint-disable-next-line @next/next/no-img-element */}
          <img src="/brand/exigo-e-logo.svg" alt="Exigo Tech" className="logo-mark" />
        </div>
        <Nav />
      </aside>
      <div className="shell-main">
        <header className="topbar">
          <button
            type="button"
            className="sidebar-toggle"
            onClick={toggle}
            aria-label={collapsed ? "Open sidebar" : "Close sidebar"}
            aria-expanded={!collapsed}
          >
            <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" aria-hidden="true">
              <path d="M4 6h16M4 12h16M4 18h16" />
            </svg>
          </button>
          <div>
            <h1>{title}</h1>
            <p>{subtitle}</p>
          </div>
        </header>
        <main className="page">{children}</main>
      </div>
    </div>
  );
}
