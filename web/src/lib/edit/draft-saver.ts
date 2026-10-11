import type { SaveDraft } from "@/hooks/use-route-edit";
import type { EditedRoute } from "@/lib/edit/edit-state";
import type { DraftDb, DraftInput } from "@/lib/storage/drafts";

/** saved：存好了；over_limit：存好了但草稿超過上限（請使用者刪幾份）；failed：存不了（草稿沒跟上畫面）。 */
export type DraftSaveStatus = "saved" | "over_limit" | "failed";

export type DraftSaverDeps = {
  openDb: () => Promise<DraftDb>;
  /** 建立草稿時要記的圖形來源與擺放（微調中圖形鎖住，所以建立當下讀就是進入微調時的）。 */
  placement: () => Pick<DraftInput, "source" | "transform">;
  now?: () => number;
  requestPersist: () => Promise<void>;
  /** 每次存完（不論結果）通知其他分頁。 */
  notifyChanged: () => void;
  onStatus: (status: DraftSaveStatus) => void;
};

/**
 * 微調的草稿存檔（給 useRouteEdit 的 saveDraft）：沒有 id 就 create，有就 update；update 找不到（在別的分頁被刪了）
 * 就重新建立，使用者剛做的修改不能消失。第一次存時請瀏覽器保留資料（不等它：Firefox 會跳詢問）。
 * 存不了時回報 failed 並回 null，不丟例外：微調本身不受影響，下一次路線變了會再存。
 */
export function createDraftSaver(deps: DraftSaverDeps): SaveDraft {
  const { openDb, placement, requestPersist, notifyChanged, onStatus, now = Date.now } = deps;
  let persistRequested = false;

  const create = async (route: EditedRoute) => {
    const db = await openDb();
    const { draft, overLimit } = await db.create({ ...placement(), route }, now());
    onStatus(overLimit ? "over_limit" : "saved");
    return draft.id;
  };

  return async (route, draftId) => {
    try {
      if (!persistRequested) {
        persistRequested = true;
        void requestPersist();
      }
      if (draftId === null) return await create(route);
      const db = await openDb();
      const updated = await db.update(draftId, route, now());
      if (updated === null) return await create(route);
      onStatus("saved");
      return draftId;
    } catch {
      onStatus("failed");
      return null;
    } finally {
      notifyChanged();
    }
  };
}
