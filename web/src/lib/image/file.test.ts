import { describe, expect, it } from "vitest";
import { imageFileProblem } from "@/lib/image/file";

describe("imageFileProblem", () => {
  it("PNG、JPG、WebP 且不超過 5 MB 可以用", () => {
    expect(imageFileProblem({ type: "image/png", size: 5 * 1024 * 1024 })).toBeNull();
    expect(imageFileProblem({ type: "image/jpeg", size: 1 })).toBeNull();
    expect(imageFileProblem({ type: "image/webp", size: 1 })).toBeNull();
  });

  it("其他格式不行", () => {
    expect(imageFileProblem({ type: "image/gif", size: 1 })).toBe("只支援 PNG、JPG、WebP 圖片");
  });

  it("超過 5 MB 不行", () => {
    expect(imageFileProblem({ type: "image/png", size: 5 * 1024 * 1024 + 1 })).toBe("圖片不能超過 5 MB");
  });
});
