import { IDBFactory } from "fake-indexeddb";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { createDraftSaver, type DraftSaveStatus } from "@/lib/edit/draft-saver";
import { SAMPLE_MATCH_RESPONSE } from "@/lib/engine/fixtures";
import { routeFromResponse } from "@/lib/edit/edit-bridge";
import { DRAFT_LIMIT, openDraftDb, type DraftDb, type DraftInput } from "@/lib/storage/drafts";

const R0 = routeFromResponse(SAMPLE_MATCH_RESPONSE);
const R1 = { ...R0, routeSig: "sig-1" };
const R2 = { ...R0, routeSig: "sig-2" };
const PLACEMENT: Pick<DraftInput, "source" | "transform"> = {
  source: { kind: "text", text: "I ♥ TAIPEI", font: "futural" },
  transform: { center: [121.5654, 25.033], sizeM: 2000, rotationDeg: 0 },
};

let db: DraftDb;
let statuses: DraftSaveStatus[];
let persist: ReturnType<typeof vi.fn<() => Promise<void>>>;
let notify: ReturnType<typeof vi.fn<() => void>>;
let clock: number;

beforeEach(async () => {
  db = await openDraftDb(new IDBFactory());
  statuses = [];
  persist = vi.fn(async () => {});
  notify = vi.fn();
  clock = 1000;
});

const saverOf = (openDb: () => Promise<DraftDb> = async () => db) =>
  createDraftSaver({
    openDb,
    now: () => clock++,
    requestPersist: persist,
    notifyChanged: notify,
    onStatus: (s) => statuses.push(s),
  });
const saver = (openDb?: () => Promise<DraftDb>) => saverOf(openDb).save;

