import { IDBFactory } from "fake-indexeddb";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  DRAFT_LIMIT,
  DRAFTS_CHANNEL,
  notifyDraftsChanged,
  onDraftsChanged,
  openDraftDb,
  requestPersist,
  UNOPENED_TTL_MS,
  type DraftDb,
  type DraftInput,
  type DraftRoute,
} from "@/lib/storage/drafts";

const DAY_MS = 24 * 3600 * 1000;
const T0 = Date.UTC(2026, 9, 11);

const ROUTE: DraftRoute = {
  segments: [
    {
      kind: "stroke",
      stroke_index: 0,
      points: [
        [121.5, 25.03],
        [121.501, 25.031],
      ],
      length_m: 140,
    },
  ],
  dataVersion: "build-1",
  routeSig: "sig-1",
  score: { coverage: 90, cleanliness: 80, direction: 100, overall: 88 },
  lengthM: 140,
};

const EDITED_ROUTE: DraftRoute = {
  ...ROUTE,
  segments: [{ ...ROUTE.segments[0], kind: "edit", stroke_index: null }],
  routeSig: "sig-2",
};

const INPUT: DraftInput = {
  source: { kind: "text", text: "HI", font: "futural" },
  transform: { center: [121.5654, 25.033], sizeM: 2000, rotationDeg: 15 },
  route: ROUTE,
};

/** 不經過 DraftDb，直接操作 artroute／drafts store（確認資料真的在不在、塞壞掉的紀錄）。 */
function raw<T>(factory: IDBFactory, mode: IDBTransactionMode, fn: (store: IDBObjectStore) => IDBRequest<T>): Promise<T> {
  return new Promise((resolve, reject) => {
    const open = factory.open("artroute", 1);
    open.onerror = () => reject(open.error);
    open.onsuccess = () => {
      const request = fn(open.result.transaction("drafts", mode).objectStore("drafts"));
      request.onerror = () => reject(request.error);
      request.onsuccess = () => {
        open.result.close();
        resolve(request.result);
      };
    };
  });
}

let factory: IDBFactory;
let db: DraftDb;

beforeEach(async () => {
  factory = new IDBFactory();
  db = await openDraftDb(factory);
});

describe("建立、更新、打開", () => {
  it("新草稿：時間都是 now、沒打開過、拿得回來", async () => {
    const { draft, overLimit } = await db.create(INPUT, T0);
    expect(overLimit).toBe(false);
    expect(draft).toEqual({ v: 1, id: draft.id, createdAt: T0, updatedAt: T0, openedAt: null, ...INPUT });
    expect(draft.id).toMatch(/^[0-9a-f-]{36}$/);
    expect(await db.get(draft.id)).toEqual(draft);
    expect(await db.list()).toEqual([draft]);
  });

  it("每份草稿的 id 不同", async () => {
    const a = await db.create(INPUT, T0);
    const b = await db.create(INPUT, T0);
    expect(a.draft.id).not.toBe(b.draft.id);
  });

  it("存在 artroute 資料庫的 drafts store，以 id 為 key，重新打開還在", async () => {
    const { draft } = await db.create(INPUT, T0);
    expect(await raw(factory, "readonly", (store) => store.get(draft.id))).toEqual(draft);
    const reopened = await openDraftDb(factory);
    expect(await reopened.get(draft.id)).toEqual(draft);
  });

  it("update 換掉路線、更新 updatedAt，其他不變", async () => {
    const { draft } = await db.create(INPUT, T0);
    const updated = await db.update(draft.id, EDITED_ROUTE, T0 + 5000);
    expect(updated).toEqual({ ...draft, route: EDITED_ROUTE, updatedAt: T0 + 5000 });
    expect(await db.get(draft.id)).toEqual(updated);
  });

  it("markOpened 只記下打開的時間", async () => {
    const { draft } = await db.create(INPUT, T0);
    const opened = await db.markOpened(draft.id, T0 + 9000);
    expect(opened).toEqual({ ...draft, openedAt: T0 + 9000 });
    expect(await db.get(draft.id)).toEqual(opened);
  });

  it("不存在的 id：get、update、markOpened 都回傳 null，也不會新增紀錄", async () => {
    expect(await db.get("nope")).toBeNull();
    expect(await db.update("nope", EDITED_ROUTE, T0)).toBeNull();
    expect(await db.markOpened("nope", T0)).toBeNull();
    expect(await db.list()).toEqual([]);
  });

  it("put 原樣存（不改時間），remove 刪掉", async () => {
    const { draft } = await db.create(INPUT, T0);
    const changed = { ...draft, route: EDITED_ROUTE };
    await db.put(changed);
    expect(await db.get(draft.id)).toEqual(changed);
    await db.remove(draft.id);
    expect(await db.get(draft.id)).toBeNull();
    expect(await db.list()).toEqual([]);
  });

  it("list 依 updatedAt 新到舊", async () => {
    const a = (await db.create(INPUT, T0)).draft;
    const b = (await db.create(INPUT, T0 + 1000)).draft;
    const c = (await db.create(INPUT, T0 + 2000)).draft;
    await db.update(a.id, EDITED_ROUTE, T0 + 3000);
    expect((await db.list()).map((d) => d.id)).toEqual([a.id, c.id, b.id]);
  });
});

