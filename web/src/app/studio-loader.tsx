"use client";

import dynamic from "next/dynamic";

// 整個工作室只在瀏覽器執行：MapLibre、Web Worker、localStorage 都需要瀏覽器
const Studio = dynamic(() => import("@/components/studio/studio").then((m) => m.Studio), {
  ssr: false,
  loading: () => <p className="p-6 text-sm text-muted-foreground">載入中…</p>,
});

export function StudioLoader() {
  return <Studio />;
}
