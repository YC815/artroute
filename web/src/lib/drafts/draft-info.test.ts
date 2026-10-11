import { describe, expect, it } from "vitest";
import { formatModified, isNewerBuild, isOutdatedRoute, newestBuild, outdatedLabel } from "@/lib/drafts/draft-info";

describe("isOutdatedRoute", () => {
  it("比引擎目前的舊才算舊；相同不算；引擎版本未知（連不上）不算", () => {
    expect(isOutdatedRoute("20261010T000000Z", "20261011T000000Z")).toBe(true);
    expect(isOutdatedRoute("20261011T000000Z", "20261011T000000Z")).toBe(false);
    expect(isOutdatedRoute("20261010T000000Z", null)).toBe(false);
  });

  it("比 /status 已知的版本新的路線（引擎在頁面開著時重建）不算舊", () => {
    expect(isOutdatedRoute("20261101T000000Z", "20261010T000000Z")).toBe(false);
    // e2e smoke 的情境：/status 20260101、/match 回 20261010
    expect(isOutdatedRoute("20261010T145928Z", "20260101T000000Z")).toBe(false);
  });

  it("不是時間戳格式（沒有順序可比）：不同就算舊", () => {
    expect(isOutdatedRoute("dev-1", "dev-2")).toBe(true);
    expect(isOutdatedRoute("dev-1", "20261010T000000Z")).toBe(true);
  });
});

describe("isNewerBuild", () => {
  it("看到的版本比已知的新才要重新讀 /status；舊的、相同的、未知的都不用", () => {
    expect(isNewerBuild("20261101T000000Z", "20261010T000000Z")).toBe(true);
    expect(isNewerBuild("20261010T000000Z", "20261101T000000Z")).toBe(false);
    expect(isNewerBuild("20261010T000000Z", "20261010T000000Z")).toBe(false);
    expect(isNewerBuild("20261101T000000Z", null)).toBe(false);
    expect(isNewerBuild(null, "20261010T000000Z")).toBe(false);
    expect(isNewerBuild("dev-2", "dev-1")).toBe(false);
  });
});

describe("newestBuild", () => {
  it("取最新的；沒有或格式不符的略過", () => {
    expect(newestBuild(["20261010T000000Z", undefined, "20261101T000000Z", null, "dev"])).toBe("20261101T000000Z");
    expect(newestBuild([null, undefined])).toBeNull();
  });
});

describe("outdatedLabel", () => {
  it("月日不補零", () => {
    expect(outdatedLabel("20261005T145928Z")).toBe("這是 10 月 5 日路網上的路線");
    expect(outdatedLabel("20260301T000000Z")).toBe("這是 3 月 1 日路網上的路線");
  });
  it("格式不符：不帶日期", () => {
    expect(outdatedLabel("dev-build")).toBe("這是舊路網上的路線");
  });
});

describe("formatModified", () => {
  it("月/日 時:分，分鐘補零", () => {
    expect(formatModified(new Date(2026, 9, 11, 20, 5).getTime())).toBe("10/11 20:05");
    expect(formatModified(new Date(2026, 0, 2, 3, 40).getTime())).toBe("1/2 03:40");
  });
});
