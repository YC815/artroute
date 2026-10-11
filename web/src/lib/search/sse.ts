// 遞增式的 SSE（text/event-stream）解析器，照 WHATWG HTML 規格的「解析事件串流」：
// 換行可以是 \r\n、\r 或 \n；冒號開頭是註解（引擎每 15 秒送一次 `: ping`）；
// 多行 data 以換行串起；沒有 event 欄位的事件名稱是 message；id、retry 與不認得的欄位忽略。
// 位元組可以在任何地方被切開（多位元組 UTF-8 字元中間、\r\n 中間、行中間），結果都相同。

/** 一個完整的事件：event 名稱與 data（多行時以 \n 串起）。 */
export type SseMessage = { event: string; data: string };

export type SseParser = {
  /** 餵一塊位元組；其中完整的事件會立刻送給 onMessage。 */
  push: (chunk: Uint8Array) => void;
  /** 串流結束：沒有以空行結尾的最後一個事件依規格丟棄。 */
  end: () => void;
};

export function createSseParser(onMessage: (message: SseMessage) => void): SseParser {
  // 每個 parser 自己一份：帶 g 旗標的 regex 有 lastIndex 狀態
  const LINE_END = /\r\n|\r|\n/g;
  // TextDecoder 預設會去掉開頭的 BOM；stream: true 讓切開的多位元組字元留到下一塊
  const decoder = new TextDecoder();
  let partial = ""; // 還沒遇到換行的那一行
  let skipLf = false; // 上一塊以 \r 結尾：下一塊開頭的 \n 是同一個換行
  let eventName = "";
  let data = "";
  let hasData = false;

  function dispatch() {
    if (hasData) onMessage({ event: eventName || "message", data: data.endsWith("\n") ? data.slice(0, -1) : data });
    eventName = "";
    data = "";
    hasData = false;
  }

  function handleLine(line: string) {
    if (line === "") return dispatch();
    if (line.startsWith(":")) return;
    const colon = line.indexOf(":");
    const field = colon < 0 ? line : line.slice(0, colon);
    let value = colon < 0 ? "" : line.slice(colon + 1);
    if (value.startsWith(" ")) value = value.slice(1);
    if (field === "event") {
      eventName = value;
    } else if (field === "data") {
      data += `${value}\n`;
      hasData = true;
    }
  }

  function feed(text: string) {
    if (text === "") return;
    let pos = 0;
    if (skipLf && text.startsWith("\n")) pos = 1;
    skipLf = false;
    LINE_END.lastIndex = pos;
    for (let match = LINE_END.exec(text); match; match = LINE_END.exec(text)) {
      handleLine(partial + text.slice(pos, match.index));
      partial = "";
      pos = match.index + match[0].length;
      // 單獨的 \r 剛好在這塊的最後：可能是被切開的 \r\n
      if (match[0] === "\r" && pos === text.length) skipLf = true;
    }
    partial += text.slice(pos);
  }

  return {
    push: (chunk) => feed(decoder.decode(chunk, { stream: true })),
    end: () => {
      feed(decoder.decode());
      partial = "";
      eventName = "";
      data = "";
      hasData = false;
    },
  };
}
