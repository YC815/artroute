/** 最多能復原幾步；超過就丟掉最舊的。 */
export const EDIT_HISTORY_LIMIT = 50;

export type History<T> = { past: T[]; present: T; future: T[] };

export function startHistory<T>(present: T): History<T> {
  return { past: [], present, future: [] };
}

/** 新的編輯：清掉重做的紀錄。 */
export function pushHistory<T>(h: History<T>, next: T): History<T> {
  return { past: [...h.past, h.present].slice(-EDIT_HISTORY_LIMIT), present: next, future: [] };
}

/** 沒得復原時回傳同一個物件（React 的 state 不變就不會重繪）。 */
export function undo<T>(h: History<T>): History<T> {
  if (h.past.length === 0) return h;
  return { past: h.past.slice(0, -1), present: h.past[h.past.length - 1], future: [h.present, ...h.future] };
}

/** 沒得重做時回傳同一個物件。 */
export function redo<T>(h: History<T>): History<T> {
  if (h.future.length === 0) return h;
  return { past: [...h.past, h.present], present: h.future[0], future: h.future.slice(1) };
}

export function canUndo<T>(h: History<T>): boolean {
  return h.past.length > 0;
}

export function canRedo<T>(h: History<T>): boolean {
  return h.future.length > 0;
}
