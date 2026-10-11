import type { PostEdit, SaveDraft } from "@/hooks/use-route-edit";
import { createDraftSaver, type DraftSaveStatus } from "@/lib/edit/draft-saver";
import { toEditOutcome, toEditRequest } from "@/lib/edit/edit-bridge";
import { postEdit as postEditRequest, type EditOutcome as ClientEditOutcome } from "@/lib/engine/client";
import type { MatchRequest } from "@/lib/engine/contract";
import type { DraftDb } from "@/lib/storage/drafts";
import type { SavedSource } from "@/lib/storage/work";
import type { Transform } from "@/lib/shape/transform";
import type { EditBase } from "@/lib/studio/edit-view";

/** 一次微調：進入時的圖形來源、擺放與原圖，以及判斷圖形有沒有被移動的基準。 */
export type EditSession = { source: SavedSource; transform: Transform; strokes: MatchRequest["strokes"]; base: EditBase };

const NOT_STARTED_MESSAGE = "微調失敗，請再試一次";

/**
 * 目前這次微調要用的東西，包成 useRouteEdit 要的 postEdit 與 saveDraft：
 * /edit 帶的原圖與草稿記的來源、擺放都取自 begin 當時（微調中圖形鎖住，所以就是畫面上的）。
 * begin 之後不清掉：圖形被移動而結束微調時，還沒寫完的存檔仍要用它的來源與擺放；下一次 begin 才換掉。
 */
export function createEditSession(deps: {
  openDb: () => Promise<DraftDb>;
  requestPersist: () => Promise<void>;
  notifyChanged: () => void;
  onStatus: (status: DraftSaveStatus) => void;
  post?: (...args: Parameters<typeof postEditRequest>) => Promise<ClientEditOutcome>;
}): { begin: (session: EditSession) => void; base: () => EditBase | null; postEdit: PostEdit; saveDraft: SaveDraft } {
  const { post = postEditRequest } = deps;
  let current: EditSession | null = null;
  // 資料庫只開一次；打不開就下一次再試
  let db: Promise<DraftDb> | null = null;

  const saveDraft = createDraftSaver({
    openDb: () => {
      db ??= deps.openDb().catch((error: unknown) => {
        db = null;
        throw error;
      });
      return db;
    },
    placement: () => {
      if (!current) throw new Error("沒有進行中的微調");
      return { source: current.source, transform: current.transform };
    },
    requestPersist: deps.requestPersist,
    notifyChanged: deps.notifyChanged,
    onStatus: deps.onStatus,
  });

  const postEdit: PostEdit = async (op, route, signal) => {
    if (!current) return { ok: false, code: "unexpected", message: NOT_STARTED_MESSAGE };
    return toEditOutcome(await post(toEditRequest(route, op, current.strokes), signal));
  };

  return {
    begin: (session) => {
      current = session;
    },
    base: () => current?.base ?? null,
    postEdit,
    saveDraft,
  };
}
