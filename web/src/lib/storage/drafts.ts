import { z } from "zod";
import { matchResponseSchema, segmentKindSchema } from "@/lib/engine/contract";
import type { Transform } from "@/lib/shape/transform";
import { savedSourceSchema, savedTransformSchema, type SavedSource } from "@/lib/storage/work";

/** 草稿上限；全部都打開過時照樣存，由 create 回 overLimit 提示使用者刪幾份。 */
export const DRAFT_LIMIT = 20;
/** 沒打開過的草稿，updatedAt 超過這麼久就自動刪除。 */
export const UNOPENED_TTL_MS = 7 * 24 * 3600 * 1000;
export const DRAFTS_CHANNEL = "artroute:drafts";

const DB_NAME = "artroute";
const DB_VERSION = 1;
const STORE_NAME = "drafts";

// 引擎回應的 segments 與 score；kind 多了微調產生的 "edit"（只有 /edit、/verify 會回）。D3 合併契約後改成引用 contract.ts。
const segmentSchema = matchResponseSchema.shape.segments.element.extend({
  kind: z.enum([...segmentKindSchema.options, "edit"]),
});
const scoreSchema = matchResponseSchema.shape.score;

export type Segment = z.infer<typeof segmentSchema>;
export type Score = z.infer<typeof scoreSchema>;

export type DraftRoute = { segments: Segment[]; dataVersion: string; routeSig: string; score: Score; lengthM: number };

/** openedAt 為 null：從沒從「我的路線」打開過（建立它的那次微調不算）。 */
export type Draft = {
  v: 1;
  id: string;
  createdAt: number;
  updatedAt: number;
  openedAt: number | null;
  source: SavedSource;
  transform: Transform;
  route: DraftRoute;
};

export type DraftInput = Pick<Draft, "source" | "transform" | "route">;

const draftSchema = z.object({
  v: z.literal(1),
  id: z.string(),
  createdAt: z.number(),
  updatedAt: z.number(),
  openedAt: z.number().nullable(),
  source: savedSourceSchema,
  transform: savedTransformSchema,
  route: z.object({
    segments: z.array(segmentSchema),
    dataVersion: z.string(),
    routeSig: z.string(),
    score: scoreSchema,
    lengthM: z.number(),
  }),
}) satisfies z.ZodType<Draft>;

export type DraftDb = {
  /** 驗證不過的紀錄跳過但不刪（避免版本升級時誤刪）；依 updatedAt 新到舊。 */
  list(): Promise<Draft[]>;
  get(id: string): Promise<Draft | null>;
  /** 原樣存，不改時間。 */
  put(draft: Draft): Promise<void>;
  remove(id: string): Promise<void>;
  /** overLimit：存完之後仍超過 DRAFT_LIMIT 份（沒有未打開的草稿可刪）。 */
  create(input: DraftInput, now: number): Promise<{ draft: Draft; overLimit: boolean }>;
  update(id: string, route: DraftRoute, now: number): Promise<Draft | null>;
  markOpened(id: string, now: number): Promise<Draft | null>;
  /** 刪掉過期的未打開草稿，回傳刪了幾份。 */
  sweep(now: number): Promise<number>;
};

function request<T>(req: IDBRequest<T>): Promise<T> {
  return new Promise((resolve, reject) => {
    req.onsuccess = () => resolve(req.result);
    req.onerror = () => reject(req.error);
  });
}

function parseDraft(record: unknown): Draft | null {
  const parsed = draftSchema.safeParse(record);
  return parsed.success ? parsed.data : null;
}

function parseDrafts(records: unknown[]): Draft[] {
  return records.map(parseDraft).filter((draft): draft is Draft => draft !== null);
}

