import { describe, expect, it } from "vitest";
import { isToggleTargetKey, type HotkeyEvent } from "@/lib/studio/hotkeys";

const button = { tagName: "BUTTON", isContentEditable: false } as unknown as EventTarget;

function key(overrides: Partial<HotkeyEvent> = {}): HotkeyEvent {
  return { key: "h", ctrlKey: false, metaKey: false, altKey: false, target: button, ...overrides };
}

describe("isToggleTargetKey", () => {
  it("H 大小寫都可以切換（焦點在按鈕、body 或沒有目標）", () => {
    expect(isToggleTargetKey(key())).toBe(true);
    expect(isToggleTargetKey(key({ key: "H" }))).toBe(true);
    expect(isToggleTargetKey(key({ target: { tagName: "BODY" } as unknown as EventTarget }))).toBe(true);
    expect(isToggleTargetKey(key({ target: null }))).toBe(true);
  });

  it("其他按鍵不切換", () => {
    for (const k of ["g", "j", "Enter", " ", "ArrowUp", "ㄘ"]) expect(isToggleTargetKey(key({ key: k }))).toBe(false);
  });

  it("帶 Ctrl／Meta／Alt 不攔", () => {
    expect(isToggleTargetKey(key({ ctrlKey: true }))).toBe(false);
    expect(isToggleTargetKey(key({ metaKey: true }))).toBe(false);
    expect(isToggleTargetKey(key({ altKey: true }))).toBe(false);
  });

  it("按住不放的重複事件、輸入法組字中不算", () => {
    expect(isToggleTargetKey(key({ repeat: true }))).toBe(false);
    expect(isToggleTargetKey(key({ isComposing: true }))).toBe(false);
  });

  it("焦點在 input、textarea、select、contenteditable 時不攔", () => {
    for (const tagName of ["INPUT", "TEXTAREA", "SELECT", "input"]) {
      expect(isToggleTargetKey(key({ target: { tagName } as unknown as EventTarget }))).toBe(false);
    }
    const editable = { tagName: "DIV", isContentEditable: true } as unknown as EventTarget;
    expect(isToggleTargetKey(key({ target: editable }))).toBe(false);
    expect(isToggleTargetKey(key({ key: "H", target: editable }))).toBe(false);
  });

  it("會用字母鍵的 ARIA 元件（下拉選單的打字跳選項等）不攔；一般按鈕照常", () => {
    const withRole = (role: string | null) =>
      ({ tagName: role === "combobox" ? "BUTTON" : "DIV", getAttribute: (name: string) => (name === "role" ? role : null) }) as unknown as EventTarget;
    for (const role of ["combobox", "listbox", "option", "menuitem", "textbox"]) {
      expect(isToggleTargetKey(key({ target: withRole(role) }))).toBe(false);
    }
    expect(isToggleTargetKey(key({ target: withRole("tab") }))).toBe(true);
    expect(isToggleTargetKey(key({ target: withRole(null) }))).toBe(true);
  });
});
