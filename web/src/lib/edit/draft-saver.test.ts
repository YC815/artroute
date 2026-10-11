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

const saver = (openDb: () => Promise<DraftDb> = async () => db) =>
  createDraftSaver({
    openDb,
    placement: () => PLACEMENT,
    now: () => clock++,
    requestPersist: persist,
    notifyChanged: notify,
    onStatus: (s) => statuses.push(s),
  });

describe("createDraftSaver", () => {
  it("第一次存建立草稿（帶圖形來源與擺放），之後同一個 id 更新", async () => {
    const save = saver();
    const id = await save(R1, null);
    expect(id).not.toBeNull();
    expect((await db.get(id!))?.route).toEqual(R1);
    expect((await db.get(id!))?.source).toEqual(PLACEMENT.source);
    expect((await db.get(id!))?.transform).toEqual(PLACEMENT.transform);
    expect(await save(R2, id)).toBe(id);
    expect((await db.list()).map((d) => d.route.routeSig)).toEqual(["sig-2"]);
    expect(statuses).toEqual(["saved", "saved"]);
  });

  it("requestPersist 只在第一次存時呼叫；每次存完都通知其他分頁", async () => {
    const save = saver();
    const id = await save(R1, null);
    await save(R2, id);
    expect(persist).toHaveBeenCalledTimes(1);
    expect(notify).toHaveBeenCalledTimes(2);
  });

  it("草稿超過上限：照存並回報 over_limit", async () => {
    for (let i = 0; i < DRAFT_LIMIT; i++) {
      const { draft } = await db.create({ ...PLACEMENT, route: R0 }, i);
      await db.markOpened(draft.id, i);
    }
    const id = await saver()(R1, null);
    expect(id).not.toBeNull();
    expect(await db.list()).toHaveLength(DRAFT_LIMIT + 1);
    expect(statuses).toEqual(["over_limit"]);
  });

  it("update 時草稿已經被刪了（另一個分頁）：重新建立，回新的 id", async () => {
    const save = saver();
    const id = await save(R1, null);
    await db.remove(id!);
    const next = await save(R2, id);
    expect(next).not.toBeNull();
    expect(next).not.toBe(id);
    expect((await db.get(next!))?.route.routeSig).toBe("sig-2");
  });

  it("存不了（資料庫打不開）：回 null、回報 failed，不丟例外，仍通知", async () => {
    const save = saver(async () => {
      throw new Error("indexedDB 不可用");
    });
    await expect(save(R1, null)).resolves.toBeNull();
    expect(statuses).toEqual(["failed"]);
    expect(notify).toHaveBeenCalledTimes(1);
  });
});
