import { describe, expect, it } from "vitest";

import {
  configEffectsForChangedPaths,
  configRuntimeEffects,
  mergeConfigRuntimeEffects,
} from "./configRuntimeEffects";

describe("配置运行时生效提示", () => {
  it("自动重载未安排时保留手动重启要求", () => {
    expect(
      configRuntimeEffects({
        revision: "rev-2",
        changed_paths: ["hybrid_scoring.score_alpha"],
        reload_scheduled: false,
        restart_required: true,
        rebuild_required: false,
        instance_id: "instance-1",
      }),
    ).toEqual({ manualRestartRequired: true, rebuildRequired: false });
  });

  it("自动重载能满足重启要求但不能代替图重建", () => {
    expect(
      configRuntimeEffects({
        revision: "rev-2",
        changed_paths: ["graph_memory.temporal_edges_enabled"],
        reload_scheduled: true,
        restart_required: true,
        rebuild_required: true,
        instance_id: "instance-1",
      }),
    ).toEqual({ manualRestartRequired: false, rebuildRequired: true });
  });

  it("模拟服务与后端使用相同的图边重建分类", () => {
    expect(
      configEffectsForChangedPaths([
        "graph_memory.causal_edges_enabled",
        "recall_engine.top_k",
      ]),
    ).toEqual({ restartRequired: true, rebuildRequired: true });
    expect(configEffectsForChangedPaths([])).toEqual({
      restartRequired: false,
      rebuildRequired: false,
    });
  });

  it("后续普通保存不能清除尚未执行的图重建要求", () => {
    expect(
      mergeConfigRuntimeEffects(
        { manualRestartRequired: false, rebuildRequired: true },
        { manualRestartRequired: true, rebuildRequired: false },
      ),
    ).toEqual({ manualRestartRequired: true, rebuildRequired: true });
  });
});
