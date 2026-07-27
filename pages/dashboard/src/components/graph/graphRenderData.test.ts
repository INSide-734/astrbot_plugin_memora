import { describe, expect, it } from "vitest";

import { buildGraphRenderData } from "./graphRenderData";

describe("buildGraphRenderData", () => {
  it("保留完整图数据，并为时间筛选生成无需重排的可见性映射", () => {
    const now = 1_000_000;
    const result = buildGraphRenderData(
      [
        { id: "a", label: "甲", type: "topic" },
        { id: "b", label: "乙", type: "fact" },
        { id: "c", label: "丙", type: "person" },
      ],
      [
        { id: 10, source: "a", target: "b", type: "related", timestamp: now - 2 * 3600 },
        { id: 11, source: "b", target: "c", type: "related", timestamp: now - 48 * 3600 },
        { id: 12, source: "c", target: "missing", type: "related", timestamp: now - 2 * 3600 },
      ],
      { start: 1, end: 24 },
      now,
      new Set(["caused_by"]),
    );

    expect(result.data.nodes.map((node) => node.id)).toEqual(["a", "b", "c"]);
    expect(result.data.edges.map((edge) => edge.id)).toEqual(["e-a-b-10", "e-b-c-11"]);
    expect(result.visibleData.nodes.map((node) => node.id)).toEqual(["a", "b"]);
    expect(result.visibleData.edges.map((edge) => edge.id)).toEqual(["e-a-b-10"]);
    expect(result.visibility).toEqual({
      a: "visible",
      b: "visible",
      c: "hidden",
      "e-a-b-10": "visible",
      "e-b-c-11": "hidden",
    });
    expect(result.hasHiddenElements).toBe(true);
    expect(result.invalidEdges).toEqual([{
      source: "c",
      target: "missing",
      sourceExists: true,
      targetExists: false,
    }]);
  });

  it("未启用时间筛选时保持全部有效元素可见", () => {
    const result = buildGraphRenderData(
      [
        { id: "a", label: "甲", type: "topic" },
        { id: "b", label: "乙", type: "fact" },
      ],
      [{ source: "a", target: "b", type: "caused_by" }],
      { start: 0, end: 720 },
      1_000_000,
      new Set(["caused_by"]),
    );

    expect(result.visibleData).toEqual(result.data);
    expect(result.data.edges[0]?.data.label).toBe("caused_by");
    expect(result.hasHiddenElements).toBe(false);
  });
});
