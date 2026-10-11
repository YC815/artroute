import type { PostEdit, SaveDraft } from "@/hooks/use-route-edit";
import { createDraftSaver, type DraftSaveStatus } from "@/lib/edit/draft-saver";
import { toEditOutcome, toEditRequest } from "@/lib/edit/edit-bridge";
import { postEdit as postEditRequest, type EditOutcome as ClientEditOutcome } from "@/lib/engine/client";
import type { MatchRequest } from "@/lib/engine/contract";
import { buildMatchRequest } from "@/lib/match/request";
import type { Draft, DraftDb, DraftPlacement } from "@/lib/storage/drafts";
import type { SavedSource } from "@/lib/storage/work";
import type { Transform } from "@/lib/shape/transform";
import type { EditBase } from "@/lib/studio/edit-view";
import { shapeKey, shapeOfSource } from "@/lib/studio/state";

/** 一次微調：進入時的圖形來源、擺放與原圖，以及判斷圖形有沒有被移動的基準。 */
export type EditSession = { source: SavedSource; transform: Transform; strokes: MatchRequest["strokes"]; base: EditBase };

const NOT_STARTED_MESSAGE = "微調失敗，請再試一次";

/**
 * 目前這次微調要用的東西，包成 useRouteEdit 要的 postEdit 與 saveDraft：
 * /edit 帶的原圖取自 begin 當時（postEdit 在操作送出的當下同步讀，那時一定是這次微調的）。
 * 草稿記的來源與擺放不在這裡讀：排隊中的存檔會晚於下一次 begin（使用者打開了別的草稿），
 * 所以由 controller 在 enter 時綁定（placementOf(session)），隨每次存檔帶進來。
 */
export function createEditSession(deps: {
  openDb: () => Promise<DraftDb>;
  requestPersist: () => Promise<void>;
  notifyChanged: () => void;
  onStatus: (status: DraftSaveStatus) => void;
  post?: (...args: Parameters<typeof postEditRequest>) => Promise<ClientEditOutcome>;
}): {
  begin: (session: EditSession) => void;
  base: () => EditBase | null;
  /** 這次微調使用者放的原圖（/edit、/verify 要帶）；還沒 begin 是 null。 */
  strokes: () => MatchRequest["strokes"] | null;
  postEdit: PostEdit;
  saveDraft: SaveDraft;
  /** 使用者刪掉了這份草稿：排隊中與之後的存檔都不做（見 DraftSaver.discard）。 */
  discardDraft: (id: string) => void;
} {
  const { post = postEditRequest } = deps;
  let current: EditSession | null = null;
  // 資料庫只開一次；打不開就下一次再試
  let db: Promise<DraftDb> | null = null;

  const saver = createDraftSaver({
    openDb: () => {
      db ??= deps.openDb().catch((error: unknown) => {
        db = null;
        throw error;
      });
      return db;
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
    strokes: () => current?.strokes ?? null,
    postEdit,
    saveDraft: saver.save,
    discardDraft: saver.discard,
  };
}

/** 這次微調要記進草稿的圖形來源與擺放（給 enter）。 */
export function placementOf(session: Pick<EditSession, "source" | "transform">): DraftPlacement {
  return { source: session.source, transform: session.transform };
}

/**
 * 打開草稿時的微調基準：圖形來源與擺放取自草稿，原圖用草稿的來源與擺放重新算，base 是它們的 shapeKey 與擺放。
 * Studio 要先 begin 這個再 enter：base 若還是前一次的，「圖形被移動」的判斷會馬上把剛打開的微調結束。
 * 草稿的來源不能成圖形（讀取時已驗證，只剩版本不符之類的意外）時是 null。
 */
export function sessionFromDraft(draft: Pick<Draft, "source" | "transform">): EditSession | null {
  const shape = shapeOfSource(draft.source);
  if (!shape) return null;
  return {
    source: draft.source,
    transform: draft.transform,
    strokes: buildMatchRequest(shape, draft.transform).strokes,
    base: { shapeKey: shapeKey(shape), transform: draft.transform },
  };
}
