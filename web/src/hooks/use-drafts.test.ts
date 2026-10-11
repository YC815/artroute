import { IDBFactory } from "fake-indexeddb";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { createDraftsController, type DraftsView } from "@/hooks/use-drafts";
import { routeFromResponse } from "@/lib/edit/edit-bridge";
import { SAMPLE_MATCH_RESPONSE } from "@/lib/engine/fixtures";
import { DRAFT_LIMIT, openDraftDb, UNOPENED_TTL_MS, type DraftDb, type DraftInput } from "@/lib/storage/drafts";

const DAY = 24 * 3600 * 1000;
const T0 = Date.UTC(2026, 9, 11);
const INPUT: DraftInput = {
  source: { kind: "text", text: "A", font: "futural" },
  transform: { center: [121.5, 25.03], sizeM: 2000, rotationDeg: 0 },
  route: routeFromResponse(SAMPLE_MATCH_RESPONSE),
};

let factory: IDBFactory;
let db: DraftDb;
let views: DraftsView[];
let changed: () => void;
let notify: ReturnType<typeof vi.fn<() => void>>;

beforeEach(async () => {
  factory = new IDBFactory();
  db = await openDraftDb(factory);
  views = [];
  notify = vi.fn();
});

const start = () =>
  createDraftsController({
    openDb: async () => db,
    now: () => T0 + 8 * DAY,
    subscribe: (cb) => {
      changed = cb;
      return () => {};
    },
    notify,
    onChange: (v) => views.push(v),
  });
const settle = () => new Promise((resolve) => setTimeout(resolve, 20));

