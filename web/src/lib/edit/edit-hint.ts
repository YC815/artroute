import type { EditState } from "@/lib/edit/edit-state";
import { DRAFT_LIMIT } from "@/lib/storage/drafts";

/** 工具列下面的一行操作說明：現在該做什麼。 */
export function editHint(state: EditState): string {
  if (!state.active) return "";
  if (state.pending) return "引擎處理中…";
  switch (state.tool) {
    case null:
      return "選一個操作：剪掉、重畫或拖曳";
    case "cut":
      return "點路線上要剪掉的圈或繞路";
    case "reroute":
      if (state.picks.length === 0) return "點路線上重畫的第一個點";
      if (state.picks.length === 1) return "再點第二個點";
      return "選「最短」或「貼圖形」";
    case "drag":
      return "抓住路線上任一點，拖到新位置再放開";
  }
}

/** 草稿存檔的結果裡，要讓使用者知道的兩種（saved 不顯示）。 */
export type DraftNotice = "over_limit" | "failed";

export function draftNoticeMessage(notice: DraftNotice): string {
  return notice === "over_limit"
    ? `草稿已超過 ${DRAFT_LIMIT} 份（都是打開過的，不會自動刪除）：請到「我的路線」刪掉幾份`
    : "這次修改沒有存成草稿（瀏覽器不允許儲存）：離開頁面會消失，請先下載 GPX";
}
