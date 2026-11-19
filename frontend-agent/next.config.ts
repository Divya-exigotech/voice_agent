import type { NextConfig } from "next";

// Server-side only (no NEXT_PUBLIC_ prefix): the browser talks to same-origin
// /api/*, and Next.js proxies those requests to the FastAPI backend.
const backendUrl = process.env.BACKEND_URL ?? "http://localhost:8000";

const nextConfig: NextConfig = {
  cacheComponents: true,
  partialPrefetching: true,
  // Done here rather than in app/page.tsx: a redirect() thrown while rendering
  // breaks Cache Components prerender validation for the "/" route.
  async redirects() {
    return [{ source: "/", destination: "/dashboard", permanent: false }];
  },
  async rewrites() {
    return [{ source: "/api/:path*", destination: `${backendUrl}/api/:path*` }];
  },
};

export default nextConfig;
