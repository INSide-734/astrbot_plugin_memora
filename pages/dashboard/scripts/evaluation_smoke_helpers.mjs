/** 打开 Evaluation 历史报告，并等待安全变体状态表可见。 */
export async function openEvaluationReportForSmoke(page) {
  await page.getByText("eval-smoke-latest", { exact: true }).click();
  const heading = page.getByText("变体执行状态", { exact: true });
  await heading.waitFor({
    state: "visible",
    timeout: 5_000,
  });
  await page.getByText("chain_graph_expansion_enabled=false", { exact: true }).waitFor({
    state: "visible",
    timeout: 5_000,
  });
  await heading.evaluate((element) => {
    element.scrollIntoView({ block: "center", inline: "nearest" });
  });
}

/** 验证变体卡片列数符合视口预期，且卡片没有撑出选择区域。 */
export async function assertEvaluationVariantGrid(page, expectedColumnCount) {
  const grid = page.locator("[data-variant-grid]");
  await grid.waitFor({ state: "visible", timeout: 5_000 });
  const layout = await grid.evaluate((element) => {
    const gridRect = element.getBoundingClientRect();
    const cards = [...element.querySelectorAll("[data-variant-card]")];
    return {
      columnCount: window.getComputedStyle(element).gridTemplateColumns.split(" ").length,
      gridOverflow: element.scrollWidth - element.clientWidth,
      cardCount: cards.length,
      cardsInsideGrid: cards.every((card) => {
        const cardRect = card.getBoundingClientRect();
        return cardRect.left >= gridRect.left - 1 && cardRect.right <= gridRect.right + 1;
      }),
    };
  });
  if (
    layout.columnCount !== expectedColumnCount
    || layout.gridOverflow > 1
    || layout.cardCount === 0
    || !layout.cardsInsideGrid
  ) {
    throw new Error(`评测变体卡片布局无效: ${JSON.stringify({ expectedColumnCount, ...layout })}`);
  }
}
