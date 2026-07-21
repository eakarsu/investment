import path from "node:path";
import { fileURLToPath } from "node:url";

const API_BASE = process.env.API_BASE || "http://localhost:8080";
const projectRoot = path.dirname(fileURLToPath(import.meta.url));

/** @type {import('next').NextConfig} */
export default {
  outputFileTracingRoot: projectRoot,
  async rewrites() {
    return [
      { source: "/api/:path*", destination: `${API_BASE}/api/:path*` },
      { source: "/healthz", destination: `${API_BASE}/healthz` },
    ];
  },
};
