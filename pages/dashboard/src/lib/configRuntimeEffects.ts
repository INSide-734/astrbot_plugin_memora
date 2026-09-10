import type {
  ConfigApplyData,
  ConfigRuntimeEffects,
} from "@/types/config";

const GRAPH_REBUILD_PATHS = new Set([
  "graph_memory.temporal_edges_enabled",
  "graph_memory.causal_edges_enabled",
]);

export interface ConfigChangedPathEffects {
  restartRequired: boolean;
  rebuildRequired: boolean;
}

/** 把后端应用结果转换为需要持续展示的运行时提示。 */
export function configRuntimeEffects(
  applyData: ConfigApplyData,
): ConfigRuntimeEffects {
  return {
    manualRestartRequired:
      applyData.restart_required && !applyData.reload_scheduled,
    rebuildRequired: applyData.rebuild_required,
  };
}

/**
 * 合并连续配置保存的提示，保留尚未由显式重建动作满足的图重建要求。
 *
 * @param previous 当前页面已经展示的运行时要求。
 * @param current 最新配置保存返回的运行时要求。
 * @returns 可安全替换页面状态的合并结果。
 */
export function mergeConfigRuntimeEffects(
  previous: ConfigRuntimeEffects | null,
  current: ConfigRuntimeEffects,
): ConfigRuntimeEffects {
  return {
    manualRestartRequired: current.manualRestartRequired,
    rebuildRequired:
      (previous?.rebuildRequired ?? false) || current.rebuildRequired,
  };
}

/** 为 Dashboard 模拟服务复现后端的重启和图重建分类。 */
export function configEffectsForChangedPaths(
  changedPaths: string[],
): ConfigChangedPathEffects {
  return {
    restartRequired: changedPaths.length > 0,
    rebuildRequired: changedPaths.some((path) =>
      GRAPH_REBUILD_PATHS.has(path),
    ),
  };
}
