import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import openapiTS, { astToString, COMMENT_HEADER } from "openapi-typescript";
import { describe, expect, it } from "vitest";

const ENGINE_OPENAPI = new URL("../../../../engine/openapi.json", import.meta.url);
const COMMITTED = new URL("./openapi.d.ts", import.meta.url);

describe("openapi.d.ts 與 engine/openapi.json 同步", () => {
  it("重新產生的型別與已 commit 的檔案逐字相同", async () => {
    // 與 package.json 的 gen:engine-types（CLI，不帶選項）同一條路：標頭＋astToString(openapiTS(...))
    const generated = `${COMMENT_HEADER}${astToString(await openapiTS(ENGINE_OPENAPI, { silent: true }))}`;
    const committed = readFileSync(fileURLToPath(COMMITTED), "utf8");
    expect(
      committed,
      "web/src/lib/engine/openapi.d.ts 與 engine/openapi.json 不同步：在 web/ 執行 `pnpm gen:engine-types`，再把結果一併 commit",
    ).toBe(generated);
  });
});
