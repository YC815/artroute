"use client";

import { useId } from "react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import type { MatchError } from "@/lib/engine/client";
import type { MatchResponse, MatchWarning } from "@/lib/engine/contract";
import { buildGpx, gpxFileName } from "@/lib/export/gpx";
import { formatDataVersion, formatDistance } from "@/lib/format";
import type { LonLat } from "@/lib/shape/types";
import { canRetry } from "@/lib/studio/state";

type Props = {
  targetLengthM: number | null;
  /** 目前有效的吸附結果；圖形不能吸附、或結果屬於上一個圖形時為 null，不顯示舊路線。 */
  response: MatchResponse | null;
  error: MatchError | null;
  pending: boolean;
  /** 路線是目前這個擺放的、可以下載 GPX（見 canDownloadGpx）。 */
  downloadable: boolean;
  /** 要標紅的部件（來自 response 或 422 的 warnings）。 */
  warnings: readonly MatchWarning[];
  /** 重送目前的請求（錯誤旁的「重新吸附」）。 */
  onRetry: () => void;
  /** 圖形不能吸附的原因（例如筆畫太多）。 */
  problem: string | null;
  dataVersion: string | null;
  /** GPX 的作品名稱（見 gpxName）。 */
  workName: string;
  /** 有搜尋範圍且有路線時：路線起點離圓心多遠、是否在圈外（見 startDistance）；否則 null，不顯示這一列。 */
  startDistance: { distanceM: number; outside: boolean } | null;
};

/** object URL 保留多久才釋放：iOS Safari 與較慢的下載處理可能在 click 之後一段時間才讀檔。 */
const REVOKE_AFTER_MS = 60_000;

/** 下載 GPX。不加 BOM：有些匯入器會把 BOM 當成 XML 宣告前的非法字元。 */
function downloadGpx(points: LonLat[], name: string) {
  const blob = new Blob([buildGpx(points, name)], { type: "application/gpx+xml" });
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = gpxFileName(name);
  // 有些瀏覽器（舊版 Firefox 等）不在文件裡的 <a> 點了不會下載
  link.hidden = true;
  document.body.append(link);
  link.click();
  link.remove();
  setTimeout(() => URL.revokeObjectURL(url), REVOKE_AFTER_MS);
}

function Row({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex justify-between gap-4 text-sm">
      <span className="text-muted-foreground">{label}</span>
      <span className="font-medium tabular-nums">{value}</span>
    </div>
  );
}

const LEGEND = [
  { label: "目標圖形", className: "border-t-2 border-dashed border-violet-600" },
  { label: "路線", className: "border-t-4 border-blue-600" },
  { label: "繞路（禁行或單行道）", className: "border-t-[6px] border-orange-500" },
  { label: "連接線", className: "border-t-[3px] border-dotted border-slate-400" },
  { label: "吸附失敗的筆畫", className: "border-t-[3px] border-dashed border-red-600" },
];

export function InfoPanel(props: Props) {
  const { targetLengthM, response, error, pending, downloadable, onRetry, warnings, problem, dataVersion } = props;
  const { workName, startDistance } = props;
  const score = response?.score;
  const headingId = useId();
  return (
    <section className="flex flex-col gap-3" aria-labelledby={headingId}>
      <div className="flex items-center justify-between">
        <h2 id={headingId} className="text-sm font-semibold">
          路線資訊
        </h2>
        <div role="status">{pending && <Badge variant="secondary">吸附中…</Badge>}</div>
      </div>
      <div className="flex flex-col gap-1">
        <Row label="目標長度" value={targetLengthM === null ? "—" : formatDistance(targetLengthM)} />
        <Row label="路線長度" value={response ? formatDistance(response.route.length_m) : "—"} />
        <Row label="覆蓋" value={score ? `${score.coverage}%` : "—"} />
        <Row label="乾淨" value={score ? `${score.cleanliness}%` : "—"} />
        <Row label="整體貼合度" value={score ? `${score.overall}%` : "—"} />
        {startDistance && (
          <Row
            label="起點離圓心"
            value={`${formatDistance(startDistance.distanceM)}${startDistance.outside ? "（圈外）" : ""}`}
          />
        )}
        <Row label="路網資料" value={formatDataVersion(response?.data_version ?? dataVersion ?? "—")} />
      </div>

      {/* live region 一直留在 DOM 裡（不用 hidden）：內容出現時才念得出來 */}
      <div className="flex flex-col gap-1 text-sm text-destructive">
        <div role="alert" className="flex flex-col gap-1">
          {problem && <p>{problem}</p>}
          {error && <p>{error.message}</p>}
        </div>
        {canRetry(error) && (
          <Button size="sm" variant="outline" className="self-start" disabled={pending} onClick={onRetry}>
            重新吸附
          </Button>
        )}
        <div aria-live="polite">
          {warnings.length > 0 && (
            <ul className="flex flex-col gap-1">
              {warnings.map((w) => (
                <li key={`${w.stroke_index}-${w.code}`}>
                  第 {w.stroke_index + 1} 筆：{w.message}
                </li>
              ))}
            </ul>
          )}
        </div>
      </div>

      <div className="flex flex-col gap-1.5">
        <Button
          disabled={!downloadable}
          onClick={() => response && downloadable && downloadGpx(response.route.points, workName)}
        >
          下載 GPX
        </Button>
        <p className="text-xs text-muted-foreground">
          在 Strava 用「我的路線 → 建立路線 → 匯入」匯入（不是上傳活動；檔案沒有時間戳）
        </p>
      </div>

      <ul className="grid grid-cols-2 gap-x-3 gap-y-1 text-xs text-muted-foreground">
        {LEGEND.map((item) => (
          <li key={item.label} className="flex items-center gap-2">
            <span className={`inline-block w-6 ${item.className}`} />
            {item.label}
          </li>
        ))}
        <li className="flex items-center gap-2">
          <span className="inline-block size-2.5 rounded-full bg-green-600" />
          起點
        </li>
        <li className="flex items-center gap-2">
          <span className="inline-block size-2.5 rounded-full bg-red-600" />
          終點
        </li>
      </ul>
    </section>
  );
}
