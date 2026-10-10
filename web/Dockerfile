# hl 範本：Next.js。需要 next.config 設 output: "standalone"
FROM node:24-alpine AS base
RUN corepack enable
WORKDIR /app

FROM base AS deps
COPY package.json pnpm-lock.yaml* pnpm-workspace.yaml* ./
RUN pnpm install --frozen-lockfile

FROM base AS build
COPY --from=deps /app/node_modules ./node_modules
COPY . .
RUN mkdir -p public && pnpm build

FROM node:24-alpine AS run
WORKDIR /app
# HOSTNAME 要綁 0.0.0.0，hl 的 proxy 才連得到；PORT 只是自我說明，hl 注入的 PORT 優先
ENV NODE_ENV=production HOSTNAME=0.0.0.0 PORT=8080
# 這個容器會解析最大 8 MB 的使用者 JSON，不以 root 執行
RUN addgroup -S nodejs && adduser -S nextjs -G nodejs
# standalone 執行時可能寫入 .next 快取，所以檔案要歸 nextjs 所有
COPY --from=build --chown=nextjs:nodejs /app/.next/standalone ./
COPY --from=build --chown=nextjs:nodejs /app/.next/static ./.next/static
# public/ 要從 build stage 複製：maplibre-gl-worker.mjs 是 pnpm build 產生的，不在 git 也不在 build context
COPY --from=build --chown=nextjs:nodejs /app/public ./public
USER nextjs
# standalone 的 server.js 會讀 PORT 環境變數（hl 注入 8080）
CMD ["node", "server.js"]
