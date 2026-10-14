/**
 * The browser always talks to the same origin (``/api/v1/...``); Next proxies
 * those calls to the FastAPI backend server-side. Doing it here rather than in
 * the client keeps the API free of CORS configuration and hides the backend
 * origin from the browser.
 *
 * @type {import('next').NextConfig}
 */
const apiTarget = process.env.API_PROXY_TARGET ?? "http://127.0.0.1:8000";

const nextConfig = {
  reactStrictMode: true,
  async rewrites() {
    return [
      { source: "/health", destination: `${apiTarget}/health` },
      { source: "/api/v1/:path*", destination: `${apiTarget}/api/v1/:path*` },
    ];
  },
};

export default nextConfig;
