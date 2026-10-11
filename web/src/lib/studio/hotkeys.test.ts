import { describe, expect, it } from "vitest";
import { editHotkey, isToggleTargetKey, type HotkeyEvent } from "@/lib/studio/hotkeys";

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

describe("editHotkey", () => {
  const z = (overrides: Partial<HotkeyEvent> = {}) => key({ key: "z", code: "KeyZ", ...overrides });

  it("⌘／Ctrl＋Z 復原，再加 Shift 重做（Shift 時 key 可能是大寫 Z）", () => {
    expect(editHotkey(z({ metaKey: true }))).toBe("undo");
    expect(editHotkey(z({ ctrlKey: true }))).toBe("undo");
    expect(editHotkey(z({ metaKey: true, shiftKey: true }))).toBe("redo");
    expect(editHotkey(z({ ctrlKey: true, shiftKey: true, key: "Z" }))).toBe("redo");
  });

  it("注音等輸入法讓 key 不是拉丁字母時，用實體鍵位 KeyZ 判斷", () => {
    expect(editHotkey(z({ metaKey: true, key: "ㄈ" }))).toBe("undo");
    expect(editHotkey(key({ metaKey: true, key: "ㄈ", code: "KeyX" }))).toBeNull();
  });

  it("Esc 取消；帶 Ctrl／Meta／Alt 的 Esc 不攔", () => {
    expect(editHotkey(key({ key: "Escape" }))).toBe("cancel");
    expect(editHotkey(key({ key: "Escape", shiftKey: true }))).toBe("cancel");
    expect(editHotkey(key({ key: "Escape", metaKey: true }))).toBeNull();
    expect(editHotkey(key({ key: "Escape", altKey: true }))).toBeNull();
  });

  it("沒有 ⌘／Ctrl 的 Z、帶 Alt 的 ⌘Z、其他按鍵都不算", () => {
    expect(editHotkey(z())).toBeNull();
    expect(editHotkey(z({ shiftKey: true, key: "Z" }))).toBeNull();
    expect(editHotkey(z({ metaKey: true, altKey: true }))).toBeNull();
    expect(editHotkey(key({ metaKey: true, key: "y", code: "KeyY" }))).toBeNull();
    expect(editHotkey(key({ key: "h" }))).toBeNull();
  });

  it("按住不放的重複事件、輸入法組字中、已被別人處理（defaultPrevented）的不算", () => {
    expect(editHotkey(z({ metaKey: true, repeat: true }))).toBeNull();
    expect(editHotkey(z({ metaKey: true, isComposing: true }))).toBeNull();
    expect(editHotkey(key({ key: "Escape", defaultPrevented: true }))).toBeNull();
  });

  it("焦點在可以打字的地方時不攔（留給文字框自己的復原與 Esc）", () => {
    const input = { tagName: "INPUT" } as unknown as EventTarget;
    const editable = { tagName: "DIV", isContentEditable: true } as unknown as EventTarget;
    const combobox = { tagName: "BUTTON", getAttribute: (n: string) => (n === "role" ? "combobox" : null) } as unknown as EventTarget;
    for (const target of [input, editable, combobox]) {
      expect(editHotkey(z({ metaKey: true, target }))).toBeNull();
      expect(editHotkey(key({ key: "Escape", target }))).toBeNull();
    }
  });
});
