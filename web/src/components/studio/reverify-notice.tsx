"use client";

import { LoaderCircle } from "lucide-react";
import { Button } from "@/components/ui/button";
import type { ReverifyState } from "@/lib/edit/reverify";

type Props = {
  /** 「這是 X 月 X 日路網上的路線」。 */
  label: string;
  state: ReverifyState;
  onReverify: () => void;
  onBackToAuto: () => void;
};

/**
 * 舊路網的路線（草稿是在舊版路網上微調的）：照常顯示、可以下載 GPX，要編輯先「重新驗證」。
 * 沒過時有問題的段在地圖上是紅色，可以「回到自動吸附」（擺放不變、重新吸附；舊草稿保留）。
 */
export function ReverifyNotice({ label, state, onReverify, onBackToAuto }: Props) {
  const verifying = state.status === "verifying";
  return (
    <div className="pointer-events-auto flex max-w-full flex-col gap-2 rounded-lg bg-background/95 p-2 shadow-md ring-1 ring-foreground/10">
      <p className="text-sm font-medium">{label}</p>
      <p className="text-xs text-muted-foreground">
        仍可下載 GPX。要編輯，請先到目前的路網上重新驗證；沒通過的段會標成紅色。
      </p>
      <div className="flex flex-wrap items-center gap-2">
        <Button size="sm" disabled={verifying} onClick={onReverify}>
          {verifying && <LoaderCircle className="animate-spin" aria-hidden="true" />}
          {verifying ? "驗證中…" : state.message ? "再驗證一次" : "重新驗證"}
        </Button>
        <Button size="sm" variant="outline" onClick={onBackToAuto}>
          回到自動吸附
        </Button>
      </div>
      <div role="alert" className="flex flex-col gap-1 text-xs text-destructive empty:hidden">
        {state.message && <p>{state.message}</p>}
        {state.bad.length > 0 && <p>有 {state.bad.length} 段在新路網上不能騎（地圖上的紅色）。</p>}
      </div>
    </div>
  );
}
