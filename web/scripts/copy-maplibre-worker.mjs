// MapLibre 6 用 import.meta.url 推算 worker 位置，打包後推不到；
// 把 worker 檔複製到 public/，執行時用 setWorkerUrl("/maplibre-gl-worker.mjs") 指定。
import { copyFileSync, mkdirSync } from "node:fs";
import { createRequire } from "node:module";
import { dirname, join } from "node:path";

const require = createRequire(import.meta.url);
const dist = dirname(require.resolve("maplibre-gl/package.json"));
mkdirSync("public", { recursive: true });
copyFileSync(join(dist, "dist/maplibre-gl-worker.mjs"), "public/maplibre-gl-worker.mjs");
