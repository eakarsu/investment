const API_BASE = process.env.API_BASE || "http://localhost:8080";

/** @type {import('next').NextConfig} */
export default {
  async rewrites() {
    return [
      { source: "/api/:path*", destination: `${API_BASE}/api/:path*` },
      { source: "/healthz", destination: `${API_BASE}/healthz` },
    ];
  },
};
