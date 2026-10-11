import { describe, expect, it, vi } from "vitest";
import { SAMPLE_MATCH_RESPONSE } from "@/lib/engine/fixtures";
import {
  displayedRoute,
  loadRouteVariant,
  ROUTE_VARIANT_STORAGE_KEY,
  saveRouteVariant,
} from "@/lib/storage/route-variant";

describe("路線版本偏好", () => {
  it("預設快速版，合法值可讀取，壞值回到預設", () => {
    expect(loadRouteVariant({ getItem: () => null, setItem: vi.fn() })).toBe("fast");
    expect(loadRouteVariant({ getItem: () => "fine", setItem: vi.fn() })).toBe("fine");
    expect(loadRouteVariant({ getItem: () => "精緻版", setItem: vi.fn() })).toBe("fast");
  });

  it("讀寫 localStorage 丟錯時不影響使用", () => {
    const store = {
      getItem: () => {
        throw new Error("blocked");
      },
      setItem: () => {
        throw new Error("full");
      },
    };
    expect(loadRouteVariant(store)).toBe("fast");
    expect(() => saveRouteVariant(store, "fine")).not.toThrow();
  });

  it("用固定 key 儲存選擇", () => {
    const setItem = vi.fn();
    saveRouteVariant({ getItem: vi.fn(), setItem }, "fine");
    expect(setItem).toHaveBeenCalledWith(ROUTE_VARIANT_STORAGE_KEY, "fine");
  });
});

describe("目前顯示的路線版本", () => {
  const fine = { ...SAMPLE_MATCH_RESPONSE, score: { ...SAMPLE_MATCH_RESPONSE.score, overall: 91 } };

  it("偏好描形版但尚未算完時顯示快速版，回來後立即顯示描形版", () => {
    expect(displayedRoute("fine", SAMPLE_MATCH_RESPONSE, null)).toEqual({
      variant: "fast",
      response: SAMPLE_MATCH_RESPONSE,
    });
    expect(displayedRoute("fine", SAMPLE_MATCH_RESPONSE, fine)).toEqual({ variant: "fine", response: fine });
  });

  it("描形版失敗（沒有結果）時顯示快速版，偏好不變", () => {
    const shown = displayedRoute("fine", SAMPLE_MATCH_RESPONSE, null);
    expect(shown).toEqual({ variant: "fast", response: SAMPLE_MATCH_RESPONSE });
  });
});
