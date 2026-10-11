"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { DRAFT_LIMIT, notifyDraftsChanged, onDraftsChanged, openDraftDb, type Draft, type DraftDb } from "@/lib/storage/drafts";

export type DraftsView = {
  drafts: Draft[];
  /** 第一次讀完（或打不開）之前是 false。 */
  loaded: boolean;
  /** 資料庫打不開（例如瀏覽器不允許 IndexedDB）：列表是空的，但不影響其他功能。 */
  unavailable: boolean;
  /** 草稿數超過上限（全部打開過、新草稿照樣存的結果）：請使用者刪幾份。 */
  overLimit: boolean;
};

export const INITIAL_DRAFTS_VIEW: DraftsView = { drafts: [], loaded: false, unavailable: false, overLimit: false };

export type DraftsController = {
  refresh(): Promise<void>;
  /** 刪除一份（確認由呼叫端做）；通知其他分頁並重新讀取。 */
  remove(id: string): Promise<void>;
  /** 標成「打開過」（之後不會被自動刪除）。 */
  markOpened(id: string): Promise<void>;
  dispose(): void;
};

/**
 * 「我的路線」的資料（useDrafts 的核心，不依賴 React）：載入時先清掉過期的未打開草稿（sweep）再讀；
 * 其他分頁（或這個分頁的存檔）通知草稿變了就重新讀。讀出來的只有驗證過的草稿，壞掉的跳過、不刪（見 DraftDb.list）。
 * 重新讀取排成一列、只套用最新一次，晚到的舊結果不會蓋掉新的。
 */
export function createDraftsController(deps: {
  openDb: () => Promise<DraftDb>;
  now: () => number;
  subscribe: (onChanged: () => void) => () => void;
  notify: () => void;
  onChange: (view: DraftsView) => void;
}): DraftsController {
  let db: Promise<DraftDb> | null = null;
  let requested = 0;
  let applied = 0;
  let disposed = false;
  let swept = false;

  const getDb = () => (db ??= deps.openDb());

  const refresh = async () => {
    const seq = ++requested;
    try {
      const handle = await getDb();
      if (!swept) {
        swept = true;
        await handle.sweep(deps.now());
      }
      const drafts = await handle.list();
      if (disposed || seq < applied) return;
      applied = seq;
      deps.onChange({ drafts, loaded: true, unavailable: false, overLimit: drafts.length > DRAFT_LIMIT });
    } catch {
      if (disposed || seq < applied) return;
      applied = seq;
      db = null;
      deps.onChange({ drafts: [], loaded: true, unavailable: true, overLimit: false });
    }
  };

  const unsubscribe = deps.subscribe(() => void refresh());
  void refresh();

  const change = async (apply: (handle: DraftDb) => Promise<unknown>) => {
    try {
      await apply(await getDb());
    } finally {
      deps.notify();
      await refresh();
    }
  };

  return {
    refresh,
    remove: (id) => change((handle) => handle.remove(id)),
    markOpened: (id) => change((handle) => handle.markOpened(id, deps.now())),
    dispose() {
      disposed = true;
      unsubscribe();
    },
  };
}

export type Drafts = { view: DraftsView; remove: (id: string) => Promise<void>; markOpened: (id: string) => Promise<void> };

/** 草稿列表（IndexedDB）；其他分頁改了就自動重新讀取。 */
export function useDrafts(): Drafts {
  const [view, setView] = useState<DraftsView>(INITIAL_DRAFTS_VIEW);
  const controllerRef = useRef<DraftsController | null>(null);

  useEffect(() => {
    const controller = createDraftsController({
      openDb: () => openDraftDb(),
      now: Date.now,
      subscribe: onDraftsChanged,
      notify: notifyDraftsChanged,
      onChange: setView,
    });
    controllerRef.current = controller;
    return () => {
      controller.dispose();
      controllerRef.current = null;
    };
  }, []);

  return useMemo(
    () => ({
      view,
      remove: async (id) => void (await controllerRef.current?.remove(id)),
      markOpened: async (id) => void (await controllerRef.current?.markOpened(id)),
    }),
    [view],
  );
}