describe("7 天沒打開就刪除", () => {
  it("沒打開過：6.9 天不刪、剛好 7 天不刪、7.1 天刪", async () => {
    const { draft } = await db.create(INPUT, T0);
    expect(await db.sweep(T0 + 6.9 * DAY_MS)).toBe(0);
    expect(await db.sweep(T0 + UNOPENED_TTL_MS)).toBe(0);
    expect(await db.get(draft.id)).not.toBeNull();
    expect(await db.sweep(T0 + 7.1 * DAY_MS)).toBe(1);
    expect(await db.get(draft.id)).toBeNull();
  });

  it("以 updatedAt 計算：期間內修改過就重新計時", async () => {
    const { draft } = await db.create(INPUT, T0);
    await db.update(draft.id, EDITED_ROUTE, T0 + 5 * DAY_MS);
    expect(await db.sweep(T0 + 10 * DAY_MS)).toBe(0);
    expect(await db.get(draft.id)).not.toBeNull();
  });

  it("打開過的 30 天也不刪", async () => {
    const { draft } = await db.create(INPUT, T0);
    await db.markOpened(draft.id, T0 + 1000);
    expect(await db.sweep(T0 + 30 * DAY_MS)).toBe(0);
    expect(await db.get(draft.id)).not.toBeNull();
  });

  it("只刪過期的未打開草稿，回傳刪了幾份", async () => {
    await db.create(INPUT, T0);
    await db.create(INPUT, T0 + 1000);
    const openedOld = (await db.create(INPUT, T0)).draft;
    await db.markOpened(openedOld.id, T0 + 2000);
    const fresh = (await db.create(INPUT, T0 + 6 * DAY_MS)).draft;
    expect(await db.sweep(T0 + 8 * DAY_MS)).toBe(2);
    expect((await db.list()).map((d) => d.id)).toEqual([fresh.id, openedOld.id]);
  });
});

describe(`上限 ${DRAFT_LIMIT} 份`, () => {
  async function fill(count: number, opened: (i: number) => boolean) {
    const ids: string[] = [];
    for (let i = 0; i < count; i++) {
      const { draft } = await db.create(INPUT, T0 + i * 1000);
      if (opened(i)) await db.markOpened(draft.id, T0 + i * 1000);
      ids.push(draft.id);
    }
    return ids;
  }

  it("已有 20 份時新建，刪掉 updatedAt 最舊的未打開草稿", async () => {
    const ids = await fill(DRAFT_LIMIT, () => false);
    const { draft, overLimit } = await db.create(INPUT, T0 + 100_000);
    expect(overLimit).toBe(false);
    const left = (await db.list()).map((d) => d.id);
    expect(left).toHaveLength(DRAFT_LIMIT);
    expect(left).not.toContain(ids[0]);
    expect(left).toContain(draft.id);
  });

  it("最舊的是打開過的就跳過它，刪最舊的未打開草稿", async () => {
    const ids = await fill(DRAFT_LIMIT, (i) => i < 3);
    await db.create(INPUT, T0 + 100_000);
    const left = (await db.list()).map((d) => d.id);
    expect(left).toHaveLength(DRAFT_LIMIT);
    expect(left).toEqual(expect.arrayContaining(ids.slice(0, 3)));
    expect(left).not.toContain(ids[3]);
  });

  it("未滿 20 份時不刪", async () => {
    await fill(DRAFT_LIMIT - 1, () => false);
    const { overLimit } = await db.create(INPUT, T0 + 100_000);
    expect(overLimit).toBe(false);
    expect(await db.list()).toHaveLength(DRAFT_LIMIT);
  });

  it("全部打開過：第 21 份照存並回 overLimit", async () => {
    await fill(DRAFT_LIMIT, () => true);
    const { draft, overLimit } = await db.create(INPUT, T0 + 100_000);
    expect(overLimit).toBe(true);
    expect(await db.get(draft.id)).toEqual(draft);
    expect(await db.list()).toHaveLength(DRAFT_LIMIT + 1);
  });

  it("仍超過上限時，就算刪了一份未打開的，也繼續回 overLimit", async () => {
    await fill(DRAFT_LIMIT, () => true);
    const first = await db.create(INPUT, T0 + 100_000);
    const second = await db.create(INPUT, T0 + 200_000);
    expect(second.overLimit).toBe(true);
    const left = (await db.list()).map((d) => d.id);
    expect(left).toHaveLength(DRAFT_LIMIT + 1);
    expect(left).not.toContain(first.draft.id);
    expect(left).toContain(second.draft.id);
  });

  it("存檔失敗時不會先刪掉舊草稿（同一個交易）", async () => {
    const ids = await fill(DRAFT_LIMIT, () => false);
    const uncloneable = { ...INPUT, route: { ...ROUTE, extra: () => 0 } } as unknown as DraftInput;
    await expect(db.create(uncloneable, T0 + 100_000)).rejects.toThrow();
    expect((await db.list()).map((d) => d.id).sort()).toEqual([...ids].sort());
  });
});

