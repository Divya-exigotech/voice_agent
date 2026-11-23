"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import type { ReactNode } from "react";

const ICON = {
  width: 18,
  height: 18,
  viewBox: "0 0 24 24",
  fill: "none",
  stroke: "currentColor",
  strokeWidth: 1.8,
  strokeLinecap: "round" as const,
  strokeLinejoin: "round" as const,
  "aria-hidden": true,
};

const GROUPS: { label: string; items: { href: string; label: string; icon: ReactNode }[] }[] = [
  {
    label: "Workspace",
    items: [
      {
        href: "/dashboard",
        label: "Dashboard",
        icon: (
          <svg {...ICON}>
            <rect x="3" y="3" width="7" height="9" rx="1.5" />
            <rect x="14" y="3" width="7" height="5" rx="1.5" />
            <rect x="14" y="12" width="7" height="9" rx="1.5" />
            <rect x="3" y="16" width="7" height="5" rx="1.5" />
          </svg>
        ),
      },
    ],
  },
  {
    label: "Module",
    items: [
      {
        href: "/agent",
        label: "Agent",
        icon: (
          <svg {...ICON}>
            <rect x="9" y="3" width="6" height="11" rx="3" />
            <path d="M5 11a7 7 0 0 0 14 0M12 18v3" />
          </svg>
        ),
      },
    ],
  },
  {
    label: "Records",
    items: [
      {
        href: "/sessions",
        label: "Sessions",
        icon: (
          <svg {...ICON}>
            <path d="M6 3h9l4 4v14H6z" />
            <path d="M14 3v5h5M9 13h7M9 17h7" />
          </svg>
        ),
      },
      {
        href: "/calls",
        label: "Calls",
        icon: (
          <svg {...ICON}>
            <path d="M22 16.9v3a2 2 0 0 1-2.2 2 19.8 19.8 0 0 1-8.6-3.1 19.5 19.5 0 0 1-6-6A19.8 19.8 0 0 1 2.1 4.2 2 2 0 0 1 4.1 2h3a2 2 0 0 1 2 1.7c.1 1 .4 1.9.7 2.8a2 2 0 0 1-.5 2.1L8.1 9.9a16 16 0 0 0 6 6l1.3-1.3a2 2 0 0 1 2.1-.4c.9.3 1.8.6 2.8.7a2 2 0 0 1 1.7 2z" />
          </svg>
        ),
      },
      {
        href: "/call-tasks",
        label: "Call tasks",
        icon: (
          <svg {...ICON}>
            <path d="M9 11l3 3L22 4M21 12v7a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h11" />
          </svg>
        ),
      },
      {
        href: "/call-events",
        label: "Call events",
        icon: (
          <svg {...ICON}>
            <path d="M22 12h-4l-3 9L9 3l-3 9H2" />
          </svg>
        ),
      },
      {
        href: "/call-transcripts",
        label: "Call transcripts",
        icon: (
          <svg {...ICON}>
            <path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2zM8 9h8M8 13h5" />
          </svg>
        ),
      },
      {
        href: "/call-actions",
        label: "Call actions",
        icon: (
          <svg {...ICON}>
            <path d="M13 2L3 14h9l-1 8 10-12h-9l1-8z" />
          </svg>
        ),
      },
      {
        href: "/latency",
        label: "Latency",
        icon: (
          <svg {...ICON}>
            <circle cx="12" cy="13" r="8" />
            <path d="M12 9v4l2.5 2M9 2h6" />
          </svg>
        ),
      },
      {
        href: "/audit-logs",
        label: "Audit logs",
        icon: (
          <svg {...ICON}>
            <path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10zM9 12l2 2 4-4" />
          </svg>
        ),
      },
    ],
  },
];

export function Nav() {
  const pathname = usePathname();
  return (
    <nav className="nav">
      {GROUPS.map((group) => (
        <div key={group.label} className="nav-group">
          <p className="nav-label">{group.label}</p>
          {group.items.map((item) => {
            const active = pathname === item.href;
            return (
              <Link
                key={item.href}
                href={item.href}
                className={`nav-item${active ? " nav-item-active" : ""}`}
                aria-current={active ? "page" : undefined}
                title={item.label}
              >
                <span className="nav-icon">{item.icon}</span>
                <span className="nav-text">{item.label}</span>
                {active && <span className="nav-dot" aria-hidden="true" />}
              </Link>
            );
          })}
        </div>
      ))}
    </nav>
  );
}
