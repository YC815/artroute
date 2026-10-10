import { defineConfig, devices } from "@playwright/test";

const PORT = 3100;
const CI = Boolean(process.env.CI);

export default defineConfig({
  testDir: "e2e",
  timeout: 60_000,
  expect: { timeout: 10_000 },
  fullyParallel: false,
  forbidOnly: CI,
  retries: CI ? 1 : 0,
  reporter: CI ? [["github"], ["html", { open: "never" }]] : [["list"]],
  use: {
    baseURL: `http://127.0.0.1:${PORT}`,
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
  },
  projects: [
    {
      name: "chromium",
      use: {
        ...devices["Desktop Chrome"],
        // 無頭模式沒有 GPU：強制用 SwiftShader 軟體 WebGL，MapLibre 才能建立地圖，結果也不受機器差異影響
        launchOptions: { args: ["--use-angle=swiftshader", "--enable-unsafe-swiftshader"] },
      },
    },
  ],
  webServer: {
    // 測的是部署用的 standalone 產物，不是 `next start`：standalone 不會自己帶 .next/static 與 public/（含 MapLibre 的 worker），
    // 部署時要複製進去，這裡照做，少複製就會在 e2e 失敗。
    // 多數測試在瀏覽器端攔截 /api/status、/api/match 與地圖 style，不需要引擎；
    // 「引擎離線」測試不攔截，走真的代理。ENGINE_URL 明確設成空字串：開發者的 shell 若已 export，也不會連到真的引擎。
    command: [
      "pnpm build",
      "mkdir -p .next/standalone/.next/static .next/standalone/public",
      "cp -R .next/static/. .next/standalone/.next/static/",
      "cp -R public/. .next/standalone/public/",
      "node .next/standalone/server.js",
    ].join(" && "),
    // server.js 讀 HOSTNAME；Docker／CI 常把它設成容器 id，必須明確指定
    env: { PORT: String(PORT), HOSTNAME: "127.0.0.1", ENGINE_URL: "" },
    url: `http://127.0.0.1:${PORT}`,
    timeout: 300_000,
    reuseExistingServer: false,
    stdout: CI ? "pipe" : "ignore",
  },
});
