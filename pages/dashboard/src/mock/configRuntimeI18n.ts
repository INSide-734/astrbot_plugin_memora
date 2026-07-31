export const CONFIG_RUNTIME_ZH_MAP: Record<string, string> = {
  "config.restartRequiredTitle": "需要手动重启",
  "config.restartRequiredDescription":
    "配置已保存，但 AstrBot 未安排自动重载。请重启插件以应用本次变更。",
  "config.rebuildRequiredTitle": "需要重建图派生数据",
  "config.rebuildRequiredDescription":
    "图边提取规则已更改。重载插件后，请运行 /memora rebuild-graph 使既有图数据与新规则一致。",
};

export const CONFIG_RUNTIME_EN_MAP: Record<string, string> = {
  "config.restartRequiredTitle": "Manual restart required",
  "config.restartRequiredDescription":
    "The configuration was saved, but AstrBot did not schedule a reload. Restart the plugin to apply these changes.",
  "config.rebuildRequiredTitle": "Graph rebuild required",
  "config.rebuildRequiredDescription":
    "Graph edge extraction changed. After reloading the plugin, run /memora rebuild-graph to rebuild existing derived graph data.",
};

export const CONFIG_RUNTIME_RU_MAP: Record<string, string> = {
  "config.restartRequiredTitle": "Требуется ручной перезапуск",
  "config.restartRequiredDescription":
    "Конфигурация сохранена, но AstrBot не запланировал перезагрузку. Перезапустите плагин, чтобы применить изменения.",
  "config.rebuildRequiredTitle": "Требуется перестроить граф",
  "config.rebuildRequiredDescription":
    "Правила извлечения связей изменились. После перезагрузки плагина выполните /memora rebuild-graph для перестроения производных данных графа.",
};
