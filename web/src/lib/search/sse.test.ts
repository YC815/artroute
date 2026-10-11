import { describe, expect, it } from "vitest";
import { createSseParser, type SseMessage } from "@/lib/search/sse";

const encoder = new TextEncoder();

/** 把整段文字切成指定位置的 byte 塊後餵給 parser，回傳收到的事件。 */
function parseBytes(bytes: Uint8Array, cuts: number[] = [], end = true): SseMessage[] {
  const messages: SseMessage[] = [];
  const parser = createSseParser((message) => messages.push(message));
  let start = 0;
  for (const cut of [...cuts, bytes.length]) {
    parser.push(bytes.subarray(start, cut));
    start = cut;
  }
  if (end) parser.end();
  return messages;
}

const parse = (text: string, cuts: number[] = []) => parseBytes(encoder.encode(text), cuts);

describe("createSseParser", () => {
  it("event + data，空行結束一個事件", () => {
    expect(parse('event: progress\ndata: {"done":1}\n\n')).toEqual([{ event: "progress", data: '{"done":1}' }]);
  });

  it("沒有 event 欄位時名稱是 message", () => {
    expect(parse("data: hi\n\n")).toEqual([{ event: "message", data: "hi" }]);
  });

  it("連續多個事件；事件名稱不會延續到下一個事件", () => {
    expect(parse("event: a\ndata: 1\n\ndata: 2\n\nevent: c\ndata: 3\n\n")).toEqual([
      { event: "a", data: "1" },
      { event: "message", data: "2" },
      { event: "c", data: "3" },
    ]);
  });

  it("多行 data 以換行串起來（最後一個換行去掉）", () => {
    expect(parse("data: line1\ndata: line2\ndata:\n\n")).toEqual([{ event: "message", data: "line1\nline2\n" }]);
  });

  it("冒號後只去掉一個空白；沒有冒號的行整行是欄位名稱、值為空字串", () => {
    expect(parse("data:  two spaces\n\n")).toEqual([{ event: "message", data: " two spaces" }]);
    expect(parse("data:nospace\n\n")).toEqual([{ event: "message", data: "nospace" }]);
    expect(parse("data\ndata: x\n\n")).toEqual([{ event: "message", data: "\nx" }]);
  });

  it("值裡的冒號保留", () => {
    expect(parse('data: {"a":"b:c"}\n\n')).toEqual([{ event: "message", data: '{"a":"b:c"}' }]);
  });

  it("註解行（: ping）與不認得的欄位（id、retry、其他）都忽略", () => {
    expect(parse(": ping\n\nid: 7\nretry: 1000\nfoo: bar\nevent: x\n: 中途的註解\ndata: 1\n\n: ping\n\n")).toEqual([
      { event: "x", data: "1" },
    ]);
  });

  it("沒有 data 的事件不送出（只有 event 也不送），也不影響下一個事件的名稱", () => {
    expect(parse("event: lonely\n\ndata: next\n\n")).toEqual([{ event: "message", data: "next" }]);
  });

  it("\\r\\n 與單獨的 \\r 都是換行", () => {
    expect(parse("event: a\r\ndata: 1\r\n\r\nevent: b\rdata: 2\r\r")).toEqual([
      { event: "a", data: "1" },
      { event: "b", data: "2" },
    ]);
  });

  it("\\r\\n 被切在兩塊之間不會變成兩個換行（不會提早結束事件）", () => {
    const text = "event: a\r\ndata: 1\r\ndata: 2\r\n\r\n";
    const crAt = text.indexOf("\r\ndata: 2") + 1;
    expect(parse(text, [crAt])).toEqual([{ event: "a", data: "1\n2" }]);
  });

  it("任意位置切塊（含行中間、欄位名稱中間）結果都一樣", () => {
    const text = 'event: progress\ndata: {"stage":"coarse","done":3}\n\n: ping\n\nevent: result\ndata: {"x":1}\r\n\r\n';
    const expected = parse(text);
    expect(expected).toHaveLength(2);
    for (let cut = 0; cut <= text.length; cut++) expect(parse(text, [cut])).toEqual(expected);
    // 每個 byte 一塊
    const bytes = encoder.encode(text);
    expect(parseBytes(bytes, Array.from({ length: bytes.length }, (_, i) => i))).toEqual(expected);
  });

  it("UTF-8 多位元組字元被切開也能正確解碼", () => {
    const text = "event: error\ndata: 搜尋逾時，請縮小範圍 🚲\n\n";
    const bytes = encoder.encode(text);
    const expected = [{ event: "error", data: "搜尋逾時，請縮小範圍 🚲" }];
    for (let cut = 0; cut <= bytes.length; cut++) expect(parseBytes(bytes, [cut])).toEqual(expected);
  });

  it("每收到完整的事件就立刻送出，不等串流結束", () => {
    const messages: SseMessage[] = [];
    const parser = createSseParser((message) => messages.push(message));
    parser.push(encoder.encode("event: a\ndata: 1\n\nevent: b\nda"));
    expect(messages).toEqual([{ event: "a", data: "1" }]);
    parser.push(encoder.encode("ta: 2\n\n"));
    expect(messages).toEqual([
      { event: "a", data: "1" },
      { event: "b", data: "2" },
    ]);
  });

  it("串流結束時沒有空行結尾的事件不送出（照 SSE 規格丟棄）", () => {
    expect(parse("event: a\ndata: 1\n\nevent: b\ndata: 2")).toEqual([{ event: "a", data: "1" }]);
    expect(parse("event: a\ndata: 1\n\nevent: b\ndata: 2\n")).toEqual([{ event: "a", data: "1" }]);
  });

  it("結尾的單獨 \\r 在 end() 時算換行", () => {
    expect(parse("data: 1\r\r")).toEqual([{ event: "message", data: "1" }]);
  });

  it("開頭的 BOM 去掉", () => {
    expect(parseBytes(new Uint8Array([0xef, 0xbb, 0xbf, ...encoder.encode("data: x\n\n")]))).toEqual([
      { event: "message", data: "x" },
    ]);
  });

  it("很長的一行分成很多塊也只解析一次（大型候選事件）", () => {
    const payload = "x".repeat(200_000);
    const bytes = encoder.encode(`event: best\ndata: ${payload}\n\n`);
    const cuts = Array.from({ length: 199 }, (_, i) => (i + 1) * 1000);
    expect(parseBytes(bytes, cuts)).toEqual([{ event: "best", data: payload }]);
  });
});
