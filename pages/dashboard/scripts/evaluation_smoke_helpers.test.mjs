import { describe, expect, it, vi } from "vitest";

import { assertEvaluationVariantGrid } from "./evaluation_smoke_helpers.mjs";

/** 构造只暴露变体网格定位器的最小 Playwright 页面替身。 */
function createVariantGridPage(layout) {
  const waitFor = vi.fn().mockResolvedValue(undefined);
  const evaluate = vi.fn().mockResolvedValue(layout);
  return {
    locator: vi.fn().mockReturnValue({ waitFor, evaluate }),
    waitFor,
    evaluate,
  };
}

describe("evaluation smoke helpers", () => {
  it("接受列数匹配且没有溢出的变体网格", async () => {
    const page = createVariantGridPage({
      columnCount: 2,
      gridOverflow: 0,
      cardCount: 7,
      cardsInsideGrid: true,
    });

    await expect(assertEvaluationVariantGrid(page, 2)).resolves.toBeUndefined();
    expect(page.locator).toHaveBeenCalledWith("[data-variant-grid]");
    expect(page.waitFor).toHaveBeenCalledWith({ state: "visible", timeout: 5_000 });
  });

  it.each([
    ["列数错误", { columnCount: 1, gridOverflow: 0, cardCount: 7, cardsInsideGrid: true }],
    ["横向溢出", { columnCount: 2, gridOverflow: 2, cardCount: 7, cardsInsideGrid: true }],
    ["没有卡片", { columnCount: 2, gridOverflow: 0, cardCount: 0, cardsInsideGrid: true }],
    ["卡片越界", { columnCount: 2, gridOverflow: 0, cardCount: 7, cardsInsideGrid: false }],
  ])("拒绝%s的变体网格", async (_name, layout) => {
    const page = createVariantGridPage(layout);

    await expect(assertEvaluationVariantGrid(page, 2)).rejects.toThrow(
      "评测变体卡片布局无效",
    );
  });
});
