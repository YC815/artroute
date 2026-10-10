import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";

type Props = { down: boolean; onRetry: () => void };

/** 引擎無法服務或正在換資料時的橫幅（spec §5）：吸附已停用，可以手動重試。 */
export function EngineBanner({ down, onRetry }: Props) {
  if (!down) return null;
  return (
    <Alert variant="destructive">
      <AlertTitle>路線引擎暫時無法使用</AlertTitle>
      <AlertDescription>
        <p>吸附已停用，可能正在更新路網資料。圖形仍然可以編輯。</p>
        <Button size="sm" variant="outline" className="mt-2" onClick={onRetry}>
          重試
        </Button>
      </AlertDescription>
    </Alert>
  );
}