/** fn 裡的讀寫在同一個交易：交易提交後才 resolve；任何一步失敗整個交易都不生效。 */
async function inTransaction<T>(
  db: IDBDatabase,
  mode: IDBTransactionMode,
  fn: (store: IDBObjectStore) => Promise<T>,
): Promise<T> {
  const tx = db.transaction(STORE_NAME, mode);
  const committed = new Promise<void>((resolve, reject) => {
    tx.oncomplete = () => resolve();
    tx.onabort = () => reject(tx.error ?? new DOMException("草稿的交易被中止", "AbortError"));
  });
  const work = fn(tx.objectStore(STORE_NAME)).catch((error: unknown) => {
    // 請求失敗時交易已經自己中止；其他錯誤（例如資料無法複製）要手動中止，否則前面的刪除會被提交。
    try {
      tx.abort();
    } catch {
      // 交易已經結束
    }
    throw error;
  });
  const [result] = await Promise.all([work, committed]);
  return result;
}

export async function openDraftDb(factory: IDBFactory = globalThis.indexedDB): Promise<DraftDb> {
  const open = factory.open(DB_NAME, DB_VERSION);
  open.onupgradeneeded = () => {
    open.result.createObjectStore(STORE_NAME, { keyPath: "id" });
  };
  const db = await request(open);
  // 之後升級資料庫版本時，其他分頁開著的舊連線會擋住升級。
  db.onversionchange = () => db.close();

  const modify = (id: string, change: (draft: Draft) => Draft) =>
    inTransaction(db, "readwrite", async (store) => {
      const draft = parseDraft(await request(store.get(id)));
      if (!draft) return null;
      const next = change(draft);
      store.put(next);
      return next;
    });

  return {
    list: () =>
      inTransaction(db, "readonly", async (store) =>
        parseDrafts(await request(store.getAll())).sort((a, b) => b.updatedAt - a.updatedAt),
      ),

    get: (id) => inTransaction(db, "readonly", async (store) => parseDraft(await request(store.get(id)))),

    put: (draft) =>
      inTransaction(db, "readwrite", async (store) => {
        await request(store.put(draft));
      }),

    remove: (id) =>
      inTransaction(db, "readwrite", async (store) => {
        await request(store.delete(id));
      }),

    // 壞掉的紀錄不算進上限：使用者在「我的路線」看不到，也就刪不掉它。
    create: (input, now) =>
      inTransaction(db, "readwrite", async (store) => {
        const drafts = parseDrafts(await request(store.getAll()));
        let count = drafts.length;
        if (count >= DRAFT_LIMIT) {
          const oldest = drafts
            .filter((d) => d.openedAt === null)
            .reduce<Draft | null>((a, d) => (a === null || d.updatedAt < a.updatedAt ? d : a), null);
          if (oldest) {
            store.delete(oldest.id);
            count -= 1;
          }
        }
        const draft: Draft = { v: 1, id: crypto.randomUUID(), createdAt: now, updatedAt: now, openedAt: null, ...input };
        store.put(draft);
        return { draft, overLimit: count + 1 > DRAFT_LIMIT };
      }),

    update: (id, route, now) => modify(id, (draft) => ({ ...draft, route, updatedAt: now })),

    markOpened: (id, now) => modify(id, (draft) => ({ ...draft, openedAt: now })),

    sweep: (now) =>
      inTransaction(db, "readwrite", async (store) => {
        const expired = parseDrafts(await request(store.getAll())).filter(
          (d) => d.openedAt === null && now - d.updatedAt > UNOPENED_TTL_MS,
        );
        for (const d of expired) store.delete(d.id);
        return expired.length;
      }),
  };
}

/** 請瀏覽器在空間不足時別清掉草稿。瀏覽器可能拒絕或沒有這個 API，都不影響使用。 */
export async function requestPersist(): Promise<void> {
  try {
    await globalThis.navigator?.storage?.persist?.();
  } catch {
    // 不影響使用
  }
}

/** 通知其他分頁草稿變了。 */
export function notifyDraftsChanged(): void {
  if (typeof BroadcastChannel === "undefined") return;
  const channel = new BroadcastChannel(DRAFTS_CHANNEL);
  channel.postMessage(null);
  channel.close();
}

/** 其他分頁的草稿變了就呼叫 cb；回傳取消訂閱。 */
export function onDraftsChanged(cb: () => void): () => void {
  if (typeof BroadcastChannel === "undefined") return () => {};
  const channel = new BroadcastChannel(DRAFTS_CHANNEL);
  channel.onmessage = () => cb();
  return () => channel.close();
}
