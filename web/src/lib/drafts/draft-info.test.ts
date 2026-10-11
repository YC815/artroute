import { describe, expect, it } from "vitest";
import { formatModified, isOutdatedRoute, outdatedLabel } from "@/lib/drafts/draft-info";

describe("isOutdatedRoute", () => {
  it("版本不同才算舊；引擎版本未知（連不上）不算", () => {
    expect(isOutdatedRoute("20261010T000000Z", "20261011T000000Z")).toBe(true);
    expect(isOutdatedRoute("20261011T000000Z", "20261011T000000Z")).toBe(false);
    expect(isOutdatedRoute("20261010T000000Z", null)).toBe(false);
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
