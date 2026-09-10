import { describe, expect, it } from "vitest";

import { buildGraphRenderData } from "./graphRenderData";

describe("buildGraphRenderData", () => {
  it("转换后端已筛选的数据并报告缺失端点的孤立边", () => {
    const result = buildGraphRenderData(
      [
        { id: "a", label: "甲", type: "topic" },
        { id: "b", label: "乙", type: "fact" },
        { id: "c", label: "丙", type: "person" },
      ],
      [
        { id: 10, source: "a", target: "b", type: "related" },
        { id: 11, source: "b", target: "c", type: "related" },
        { id: 12, source: "c", target: "missing", type: "related" },
      ],
      new Set(["caused_by"]),
    );

    expect(result.data.nodes.map((node) => node.id)).toEqual(["a", "b", "c"]);
    expect(result.data.edges.map((edge) => edge.id)).toEqual(["e-a-b-10", "e-b-c-11"]);
    expect(result.invalidEdges).toEqual([{
      source: "c",
      target: "missing",
      sourceExists: true,
      targetExists: false,
    }]);
  });

  it("只为因果边保留关系标签", () => {
    const result = buildGraphRenderData(
      [
        { id: "a", label: "甲", type: "topic" },
        { id: "b", label: "乙", type: "fact" },
      ],
      [{ source: "a", target: "b", type: "caused_by" }],
      new Set(["caused_by"]),
    );

    expect(result.data.edges[0]?.data.label).toBe("caused_by");
  });
});
