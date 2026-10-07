/** @type {import('next').NextConfig} */
const API_BASE = process.env.NEXT_PUBLIC_API_BASE ?? "http://127.0.0.1:8000";

const nextConfig = {
  reactStrictMode: true,
  async rewrites() {
    return [
      // MJPEG live stream – must bypass the JS route handler because
      // fetch() inside the App-Router proxy buffers the body and
      // never delivers the multipart/x-mixed-replace frames.
      {
        source: "/api/stream/cameras/:cameraId/stream.mjpg",
        destination: `${API_BASE}/api/monitoring/cameras/:cameraId/stream.mjpg`,
      },
    ];
  },
};

export default nextConfig;