describe("createDraftsController", () => {
  it("載入時先清掉過期的未打開草稿，再讀；打開過的留著", async () => {
    const stale = await db.create(INPUT, T0);
    const opened = await db.create(INPUT, T0);
    await db.markOpened(opened.draft.id, T0);
    const fresh = await db.create(INPUT, T0 + 5 * DAY);
    start();
    await settle();
    const ids = views.at(-1)!.drafts.map((d) => d.id);
    expect(ids).not.toContain(stale.draft.id);
    expect(ids.sort()).toEqual([opened.draft.id, fresh.draft.id].sort());
    expect(views.at(-1)).toMatchObject({ loaded: true, unavailable: false, overLimit: false });
    expect(8 * DAY).toBeGreaterThan(UNOPENED_TTL_MS);
  });

  it("只在載入時 sweep 一次；之後重新讀取不再刪", async () => {
    const controller = start();
    await settle();
    const late = await db.create(INPUT, T0); // 比 now 舊 8 天，但載入後才出現
    changed();
    await settle();
    expect(views.at(-1)!.drafts.map((d) => d.id)).toContain(late.draft.id);
    controller.dispose();
  });

  it("其他分頁通知草稿變了：重新讀取", async () => {
    start();
    await settle();
    expect(views.at(-1)!.drafts).toHaveLength(0);
    await db.create(INPUT, T0 + 8 * DAY);
    changed();
    await settle();
    expect(views.at(-1)!.drafts).toHaveLength(1);
  });

  it("壞掉的草稿跳過、不刪", async () => {
    await db.create(INPUT, T0 + 8 * DAY);
    await db.put({ ...(await db.list())[0], id: "broken", v: 2 } as never);
    start();
    await settle();
    expect(views.at(-1)!.drafts.map((d) => d.id)).not.toContain("broken");
    expect((await db.get("broken"))).toBeNull(); // get 也驗證；原始紀錄還在
    const raw = await new Promise<unknown[]>((resolve) => {
      const open = factory.open("artroute");
      open.onsuccess = () => {
        const req = open.result.transaction("drafts").objectStore("drafts").getAll();
        req.onsuccess = () => resolve(req.result);
      };
    });
    expect(raw).toHaveLength(2);
  });

  it("超過上限：回報 overLimit", async () => {
    for (let i = 0; i < DRAFT_LIMIT + 1; i++) {
      const { draft } = await db.create(INPUT, T0 + 8 * DAY + i);
      await db.markOpened(draft.id, T0);
    }
    start();
    await settle();
    expect(views.at(-1)).toMatchObject({ overLimit: true });
    expect(views.at(-1)!.drafts).toHaveLength(DRAFT_LIMIT + 1);
  });

  it("remove：刪掉那一份、通知其他分頁、列表更新", async () => {
    const { draft } = await db.create(INPUT, T0 + 8 * DAY);
    const controller = start();
    await settle();
    await controller.remove(draft.id);
    expect(notify).toHaveBeenCalledTimes(1);
    expect(views.at(-1)!.drafts).toEqual([]);
    expect(await db.get(draft.id)).toBeNull();
  });

  it("markOpened：之後 sweep 不會刪它；通知其他分頁", async () => {
    const { draft } = await db.create(INPUT, T0);
    const controller = start(); // 這份已過期，載入時被 sweep 掉
    await settle();
    expect(views.at(-1)!.drafts).toEqual([]);
    const other = await db.create(INPUT, T0 + 8 * DAY);
    await controller.markOpened(other.draft.id);
    expect(notify).toHaveBeenCalled();
    expect((await db.get(other.draft.id))?.openedAt).not.toBeNull();
    expect(draft.id).not.toBe(other.draft.id);
  });

  it("資料庫打不開：unavailable，列表是空的，不丟例外", async () => {
    createDraftsController({
      openDb: async () => {
        throw new Error("indexedDB 不可用");
      },
      now: () => T0,
      subscribe: () => () => {},
      notify,
      onChange: (v) => views.push(v),
    });
    await settle();
    expect(views.at(-1)).toEqual({ drafts: [], loaded: true, unavailable: true, overLimit: false, actionError: null });
  });

  it("dispose 之後不再通知", async () => {
    const controller = start();
    await settle();
    const seen = views.length;
    controller.dispose();
    await db.create(INPUT, T0 + 8 * DAY);
    await controller.refresh();
    expect(views).toHaveLength(seen);
  });

  describe("IndexedDB 出錯（刪除、標記打開過）不丟未處理的 rejection，改成留下說明", () => {
    const failing = (method: "remove" | "markOpened"): DraftDb => ({
      ...db,
      [method]: async () => {
        throw new DOMException("交易中止", "AbortError");
      },
    });
    const startWith = (handle: DraftDb) =>
      createDraftsController({
        openDb: async () => handle,
        now: () => T0 + 8 * DAY,
        subscribe: () => () => {},
        notify,
        onChange: (v) => views.push(v),
      });

    it("remove 失敗：resolve、通知其他分頁、列表照舊、actionError 有說明", async () => {
      const { draft } = await db.create(INPUT, T0 + 8 * DAY);
      const controller = startWith(failing("remove"));
      await settle();
      const spy = vi.spyOn(console, "error").mockImplementation(() => {});
      await expect(controller.remove(draft.id)).resolves.toBeUndefined();
      expect(spy).toHaveBeenCalled();
      spy.mockRestore();
      expect(views.at(-1)!.drafts.map((d) => d.id)).toEqual([draft.id]);
      expect(views.at(-1)!.actionError).toContain("刪除草稿失敗");
    });

    it("markOpened 失敗：同樣不丟例外，說明提到可能被自動刪除", async () => {
      const { draft } = await db.create(INPUT, T0 + 8 * DAY);
      const controller = startWith(failing("markOpened"));
      await settle();
      const spy = vi.spyOn(console, "error").mockImplementation(() => {});
      await expect(controller.markOpened(draft.id)).resolves.toBeUndefined();
      spy.mockRestore();
      expect(views.at(-1)!.actionError).toContain("自動刪除");
    });

    it("下一次成功的操作清掉說明", async () => {
      const { draft } = await db.create(INPUT, T0 + 8 * DAY);
      let broken = true;
      const flaky: DraftDb = {
        ...db,
        remove: async (id) => {
          if (broken) throw new Error("x");
          return db.remove(id);
        },
      };
      const controller = startWith(flaky);
      await settle();
      const spy = vi.spyOn(console, "error").mockImplementation(() => {});
      await controller.remove(draft.id);
      spy.mockRestore();
      expect(views.at(-1)!.actionError).not.toBeNull();
      broken = false;
      await controller.remove(draft.id);
      expect(views.at(-1)!.actionError).toBeNull();
      expect(views.at(-1)!.drafts).toEqual([]);
    });
  });
});