describe("壞掉的紀錄", () => {
  const BROKEN = { v: 1, id: "broken", createdAt: T0, updatedAt: T0, openedAt: null, source: { kind: "text" } };

  beforeEach(async () => {
    await raw(factory, "readwrite", (store) => store.put(BROKEN));
  });

  it("list 跳過、get 回傳 null，但仍留在 store", async () => {
    const { draft } = await db.create(INPUT, T0);
    expect(await db.list()).toEqual([draft]);
    expect(await db.get("broken")).toBeNull();
    expect(await raw(factory, "readonly", (store) => store.get("broken"))).toEqual(BROKEN);
  });

  it("版本號不是 1 的也跳過", async () => {
    const { draft } = await db.create(INPUT, T0);
    await raw(factory, "readwrite", (store) => store.put({ ...draft, id: "v2", v: 2 }));
    expect((await db.list()).map((d) => d.id)).toEqual([draft.id]);
  });

  it("sweep、update、markOpened 都不會動到它", async () => {
    expect(await db.sweep(T0 + 30 * DAY_MS)).toBe(0);
    expect(await db.update("broken", ROUTE, T0 + 1000)).toBeNull();
    expect(await db.markOpened("broken", T0 + 1000)).toBeNull();
    expect(await raw(factory, "readonly", (store) => store.get("broken"))).toEqual(BROKEN);
  });

  it("不算進 20 份上限（使用者看不到也刪不掉它）", async () => {
    for (let i = 0; i < DRAFT_LIMIT - 1; i++) await db.create(INPUT, T0 + i * 1000);
    const { overLimit } = await db.create(INPUT, T0 + 100_000);
    expect(overLimit).toBe(false);
    expect(await db.list()).toHaveLength(DRAFT_LIMIT);
    expect(await raw(factory, "readonly", (store) => store.get("broken"))).toEqual(BROKEN);
  });
});

describe("requestPersist", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("沒有 navigator.storage 時不丟錯", async () => {
    vi.stubGlobal("navigator", {});
    await expect(requestPersist()).resolves.toBeUndefined();
    vi.stubGlobal("navigator", undefined);
    await expect(requestPersist()).resolves.toBeUndefined();
  });

  it("有 API 就呼叫；被拒絕或丟錯都吞掉", async () => {
    const persist = vi.fn().mockResolvedValue(false);
    vi.stubGlobal("navigator", { storage: { persist } });
    await requestPersist();
    expect(persist).toHaveBeenCalledOnce();

    vi.stubGlobal("navigator", { storage: { persist: vi.fn().mockRejectedValue(new Error("denied")) } });
    await expect(requestPersist()).resolves.toBeUndefined();
  });
});

describe("多分頁通知", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("其他分頁 notify 時收到通知，取消訂閱後就不再收到", async () => {
    const cb = vi.fn();
    const unsubscribe = onDraftsChanged(cb);
    notifyDraftsChanged();
    await vi.waitFor(() => expect(cb).toHaveBeenCalledOnce());

    unsubscribe();
    const probe = new BroadcastChannel(DRAFTS_CHANNEL);
    const delivered = new Promise((resolve) => (probe.onmessage = resolve));
    notifyDraftsChanged();
    await delivered;
    probe.close();
    expect(cb).toHaveBeenCalledOnce();
  });

  it("沒有 BroadcastChannel 時是 no-op", () => {
    vi.stubGlobal("BroadcastChannel", undefined);
    const unsubscribe = onDraftsChanged(vi.fn());
    expect(() => notifyDraftsChanged()).not.toThrow();
    expect(() => unsubscribe()).not.toThrow();
  });
});
