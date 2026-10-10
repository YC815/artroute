import { describe, expect, it } from "vitest";
import { buildGpx, gpxFileName } from "@/lib/export/gpx";

/** 完整 XML 解析器不在依賴裡，這裡只檢查：去掉標籤與宣告後，文字裡沒有未跳脫的 & 或 <。 */
function textOutsideTags(xml: string): string {
  return xml.replace(/<\?[^>]*\?>/g, "").replace(/<\/?[A-Za-z][^<>]*>/g, "");
}

function hasRawMarkup(text: string): boolean {
  return /[<]/.test(text) || /&(?!(amp|lt|gt|quot|apos);)/.test(text);
}

describe("buildGpx", () => {
  it("輸出 GPX 1.1 軌跡，座標取 6 位小數，lat/lon 順序正確", () => {
    const gpx = buildGpx(
      [
        [121.5, 25.03],
        [121.5012345678, 25.0312345678],
      ],
      "HI",
    );
    expect(gpx).toBe(
      [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<gpx version="1.1" creator="artroute" xmlns="http://www.topografix.com/GPX/1/1">',
        "  <metadata><name>HI</name></metadata>",
        "  <trk>",
        "    <name>HI</name>",
        "    <trkseg>",
        '      <trkpt lat="25.030000" lon="121.500000"/>',
        '      <trkpt lat="25.031235" lon="121.501235"/>',
        "    </trkseg>",
        "  </trk>",
        "</gpx>",
        "",
      ].join("\n"),
    );
  });

  it("名稱做 XML 跳脫", () => {
    expect(buildGpx([], "A & B <3")).toContain("<name>A &amp; B &lt;3</name>");
  });

  it("含 & 與 < 的名稱輸出的 XML 文字沒有未跳脫的字元", () => {
    const gpx = buildGpx([[121.5, 25.03]], "A & B <3 'x' \"y\"");
    expect(hasRawMarkup(textOutsideTags(gpx))).toBe(false);
    expect(gpx).toContain("<name>A &amp; B &lt;3 &apos;x&apos; &quot;y&quot;</name>");
  });

  it("只輸出傳入的點，順序不變，數量一致", () => {
    const gpx = buildGpx(
      [
        [121.1, 25.1],
        [121.2, 25.2],
        [121.3, 25.3],
      ],
      "route",
    );
    const lons = [...gpx.matchAll(/<trkpt lat="[^"]+" lon="([^"]+)"\/>/g)].map((m) => m[1]);
    expect(lons).toEqual(["121.100000", "121.200000", "121.300000"]);
  });
});

/** XML 1.0 不允許的字元：U+0000–U+0008、U+000B、U+000C、U+000E–U+001F、U+FFFE、U+FFFF（用碼位比對，避免控制字元 regex）。 */
function hasIllegalXmlChar(text: string): boolean {
  return [...text].some((c) => {
    const code = c.codePointAt(0)!;
    const isControl = code < 0x20 && code !== 0x09 && code !== 0x0a && code !== 0x0d;
    return isControl || code === 0xfffe || code === 0xffff;
  });
}

describe("buildGpx 名稱清理", () => {
  it("去掉 XML 1.0 不允許的控制字元（U+0001 不可原樣輸出）", () => {
    const gpx = buildGpx([], "A\u0001B");
    expect(gpx).toContain("<name>AB</name>");
    expect(hasIllegalXmlChar(gpx)).toBe(false);
  });

  it("U+0000、U+0008、U+000B、U+000C、U+000E、U+001F、U+FFFE、U+FFFF 都去掉", () => {
    const illegal = "\u0000\u0008\u000B\u000C\u000E\u001F￾￿";
    const gpx = buildGpx([], `X${illegal}Y`);
    expect(gpx).toContain("<name>XY</name>");
    expect(hasIllegalXmlChar(gpx)).toBe(false);
  });

  it("換行收成一個空白（兩行文字）", () => {
    expect(buildGpx([], "A\nB")).toContain("<name>A B</name>");
  });

  it("所有空白連續出現時收成一個空白，並去掉頭尾", () => {
    expect(buildGpx([], "  A \t\r\n  B  ")).toContain("<name>A B</name>");
  });

  it("清理後是空字串就用 artroute", () => {
    expect(buildGpx([], "\u0001 \n")).toContain("<name>artroute</name>");
    expect(buildGpx([], "")).toContain("<name>artroute</name>");
  });

  it("非 ASCII 名稱原樣輸出，不跳脫", () => {
    expect(buildGpx([], "台北")).toContain("<name>台北</name>");
  });
});

describe("gpxFileName", () => {
  it("轉成小寫英數字與 -", () => {
    expect(gpxFileName("I ♥ TAIPEI")).toBe("artroute-i-taipei.gpx");
  });

  it("沒有英數字時用 route", () => {
    expect(gpxFileName("♥")).toBe("artroute-route.gpx");
  });

  it("空字串用 route", () => {
    expect(gpxFileName("")).toBe("artroute-route.gpx");
  });

  it("含 & 與 < 的名稱仍得到安全的檔名", () => {
    expect(gpxFileName("A & B <3")).toBe("artroute-a-b-3.gpx");
  });

  it("路徑分隔符與特殊字元都不會進到檔名", () => {
    expect(gpxFileName("../../etc/passwd")).toBe("artroute-etc-passwd.gpx");
    expect(gpxFileName('a"b\'c;d:e*f?g\\h')).toBe("artroute-a-b-c-d-e-f-g-h.gpx");
  });
});
