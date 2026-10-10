"use client";

import { useRef } from "react";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import type { EngineStatus } from "@/hooks/use-engine-status";
import { cn } from "@/lib/utils";

type Status = EngineStatus["status"];
type Props = { status: Status; onRetry: () => void };

/** 給螢幕閱讀器的狀態文字；down 的內容由 Alert（role=alert）自己宣告，這裡留空避免念兩次。 */
const STATUS_TEXT: Record<Status, string> = {
  checking: "檢查引擎中…",
  ok: "路線引擎已連線",
  down: "",
};

/**
 * 引擎狀態（spec §5）：檢查中顯示「檢查引擎中…」；無法服務或正在換資料時顯示橫幅，吸附已停用，可以手動重試；
 * 正常時只留給螢幕閱讀器的文字（sr-only，不占版面）。
 * 按「重試」後按鈕會消失，焦點移到這個區塊，不會掉回 body。
 */
export function EngineBanner({ status, onRetry }: Props) {
  const regionRef = useRef<HTMLDivElement>(null);
  const retry = () => {
    onRetry();
    regionRef.current?.focus();
  };
  return (
    <div ref={regionRef} tabIndex={-1} className={cn("outline-none", status === "ok" && "sr-only")}>
      <p role="status" className={status === "checking" ? "text-sm text-muted-foreground" : "sr-only"}>
        {STATUS_TEXT[status]}
      </p>
      {status === "down" && (
        <Alert variant="destructive">
          <AlertTitle>路線引擎暫時無法使用</AlertTitle>
          <AlertDescription>
            <p>吸附已停用，可能正在更新路網資料。圖形仍然可以編輯。</p>
            <Button size="sm" variant="outline" className="mt-2" onClick={retry}>
              重試
            </Button>
          </AlertDescription>
        </Alert>
      )}
    </div>
  );
}