describe("createDraftSaver", () => {
  it("第一次存建立草稿（帶圖形來源與擺放），之後同一個 id 更新", async () => {
    const save = saver();
    const id = await save(R1, null, PLACEMENT);
    expect(id).not.toBeNull();
    expect((await db.get(id!))?.route).toEqual(R1);
    expect((await db.get(id!))?.source).toEqual(PLACEMENT.source);
    expect((await db.get(id!))?.transform).toEqual(PLACEMENT.transform);
    expect(await save(R2, id, PLACEMENT)).toBe(id);
    expect((await db.list()).map((d) => d.route.routeSig)).toEqual(["sig-2"]);
    expect(statuses).toEqual(["saved", "saved"]);
  });

  it("requestPersist 只在第一次存時呼叫；每次存完都通知其他分頁", async () => {
    const save = saver();
    const id = await save(R1, null, PLACEMENT);
    await save(R2, id, PLACEMENT);
    expect(persist).toHaveBeenCalledTimes(1);
    expect(notify).toHaveBeenCalledTimes(2);
  });

  it("草稿超過上限：照存並回報 over_limit", async () => {
    for (let i = 0; i < DRAFT_LIMIT; i++) {
      const { draft } = await db.create({ ...PLACEMENT, route: R0 }, i);
      await db.markOpened(draft.id, i);
    }
    const id = await saver()(R1, null, PLACEMENT);
    expect(id).not.toBeNull();
    expect(await db.list()).toHaveLength(DRAFT_LIMIT + 1);
    expect(statuses).toEqual(["over_limit"]);
  });

  it("update 時草稿已經被刪了（另一個分頁）：重新建立，回新的 id", async () => {
    const save = saver();
    const id = await save(R1, null, PLACEMENT);
    await db.remove(id!);
    const next = await save(R2, id, PLACEMENT);
    expect(next).not.toBeNull();
    expect(next).not.toBe(id);
    expect((await db.get(next!))?.route.routeSig).toBe("sig-2");
  });

  it("存不了（資料庫打不開）：回 null、回報 failed，不丟例外，仍通知", async () => {
    const save = saver(async () => {
      throw new Error("indexedDB 不可用");
    });
    await expect(save(R1, null, PLACEMENT)).resolves.toBeNull();
    expect(statuses).toEqual(["failed"]);
    expect(notify).toHaveBeenCalledTimes(1);
  });

  it("建立時記的是呼叫端給的 placement（不是別的地方「現在」的）", async () => {
    const other: DraftInput["source"] = { kind: "text", text: "B", font: "futural" };
    const id = await saver()(R1, null, { ...PLACEMENT, source: other });
    expect((await db.get(id!))?.source).toEqual(other);
  });

  it("被別的分頁刪除：update 找不到就重建（修改不消失）", async () => {
    const s = saverOf();
    const id = await s.save(R1, null, PLACEMENT);
    await db.remove(id!);
    const next = await s.save(R2, id, PLACEMENT);
    expect(next).not.toBeNull();
    expect((await db.list()).map((d) => d.route.routeSig)).toEqual(["sig-2"]);
  });

  it("被使用者刪除（discard）：之後的存檔不做，不重建、不復活", async () => {
    const s = saverOf();
    const id = await s.save(R1, null, PLACEMENT);
    s.discard(id!);
    await db.remove(id!);
    expect(await s.save(R2, id, PLACEMENT)).toBeNull();
    expect(await db.list()).toEqual([]);
  });

  it("使用者刪除時 update 已經排在等資料庫打開：醒來後不寫（草稿維持刪除前的內容）", async () => {
    const s = saverOf();
    const id = await s.save(R1, null, PLACEMENT);
    let release!: () => void;
    const gate = new Promise<void>((resolve) => (release = resolve));
    const slow = createDraftSaver({
      openDb: async () => {
        await gate;
        return db;
      },
      requestPersist: persist,
      notifyChanged: notify,
      onStatus: (x) => statuses.push(x),
    });
    const pending = slow.save(R2, id, PLACEMENT);
    slow.discard(id!);
    release();
    expect(await pending).toBeNull();
    expect((await db.get(id!))?.route.routeSig).toBe("sig-1");
  });

  it("使用者在 update 進行中刪除，update 找不到草稿：不重建", async () => {
    const real = saverOf();
    const id = await real.save(R1, null, PLACEMENT);
    let release!: () => void;
    const gate = new Promise<void>((resolve) => (release = resolve));
    const racing: DraftDb = {
      ...db,
      update: async () => {
        await gate;
        return null; // 這時草稿已經被刪了
      },
    };
    const s = createDraftSaver({
      openDb: async () => racing,
      requestPersist: persist,
      notifyChanged: notify,
      onStatus: (x) => statuses.push(x),
    });
    const pending = s.save(R2, id, PLACEMENT);
    await new Promise((resolve) => setTimeout(resolve, 5));
    s.discard(id!);
    await db.remove(id!);
    release();
    expect(await pending).toBeNull();
    expect(await db.list()).toEqual([]);
  });

  it("刪除失敗（草稿還在）→ restore → 之後的編輯照存：資料庫是新路線、沒有「存不了」", async () => {
    const s = saverOf();
    const id = await s.save(R1, null, PLACEMENT);
    s.discard(id!); // 刪除前先讓排隊中的存檔失效；刪除失敗，草稿還在
    s.restore(id!);
    statuses.length = 0;
    expect(await s.save(R2, id, PLACEMENT)).toBe(id);
    expect((await db.get(id!))?.route.routeSig).toBe("sig-2");
    expect(statuses).toEqual(["saved"]);
  });

  it("restore 只撤銷那一份的 discard：另一份被使用者刪掉的仍然不復活", async () => {
    const s = saverOf();
    const kept = await s.save(R1, null, PLACEMENT);
    const gone = await s.save(R1, null, PLACEMENT);
    s.discard(kept!);
    s.discard(gone!);
    s.restore(kept!);
    await db.remove(gone!);
    expect(await s.save(R2, gone, PLACEMENT)).toBeNull();
    expect(await s.save(R2, kept, PLACEMENT)).toBe(kept);
    expect((await db.list()).map((d) => d.id)).toEqual([kept]);
  });
});
