import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  // hl 部署用 .next/standalone/server.js（讀環境變數 PORT）
  output: "standalone",
  cacheComponents: true,
  partialPrefetching: true,
  turbopack: {
    rules: {
      "*.css": {
        loaders: ["@tailwindcss/turbopack"],
        as: "*.css",
      },
    },
  },
};

export default nextConfig;
