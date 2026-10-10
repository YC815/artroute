// 依序執行非同步任務：後送的任務要等前面的全部結束才開始。
// 不跳過任何任務——後面的任務可能依賴前面任務留下的狀態。

/** 建立一條佇列。任務失敗只影響它自己的回傳 promise，不會卡住後面的任務。 */
export function createSerialQueue() {
  let tail: Promise<unknown> = Promise.resolve();
  return function enqueue<T>(task: () => Promise<T>): Promise<T> {
    const run = tail.then(task);
    tail = run.catch(() => undefined);
    return run;
  };
}
