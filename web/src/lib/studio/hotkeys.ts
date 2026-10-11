/** 「隱藏原圖」的快捷鍵（大小寫都可，Shift＋H 也算）。 */
export const TOGGLE_TARGET_KEY = "h";

/** 鍵盤事件裡判斷要用到的欄位（KeyboardEvent 本身就符合；測試可以直接給物件）。 */
export type HotkeyEvent = {
  key: string;
  ctrlKey: boolean;
  metaKey: boolean;
  altKey: boolean;
  repeat?: boolean;
  isComposing?: boolean;
  target: EventTarget | null;
};

const TEXT_ENTRY_TAGS = new Set(["INPUT", "TEXTAREA", "SELECT"]);
/**
 * 自己會用字母鍵的 ARIA 元件：文字輸入，以及有「打字跳到選項」的下拉選單、清單與選單
 * （例如 Radix Select 的觸發按鈕是 role="combobox"，打開後焦點在 role="option" 上）。
 */
const TEXT_ENTRY_ROLES = new Set([
  "textbox",
  "searchbox",
  "combobox",
  "listbox",
  "option",
  "menu",
  "menuitem",
  "menuitemcheckbox",
  "menuitemradio",
]);

type TargetLike = { tagName?: unknown; isContentEditable?: unknown; getAttribute?: (name: string) => string | null };

/** 焦點在可以打字或會用字母鍵的地方（input、textarea、select、contenteditable、上面那些 role）：快捷鍵不能攔。 */
function isTextEntry(target: EventTarget | null): boolean {
  // vitest 跑在 node，沒有 HTMLElement：用欄位判斷，不用 instanceof
  const el = target as TargetLike | null;
  if (!el) return false;
  if (el.isContentEditable === true) return true;
  if (typeof el.tagName === "string" && TEXT_ENTRY_TAGS.has(el.tagName.toUpperCase())) return true;
  const role = typeof el.getAttribute === "function" ? el.getAttribute("role") : null;
  return role !== null && TEXT_ENTRY_ROLES.has(role);
}

/**
 * 這個按鍵要不要切換「隱藏原圖」：只認 H，帶 Ctrl／Meta／Alt 的不攔（留給瀏覽器與系統的快捷鍵），
 * 按住不放的重複事件與輸入法組字中不算，焦點在可以打字的地方時也不攔。
 */
export function isToggleTargetKey(e: HotkeyEvent): boolean {
  if (e.ctrlKey || e.metaKey || e.altKey || e.repeat || e.isComposing) return false;
  if (e.key.toLowerCase() !== TOGGLE_TARGET_KEY) return false;
  return !isTextEntry(e.target);
}
