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
