import { describe, expect, it } from "vitest";
import { createSerialQueue } from "./serial-queue";

/** 手動完成的 promise，用來控制兩個任務誰先結束。 */
function deferred() {
  let resolve!: () => void;
  const promise = new Promise<void>((r) => (resolve = r));
  return { promise, resolve };
}

describe("createSerialQueue", () => {
  it("慢的 A 先送、快的 B 後送：B 要等 A 做完才開始，最後狀態是 B", async () => {
    const enqueue = createSerialQueue();
    const log: string[] = [];
    let image = "";
    const slowA = deferred();

    const a = enqueue(async () => {
      log.push("A 開始");
      await slowA.promise;
      image = "A";
      log.push("A 結束");
    });
    const b = enqueue(async () => {
      log.push("B 開始");
      image = "B";
      log.push("B 結束");
    });

    // A 還沒做完，B 不可以開始
    await Promise.resolve();
    await Promise.resolve();
    expect(log).toEqual(["A 開始"]);

    slowA.resolve();
    await Promise.all([a, b]);
    expect(log).toEqual(["A 開始", "A 結束", "B 開始", "B 結束"]);
    expect(image).toBe("B");
  });

  it("任務的回傳值原樣交還", async () => {
    const enqueue = createSerialQueue();
    expect(await enqueue(async () => 42)).toBe(42);
  });

  it("前一個任務失敗，後面的任務照常執行；失敗的那個仍然把錯誤交給自己的呼叫端", async () => {
    const enqueue = createSerialQueue();
    const failed = enqueue(async () => {
      throw new Error("壞掉");
    });
    const after = enqueue(async () => "還活著");
    await expect(failed).rejects.toThrow("壞掉");
    await expect(after).resolves.toBe("還活著");
  });

  it("同步丟錯的任務也不會卡住佇列", async () => {
    const enqueue = createSerialQueue();
    const failed = enqueue(() => {
      throw new Error("同步壞掉");
    });
    const after = enqueue(async () => "還活著");
    await expect(failed).rejects.toThrow("同步壞掉");
    await expect(after).resolves.toBe("還活著");
  });
});
