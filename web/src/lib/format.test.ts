import { describe, expect, it } from "vitest";
import { formatDataVersion, formatDistance } from "@/lib/format";

describe("formatDistance", () => {
  it("不到 1 km 用公尺、四捨五入到整數", () => {
    expect(formatDistance(849.6)).toBe("850 m");
  });

  it("1 km 以上用公里、兩位小數", () => {
    expect(formatDistance(5230.4)).toBe("5.23 km");
    expect(formatDistance(1000)).toBe("1.00 km");
  });
});

describe("formatDataVersion", () => {
  it("build id 轉成日期", () => {
    expect(formatDataVersion("20261010T145928Z")).toBe("2026-10-10");
  });

  it("格式不符時原樣回傳", () => {
    expect(formatDataVersion("dev")).toBe("dev");
  });
});
